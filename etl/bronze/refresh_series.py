"""Bronze refresh: re-fetch the detail payload for series already in the warehouse.

The TV counterpart of ``refresh_movies.py``. Every other TV Bronze module
sources its ``series_id``s from *discovery* (``discover/tv``) — nothing sources
them from the warehouse, so a show already in the catalogue could only be
refreshed by a full ``run_pipeline.py`` re-run. This module is the path that
says "update the shows I already have": it takes ``SELECT series_id FROM
dim_series`` as its input and writes an ordinary new Bronze
``series_details`` partition, so every Silver / Gold / warehouse stage
downstream runs unchanged. Bronze stays append-only — a refresh writes a new
``ingestion_date=`` partition, it never edits an existing one.

Unlike ``refresh_movies``, there is **no payload split**: the series detail
payload keeps ``aggregate_credits`` inline (the Task 77 ``videos`` precedent,
not the movie ``credits`` one — there is no standalone ``bronze/tv_credits``
entity to stay compatible with), so a refresh writes exactly the one file
shape ``ingest_series_details`` writes. This module therefore resolves the id
list and then delegates to that already-tested writer rather than carrying a
second copy of the fetch/write loop.

S3 layout (identical to the ingest path):
    bronze/series_details/ingestion_date=YYYY-MM-DD/<series_id>.json

Usage:
    python -m etl.bronze.refresh_series
    python -m etl.bronze.refresh_series --date 2026-09-10
    python -m etl.bronze.refresh_series --series-ids 1396 1399
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from etl.bronze.ingest_series_details import ingest_series_details
from etl.tmdb_client import TMDBClient
from warehouse.db import get_engine

logger = logging.getLogger(__name__)


def _series_ids_from_warehouse(engine: Engine) -> list[int]:
    """Return every series_id in dim_series, ascending.

    Ordered so a mid-run failure and a re-run process shows in the same
    sequence, and so two runs' logs line up series-for-series.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT series_id FROM dim_series ORDER BY series_id")
        ).scalars().all()
    return [int(r) for r in rows]


def refresh_series(
    series_ids: list[int] | None = None,
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
    engine: Engine | None = None,
) -> tuple[list[int], list[int]]:
    """Re-fetch the detail payload for known series and write it to Bronze S3.

    ``series_ids`` defaults to every id in ``dim_series``. Each show is written
    before the next is fetched (``ingest_series_details``'s contract), so a
    mid-run failure never discards completed work. Failures are logged with the
    specific series_id so callers can retry only the failed subset.

    Returns ``(succeeded_ids, failed_ids)``.

    Idempotent: re-running with the same series_id and ingestion_date rewrites
    the same key with the same content.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if series_ids is None:
        series_ids = _series_ids_from_warehouse(engine or get_engine())

    logger.info(
        "Starting series refresh: %d show(s) from the warehouse, date=%s",
        len(series_ids), ingestion_date,
    )
    return ingest_series_details(
        series_ids, ingestion_date=ingestion_date, client=client
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-fetch the Bronze detail payload for series already in dim_series."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--series-ids",
        type=int,
        nargs="+",
        default=None,
        metavar="ID",
        help="Explicit series IDs to refresh (default: every id in dim_series).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging

    setup_logging("refresh_series")
    args = _parse_args()
    refresh_series(series_ids=args.series_ids, ingestion_date=args.date)
