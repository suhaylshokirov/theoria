"""Bronze ingestion: IMDb's public bulk ratings dataset.

This is the project's **first non-TMDB Bronze source, and its first
bulk-file one**. Every other ingest module writes one JSON file per entity
id, because it calls a per-entity TMDB endpoint. IMDb instead publishes one
daily snapshot covering its entire catalogue
(`https://datasets.imdbws.com/title.ratings.tsv.gz` — tab-separated,
gzip-compressed, refreshed daily, no auth/key/quota). A single snapshot file
*is* the raw response here, so one file per partition is the faithful
Bronze representation, not a shortcut — Bronze stays immutable and
append-only either way.

Uses `requests` directly rather than `TMDBClient`: there is no API key to
inject and no TMDB base URL involved, so the client would add nothing but
the wrong abstraction. The retry posture (429 + 5xx, exponential backoff,
honouring Retry-After) mirrors `tmdb_client.py`'s regardless.

The gzip bytes are written to Bronze **verbatim** via `s3_utils.write_bytes`
— never decompressed or re-encoded here — so the stored artefact is
byte-identical to what IMDb served.

S3 layout:
    bronze/imdb_ratings/ingestion_date=YYYY-MM-DD/title.ratings.tsv.gz

Idempotent: re-running for the same date re-downloads and overwrites the
same key. IMDb's snapshot changes daily, so a same-day re-run may not be
byte-identical to an earlier run that day — that is a property of the
upstream source, not a bug in this module.

Usage:
    python -m etl.bronze.ingest_imdb_ratings
    python -m etl.bronze.ingest_imdb_ratings --date 2026-06-22
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import requests

import config
from etl import s3_utils

logger = logging.getLogger(__name__)

# HTTP status codes worth retrying: rate limiting + transient server errors.
# Same set tmdb_client.py uses.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _fetch_with_retry(
    url: str,
    *,
    max_retries: int = 3,
    backoff_factor: float = 0.5,
    timeout: float = 30.0,
) -> bytes:
    """GET `url` and return its raw response body, retrying transient failures.

    Mirrors TMDBClient.get()'s retry posture (429 + 5xx, honouring
    Retry-After, exponential backoff otherwise) without any of its TMDB
    specifics — there is no API key and no JSON body here, just bytes.
    Raises RuntimeError on persistent failure; errors are never swallowed.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries:
                logger.warning(
                    "GET %s failed (attempt %d/%d): %s", url, attempt + 1, max_retries + 1, exc
                )
                time.sleep(backoff_factor * (2 ** attempt))
                continue
            logger.error("GET %s failed after %d attempts: %s", url, attempt + 1, exc)
            raise RuntimeError(f"Request to {url} failed: {exc}") from exc

        if response.status_code == 200:
            return response.content

        if response.status_code in _RETRYABLE_STATUS and attempt < max_retries:
            logger.warning(
                "GET %s -> %d (retryable), attempt %d/%d",
                url, response.status_code, attempt + 1, max_retries + 1,
            )
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(int(retry_after))
            else:
                time.sleep(backoff_factor * (2 ** attempt))
            continue

        logger.error("GET %s -> %d (giving up)", url, response.status_code)
        raise RuntimeError(f"GET {url} failed with status {response.status_code}")

    # Loop only exits via return/raise above; this guards the type checker.
    raise RuntimeError(f"Request to {url} failed: {last_exc}")


def ingest_imdb_ratings(ingestion_date: dt.date | None = None) -> str:
    """Download IMDb's public ratings snapshot and write it verbatim to Bronze.

    Returns the s3:// URI written.

    Idempotent: re-running with the same ingestion_date re-fetches and
    overwrites the same key.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()

    t0 = time.monotonic()
    logger.info(
        "Starting IMDb ratings ingestion: date=%s, url=%s",
        ingestion_date, config.IMDB_RATINGS_URL,
    )

    data = _fetch_with_retry(config.IMDB_RATINGS_URL)

    key = s3_utils.build_path(
        "bronze", "imdb_ratings", ingestion_date, "title.ratings.tsv.gz"
    )
    uri = s3_utils.write_bytes(config.S3_BUCKET, key, data)

    elapsed = time.monotonic() - t0
    logger.info(
        "IMDb ratings ingestion complete: %d bytes written to %s in %.2fs",
        len(data), uri, elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest IMDb's public bulk ratings dataset to Bronze S3."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_imdb_ratings")
    args = _parse_args()
    ingest_imdb_ratings(ingestion_date=args.date)
