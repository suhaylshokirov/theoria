"""Shared S3 helpers for every ingestion / transform script.

This is the *single* place that knows:
- how to build an S3 client (credentials + region come from config.py),
- how to serialise data to JSON / Parquet bytes,
- how to fetch many small objects at once without paying the latency of each
  round-trip in series,
- the project's S3 key layout (the path convention lives here and nowhere else).

Keeping all of this in one module means Bronze/Silver/Gold scripts never
hardcode a bucket, a path shape, or a serialisation format. If the convention
changes, it changes here once.

S3 path convention (key, relative to the bucket):
    <layer>/<entity>/ingestion_date=YYYY-MM-DD/<filename>.{json|parquet}

Usage:
    from etl import s3_utils
    key = s3_utils.build_path("bronze", "genres", "2026-06-21", "genres.json")
    s3_utils.write_json(config.S3_BUCKET, key, payload)
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import boto3
import pandas as pd
from botocore.config import Config

import config

logger = logging.getLogger(__name__)

# How many object reads to have in flight at once in read_json_objects(). The
# Silver transforms each fetch ~1,200 small JSON files; run serially against a
# bucket in another region than the runner, that round-trip latency dominates
# the whole nightly job. 32 parallel reads collapse ~15 min of waiting to ~1.
DEFAULT_READ_WORKERS = 32

# connect/read timeouts so one stalled socket fails fast and retries instead of
# hanging until the job's own timeout kills it (seen once in CI: a dropped S3
# transfer blocked read() for 4.5 min). max_pool_connections must exceed
# DEFAULT_READ_WORKERS or threads queue on the connection pool and the
# parallelism is lost.
_CLIENT_CONFIG = Config(
    connect_timeout=10,
    read_timeout=30,
    retries={"max_attempts": 4, "mode": "standard"},
    max_pool_connections=DEFAULT_READ_WORKERS * 2,
)

# Module-level client, created lazily and reused (connection pooling, fewer
# credential lookups). Never built at import time so importing this module
# stays cheap and side-effect free.
_s3_client = None


def get_s3_client():
    """Return a shared boto3 S3 client, building it once on first use.

    Credentials and region are read from config.py, never from os.environ or
    hardcoded values here.
    """
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_REGION,
            config=_CLIENT_CONFIG,
        )
    return _s3_client


def read_json_objects(
    bucket: str,
    keys: Sequence[str],
    *,
    max_workers: int = DEFAULT_READ_WORKERS,
) -> list[tuple[str, Any, Exception | None]]:
    """Download and JSON-parse many S3 objects concurrently.

    Returns one ``(key, parsed_or_None, error_or_None)`` tuple per input key, in
    the **same order as `keys`** — the fetch order is nondeterministic but the
    result order is not, so two runs over the same partition line up row for row.

    A failure to fetch or parse one object is captured in that object's tuple,
    never raised: one unreadable file must not sink a batch of 1,200. The caller
    decides what a failure means (count it, log it, abort if they all failed).

    boto3 clients are thread-safe for API calls, so every worker shares the one
    pooled client from ``get_s3_client()``.
    """
    keys = list(keys)
    if not keys:
        return []

    client = get_s3_client()
    results: list[tuple[str, Any, Exception | None]] = [
        (key, None, None) for key in keys
    ]

    def _fetch(key: str) -> Any:
        response = client.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read())

    workers = min(max_workers, len(keys))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_index = {
            pool.submit(_fetch, key): i for i, key in enumerate(keys)
        }
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            try:
                results[i] = (keys[i], future.result(), None)
            except Exception as exc:  # noqa: BLE001 — reported per key, not raised
                results[i] = (keys[i], None, exc)

    return results


def build_path(
    layer: str,
    entity: str,
    ingestion_date: str | dt.date,
    filename: str,
) -> str:
    """Build an S3 object key following the one true path convention.

    `<layer>/<entity>/ingestion_date=YYYY-MM-DD/<filename>`

    `ingestion_date` accepts a date (formatted as YYYY-MM-DD) or a string
    (used as-is). Defining the layout in exactly one function means callers
    never assemble these paths by hand.
    """
    if isinstance(ingestion_date, dt.date):
        ingestion_date = ingestion_date.isoformat()
    return f"{layer}/{entity}/ingestion_date={ingestion_date}/{filename}"


def write_json(bucket: str, key: str, data: Any) -> str:
    """Serialise `data` to pretty UTF-8 JSON and upload it to s3://bucket/key.

    Returns the full s3:// URI written. Raises on failure (errors are never
    swallowed) so callers can log the specific object that failed.
    """
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    get_s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
    )
    uri = f"s3://{bucket}/{key}"
    logger.info("Wrote JSON to %s (%d bytes)", uri, len(body))
    return uri


def write_bytes(bucket: str, key: str, data: bytes) -> str:
    """Upload raw bytes verbatim to s3://bucket/key — no serialisation at all.

    For Bronze sources that are already a complete file as fetched (e.g.
    IMDb's gzipped bulk ratings snapshot), rather than a Python object this
    module has to serialise to JSON or Parquet. The bytes stored are
    byte-identical to what the source server returned, which is what "Bronze
    is immutable, raw" means for a source that isn't a per-entity API call.
    """
    get_s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType="application/gzip",
    )
    uri = f"s3://{bucket}/{key}"
    logger.info("Wrote raw bytes to %s (%d bytes)", uri, len(data))
    return uri


def write_parquet(bucket: str, key: str, df: pd.DataFrame) -> str:
    """Serialise a DataFrame to Parquet in-memory and upload to s3://bucket/key.

    Parquet is written to a bytes buffer (no temp files on disk) and uploaded
    in one PutObject. Returns the full s3:// URI written.
    """
    buffer = io.BytesIO()
    # pyarrow is the engine; index is dropped so it never leaks into the file.
    df.to_parquet(buffer, engine="pyarrow", index=False)
    body = buffer.getvalue()
    get_s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/octet-stream",
    )
    uri = f"s3://{bucket}/{key}"
    logger.info("Wrote Parquet to %s (%d rows, %d bytes)", uri, len(df), len(body))
    return uri
