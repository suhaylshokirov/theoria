"""Bronze ingestion: cast & crew credits per movie.

Fetches the TMDB credits endpoint for every supplied movie_id and writes each
response as a separate raw JSON file to the Bronze layer on S3.

Each movie is written individually as it completes — a failure on one movie
never loses credits already written. Failures are logged with the specific
movie_id so failed IDs can be retried without re-fetching the full catalogue.

S3 layout:
    bronze/credits/ingestion_date=YYYY-MM-DD/<movie_id>.json

Usage:
    python -m etl.bronze.ingest_credits --movie-ids 550 551 552
    python -m etl.bronze.ingest_credits --date 2026-06-22 --movie-ids 550
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


def ingest_credits(
    movie_ids: list[int],
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
) -> tuple[list[int], list[int]]:
    """Fetch cast & crew credits for each movie_id and write them to Bronze S3.

    Each movie's credits are written before the next is fetched so a mid-run
    failure never discards already-completed work. Failures are logged with the
    specific movie_id so callers can retry only the failed subset.

    Returns (succeeded_ids, failed_ids).

    Idempotent: re-running with the same movie_id and ingestion_date writes
    the same key with the same content.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    t0 = time.monotonic()
    total = len(movie_ids)
    logger.info(
        "Starting credits ingestion: %d movie(s), date=%s", total, ingestion_date
    )

    succeeded: list[int] = []
    failed: list[int] = []

    for movie_id in movie_ids:
        try:
            payload = client.get_movie_credits(movie_id)

            key = s3_utils.build_path(
                "bronze", "credits", ingestion_date, f"{movie_id}.json"
            )
            s3_utils.write_json(config.S3_BUCKET, key, payload)

            succeeded.append(movie_id)
            logger.debug("movie_id=%d credits written to Bronze", movie_id)

        except Exception as exc:
            failed.append(movie_id)
            logger.error("movie_id=%d credits failed, skipping: %s", movie_id, exc)

    elapsed = time.monotonic() - t0
    logger.info(
        "Credits ingestion complete: %d written, %d failed out of %d in %.2fs",
        len(succeeded), len(failed), total, elapsed,
    )
    if failed:
        logger.warning("Failed movie_ids: %s", failed)

    return succeeded, failed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest TMDB movie credits to Bronze S3."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--movie-ids",
        type=int,
        nargs="+",
        required=True,
        metavar="ID",
        help="One or more TMDB movie IDs to fetch credits for.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_credits")
    args = _parse_args()
    ingest_credits(movie_ids=args.movie_ids, ingestion_date=args.date)
