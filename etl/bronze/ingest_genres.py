"""Bronze ingestion: TMDB genre list.

Pulls the official genre list from TMDB once per run and writes the raw API
response as JSON to the Bronze layer on S3. One file per ingestion date so the
historical raw data is preserved.

Usage (module-level entry point, never call from other modules):
    python -m etl.bronze.ingest_genres
    python -m etl.bronze.ingest_genres --date 2026-06-21
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import config
from etl import s3_utils
from etl.tmdb_client import TMDBClient

logger = logging.getLogger(__name__)


def ingest_genres(
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
) -> str:
    """Fetch the TMDB genre list and write it to Bronze S3.

    Returns the s3:// URI of the written object.

    Idempotent: re-running with the same `ingestion_date` writes identical data
    to the same key (same source, same date → same output).
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    t0 = time.monotonic()
    logger.info("Starting genre ingestion for date=%s", ingestion_date)

    payload = client.get_genres()
    genres = payload.get("genres", [])
    logger.info("Fetched %d genres from TMDB", len(genres))

    key = s3_utils.build_path("bronze", "genres", ingestion_date, "genres.json")
    uri = s3_utils.write_json(config.S3_BUCKET, key, payload)

    elapsed = time.monotonic() - t0
    logger.info(
        "Genre ingestion complete: %d genres written to %s in %.2fs",
        len(genres),
        uri,
        elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest TMDB genres to Bronze S3.")
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_genres")
    args = _parse_args()
    ingest_genres(ingestion_date=args.date)
