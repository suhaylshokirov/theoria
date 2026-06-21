"""Shared S3 write helpers for every ingestion / transform script.

This is the *single* place that knows:
- how to build an S3 client (credentials + region come from config.py),
- how to serialise data to JSON / Parquet bytes,
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
from typing import Any

import boto3
import pandas as pd

import config

logger = logging.getLogger(__name__)

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
        )
    return _s3_client


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
