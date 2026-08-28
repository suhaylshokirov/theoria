"""Bronze refresh: re-fetch details + credits for films already in the warehouse.

Every other Bronze ingest module sources its movie_ids from a *discovery*
endpoint (``movie/popular``, ``discover/movie``) — nothing sources them from
the warehouse. That is the reason a film already in the catalogue can only be
refreshed by a full ``run_pipeline.py`` re-run: there was no path that says
"update the films I already have".

This module is that path. It takes ``SELECT movie_id FROM dim_movie`` as its
input and writes an ordinary Bronze partition — the *same* two key shapes that
``ingest_movie_details`` and ``ingest_credits`` write — so every Silver / Gold /
warehouse stage downstream runs unchanged. Bronze stays append-only: a refresh
writes a new ``ingestion_date=`` partition, it never edits an existing one.

One TMDB call per film, not two: ``append_to_response=credits`` returns the
detail payload with credits folded in, and this module splits it back into the
two files the existing transforms expect.

S3 layout (identical to the ingest path):
    bronze/movie_details/ingestion_date=YYYY-MM-DD/<movie_id>.json
    bronze/credits/ingestion_date=YYYY-MM-DD/<movie_id>.json

Usage:
    python -m etl.bronze.refresh_movies
    python -m etl.bronze.refresh_movies --date 2026-07-29
    python -m etl.bronze.refresh_movies --movie-ids 550 551
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

from sqlalchemy import text
from sqlalchemy.engine import Engine

import config
from etl import s3_utils
from etl.tmdb_client import TMDBClient
from warehouse.db import get_engine

logger = logging.getLogger(__name__)

_EMPTY_CREDITS: dict[str, list] = {"cast": [], "crew": []}


def _movie_ids_from_warehouse(engine: Engine) -> list[int]:
    """Return every movie_id in dim_movie, ascending.

    Ordered so a mid-run failure and a re-run process films in the same
    sequence, and so two runs' logs line up film-for-film.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT movie_id FROM dim_movie ORDER BY movie_id")
        ).scalars().all()
    return [int(r) for r in rows]


def _split_payload(movie_id: int, payload: dict) -> tuple[dict, dict]:
    """Separate one append_to_response=credits payload into (details, credits).

    ``details`` is the movie object with the ``credits`` key removed, so it is
    byte-comparable to what ``ingest_movie_details`` writes. ``credits`` is
    rebuilt into the ``{"id", "cast", "crew"}`` shape the standalone
    ``movie/{id}/credits`` endpoint returns, which is what ``ingest_credits``
    writes and what ``transform_credits_bridge`` / ``transform_people`` read.
    """
    details = {k: v for k, v in payload.items() if k != "credits"}
    raw_credits = payload.get("credits")
    if not isinstance(raw_credits, dict):
        logger.warning(
            "movie_id=%d: append_to_response returned no credits, writing empty",
            movie_id,
        )
        raw_credits = _EMPTY_CREDITS
    credits = {
        "id": movie_id,
        "cast": raw_credits.get("cast", []),
        "crew": raw_credits.get("crew", []),
    }
    return details, credits


def refresh_movies(
    movie_ids: list[int] | None = None,
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
    engine: Engine | None = None,
) -> tuple[list[int], list[int]]:
    """Re-fetch details + credits for known films and write them to Bronze S3.

    ``movie_ids`` defaults to every id in ``dim_movie``. Each film is written
    (both files) before the next is fetched, so a mid-run failure never
    discards completed work. Failures are logged with the specific movie_id so
    callers can retry only the failed subset.

    Returns ``(succeeded_ids, failed_ids)``.

    Idempotent: re-running with the same movie_id and ingestion_date rewrites
    the same two keys with the same content.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()
    if movie_ids is None:
        movie_ids = _movie_ids_from_warehouse(engine or get_engine())

    t0 = time.monotonic()
    total = len(movie_ids)
    logger.info(
        "Starting movie refresh: %d film(s) from the warehouse, date=%s",
        total, ingestion_date,
    )

    succeeded: list[int] = []
    failed: list[int] = []

    for movie_id in movie_ids:
        try:
            payload = client.get_movie_details(
                movie_id, append_to_response="credits"
            )
            details, credits = _split_payload(movie_id, payload)

            details_key = s3_utils.build_path(
                "bronze", "movie_details", ingestion_date, f"{movie_id}.json"
            )
            credits_key = s3_utils.build_path(
                "bronze", "credits", ingestion_date, f"{movie_id}.json"
            )
            s3_utils.write_json(config.S3_BUCKET, details_key, details)
            s3_utils.write_json(config.S3_BUCKET, credits_key, credits)

            succeeded.append(movie_id)
            logger.debug("movie_id=%d refreshed (details + credits)", movie_id)

        except Exception as exc:
            failed.append(movie_id)
            logger.error("movie_id=%d refresh failed, skipping: %s", movie_id, exc)

    elapsed = time.monotonic() - t0
    logger.info(
        "Movie refresh complete: %d refreshed, %d failed out of %d in %.2fs",
        len(succeeded), len(failed), total, elapsed,
    )
    if failed:
        logger.warning("Failed movie_ids: %s", failed)

    return succeeded, failed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-fetch Bronze details + credits for films already in dim_movie."
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
        default=None,
        metavar="ID",
        help="Explicit movie IDs to refresh (default: every id in dim_movie).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging

    setup_logging("refresh_movies")
    args = _parse_args()
    refresh_movies(movie_ids=args.movie_ids, ingestion_date=args.date)
