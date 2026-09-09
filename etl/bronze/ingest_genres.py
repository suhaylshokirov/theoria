"""Bronze ingestion: TMDB genre list.

Pulls the official genre list from TMDB once per run and writes the raw API
response as JSON to the Bronze layer on S3. One file per ingestion date so the
historical raw data is preserved.

`with_tv=True` additionally fetches `genre/tv/list` and writes it beside the
movie list as `genres_tv.json` — a second one-call fetch, off by default so
the movie pipeline's call volume is unchanged until TV is turned on for real
(Task 85). `transform_genres` merges the two into one `dim_genre` (Task 78).

Usage (module-level entry point, never call from other modules):
    python -m etl.bronze.ingest_genres
    python -m etl.bronze.ingest_genres --date 2026-06-21
    python -m etl.bronze.ingest_genres --with-tv
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
    with_tv: bool = False,
) -> str:
    """Fetch the TMDB genre list(s) and write them to Bronze S3.

    Always writes the movie genre list to `genres.json`. When `with_tv` is
    True, also writes the TV genre list to `genres_tv.json` in the same
    partition. Returns the s3:// URI of the movie list (the always-present
    one), unchanged by `with_tv` so existing callers are unaffected.

    Idempotent: re-running with the same `ingestion_date` writes identical data
    to the same key(s) (same source, same date → same output).
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    t0 = time.monotonic()
    logger.info("Starting genre ingestion for date=%s (with_tv=%s)", ingestion_date, with_tv)

    payload = client.get_genres()
    genres = payload.get("genres", [])
    logger.info("Fetched %d movie genres from TMDB", len(genres))

    key = s3_utils.build_path("bronze", "genres", ingestion_date, "genres.json")
    uri = s3_utils.write_json(config.S3_BUCKET, key, payload)

    if with_tv:
        tv_payload = client.get_tv_genres()
        tv_genres = tv_payload.get("genres", [])
        tv_key = s3_utils.build_path("bronze", "genres", ingestion_date, "genres_tv.json")
        tv_uri = s3_utils.write_json(config.S3_BUCKET, tv_key, tv_payload)
        logger.info("Fetched %d TV genres from TMDB, written to %s", len(tv_genres), tv_uri)

    elapsed = time.monotonic() - t0
    logger.info(
        "Genre ingestion complete: %d movie genres written to %s in %.2fs",
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
    parser.add_argument(
        "--with-tv",
        action="store_true",
        help="Also fetch genre/tv/list and write genres_tv.json (one extra call).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_genres")
    args = _parse_args()
    ingest_genres(ingestion_date=args.date, with_tv=args.with_tv)
