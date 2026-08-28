"""Gold: a dated snapshot of every film's volatile metrics.

``fact_movie_metrics`` has PK ``(movie_id, date_id, genre_id)`` where
``date_id`` is derived from the film's *release* date — ``ingestion_date`` is
only a column. So when a nightly refresh re-loads it, the row upserts in place
and the previous rating / vote_count / revenue is simply gone. The warehouse
holds "latest", never a history.

This module writes that history to the lake instead: one row per film per run,
appended as a new ``ingestion_date=`` partition. History-of-measurements is
exactly what the data lake is for, and ~1,215 rows/day would grow unbounded
against a 0.5 GB managed-Postgres tier. This is what makes "rating over time"
or "revenue still accumulating after release" answerable later.

S3 source:  silver/movies/ingestion_date=YYYY-MM-DD/movies.parquet
S3 output:  gold/metrics_snapshot/ingestion_date=YYYY-MM-DD/metrics_snapshot.parquet

Columns: movie_id, snapshot_date, rating, vote_count, revenue, popularity

Idempotent: running twice for the same date overwrites the same key with the
same content.

Usage:
    python -m etl.gold.build_metrics_snapshot
    python -m etl.gold.build_metrics_snapshot --date 2026-07-29
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import pandas as pd

import config
from etl import s3_utils
from etl.warehouse_loader.common import _read_silver_parquet

logger = logging.getLogger(__name__)

# Silver column -> snapshot column. `vote_average` is renamed to `rating` to
# match the vocabulary the warehouse and the site use; the rest pass through.
_COLUMN_MAP = {
    "movie_id": "movie_id",
    "vote_average": "rating",
    "vote_count": "vote_count",
    "revenue": "revenue",
    "popularity": "popularity",
}


def build_metrics_snapshot(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Silver movies and write one dated volatile-metrics snapshot to Gold.

    Returns the s3:// URI of the written Parquet file.
    Raises FileNotFoundError if the Silver movies file is missing for the date.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting metrics snapshot for date=%s", ingestion_date)

    movies = _read_silver_parquet(bucket, "movies", ingestion_date, "movies.parquet")

    snapshot = movies[list(_COLUMN_MAP)].rename(columns=_COLUMN_MAP)
    snapshot = snapshot.dropna(subset=["movie_id"])
    snapshot.insert(1, "snapshot_date", ingestion_date)

    key = s3_utils.build_path(
        "gold", "metrics_snapshot", ingestion_date, "metrics_snapshot.parquet"
    )
    uri = s3_utils.write_parquet(bucket, key, snapshot)

    elapsed = time.monotonic() - t0
    logger.info(
        "Metrics snapshot complete: %d film(s) snapshotted for %s in %.2fs -> %s",
        len(snapshot), ingestion_date, elapsed, uri,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a dated snapshot of every film's volatile metrics to Gold."
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

    setup_logging("build_metrics_snapshot")
    args = _parse_args()
    print(build_metrics_snapshot(ingestion_date=args.date))
