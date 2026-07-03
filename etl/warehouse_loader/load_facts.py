"""Warehouse loader: Facts.

Reads the Silver Parquet files for a given ingestion_date, resolves natural
keys against the dimension tables already loaded by load_dimensions.py, and
upserts into the two fact tables (fact_movie_metrics, fact_casting).

Rows that fail an FK lookup (reference a movie/genre/date/actor/director not
present in the dimensions) are never inserted and never silently dropped —
they are quarantined to data_quality/rejected/ with a rejection_reason
column, mirroring the pattern used by data_quality/silver_checks.py.

fact_movie_metrics is built by exploding each movie's genre_ids: one row per
(movie_id, date_id, genre_id). date_id is derived from release_date to match
the YYYYMMDD surrogate key produced by load_dim_date().

fact_casting requires both actor_id and director_id NOT NULL (see
warehouse/ddl/02_facts.sql), but Silver's credits_bridge stores cast and crew
as separate per-person rows. We resolve this by cross-joining, per movie,
every credited actor with every credited director — one row per
(movie_id, actor_id, director_id) pair, carrying the actor's role/ordering.
A movie with no credited director (or no credited actors) contributes no
casting rows and its bridge rows are quarantined.

S3 sources:
    silver/movies/ingestion_date=YYYY-MM-DD/movies.parquet
    silver/credits_bridge/ingestion_date=YYYY-MM-DD/credits_bridge.parquet

Both fact tables carry an ingestion_date column recording which Silver
partition last wrote each row. It does not participate in either table's
PRIMARY KEY — the existing composite PK (movie_id, date_id, genre_id) /
(movie_id, actor_id, director_id) already guards against duplicate inserts
via the ON CONFLICT upsert, so re-running the same or an earlier partition
is a no-op update rather than a new row.

Usage:
    python -m etl.warehouse_loader.load_facts
    python -m etl.warehouse_loader.load_facts --date 2026-06-22
    python -m etl.warehouse_loader.load_facts --incremental
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

import config
from etl.incremental import pending_partitions, set_watermark
from etl.warehouse_loader.common import _existing_ids, _read_silver_parquet, _upsert
from warehouse.db import get_session

logger = logging.getLogger(__name__)

_LOADER_NAME = "load_facts"
_WATERMARK_ENTITY = "movies"  # reference entity used to discover new Silver partitions


def _records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert NaN/NaT-bearing values in a list of dicts to None."""
    return [{k: (None if pd.isna(v) else v) for k, v in row.items()} for row in rows]


def _write_rejects(rejects: list[dict[str, Any]], entity: str, ingestion_date: dt.date,
                    rejected_dir: Path) -> Path | None:
    """Write quarantined rows to a local Parquet file. Returns the path, or None if empty."""
    if not rejects:
        return None
    df = pd.DataFrame(rejects)
    rejected_dir.mkdir(parents=True, exist_ok=True)
    path = rejected_dir / f"{entity}_rejected_{ingestion_date.isoformat()}.parquet"
    df.to_parquet(path, engine="pyarrow", index=False)
    logger.warning("Wrote %d rejected row(s) for entity=%s to %s", len(df), entity, path)
    return path


def _build_movie_metrics_rows(
    movies_df: pd.DataFrame,
    valid_movie_ids: set[int],
    valid_date_ids: set[int],
    valid_genre_ids: set[int],
    ingestion_date: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Explode Silver movies into (movie_id, date_id, genre_id) fact rows.

    Returns (rows, rejects). A row is rejected if its movie_id/date_id/
    genre_id cannot be resolved against the dimension tables.
    """
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []

    for record in movies_df.to_dict("records"):
        movie_id = record["movie_id"]
        base = {
            "movie_id": movie_id,
            "rating": record.get("vote_average"),
            "vote_count": record.get("vote_count"),
            "revenue": record.get("revenue"),
            "budget": record.get("budget"),
            "popularity": record.get("popularity"),
        }

        if pd.isna(movie_id) or int(movie_id) not in valid_movie_ids:
            rejects.append({**base, "rejection_reason": "unknown movie_id"})
            continue

        release_date = record.get("release_date")
        if release_date is None or pd.isna(release_date):
            rejects.append({**base, "rejection_reason": "missing release_date"})
            continue
        release_ts = pd.Timestamp(release_date)
        date_id = release_ts.year * 10_000 + release_ts.month * 100 + release_ts.day
        if date_id not in valid_date_ids:
            rejects.append({**base, "date_id": date_id, "rejection_reason": "unknown date_id"})
            continue

        genre_ids = record.get("genre_ids")
        genre_ids = list(genre_ids) if genre_ids is not None else []
        if not genre_ids:
            rejects.append({**base, "date_id": date_id, "rejection_reason": "no genres"})
            continue

        for genre_id in genre_ids:
            if genre_id is None or int(genre_id) not in valid_genre_ids:
                rejects.append({
                    **base, "date_id": date_id, "genre_id": genre_id,
                    "rejection_reason": "unknown genre_id",
                })
                continue
            rows.append({
                "movie_id": int(movie_id),
                "date_id": date_id,
                "genre_id": int(genre_id),
                "rating": base["rating"],
                "vote_count": base["vote_count"],
                "revenue": base["revenue"],
                "budget": base["budget"],
                "popularity": base["popularity"],
                "ingestion_date": ingestion_date,
            })

    return rows, rejects


def _build_casting_rows(
    bridge_df: pd.DataFrame,
    valid_movie_ids: set[int],
    valid_actor_ids: set[int],
    valid_director_ids: set[int],
    ingestion_date: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cross-join cast and director bridge rows per movie into fact_casting rows.

    Returns (rows, rejects).
    """
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []

    cast_df = bridge_df[bridge_df["credit_type"] == "cast"]
    director_df = bridge_df[
        (bridge_df["credit_type"] == "crew") & (bridge_df["role"] == "Director")
    ]

    for movie_id, actor_group in cast_df.groupby("movie_id"):
        movie_id_int = int(movie_id) if not pd.isna(movie_id) else None
        if movie_id_int is None or movie_id_int not in valid_movie_ids:
            for actor_row in actor_group.to_dict("records"):
                rejects.append({**actor_row, "rejection_reason": "unknown movie_id"})
            continue

        directors = director_df[director_df["movie_id"] == movie_id]
        if directors.empty:
            for actor_row in actor_group.to_dict("records"):
                rejects.append({**actor_row, "rejection_reason": "no director for movie"})
            continue

        for actor_row in actor_group.to_dict("records"):
            actor_id = actor_row["person_id"]
            if pd.isna(actor_id) or int(actor_id) not in valid_actor_ids:
                rejects.append({**actor_row, "rejection_reason": "unknown actor_id"})
                continue

            for director_row in directors.to_dict("records"):
                director_id = director_row["person_id"]
                if pd.isna(director_id) or int(director_id) not in valid_director_ids:
                    rejects.append({**director_row, "rejection_reason": "unknown director_id"})
                    continue
                rows.append({
                    "movie_id": movie_id_int,
                    "actor_id": int(actor_id),
                    "director_id": int(director_id),
                    "role": actor_row.get("role"),
                    "ordering": actor_row.get("ordering"),
                    "ingestion_date": ingestion_date,
                })

    return rows, rejects


def load_fact_movie_metrics(
    session: Session, movies_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Resolve and upsert Silver movies into fact_movie_metrics. Returns (count, rejects)."""
    valid_movie_ids = _existing_ids(session, "dim_movie", "movie_id")
    valid_date_ids = _existing_ids(session, "dim_date", "date_id")
    valid_genre_ids = _existing_ids(session, "dim_genre", "genre_id")

    rows, rejects = _build_movie_metrics_rows(
        movies_df, valid_movie_ids, valid_date_ids, valid_genre_ids, ingestion_date,
    )
    columns = ["movie_id", "date_id", "genre_id", "rating", "vote_count", "revenue",
               "budget", "popularity", "ingestion_date"]
    count = _upsert(session, "fact_movie_metrics", ["movie_id", "date_id", "genre_id"], columns, _records(rows))
    logger.info(
        "fact_movie_metrics: upserted %d row(s), rejected %d row(s)", count, len(rejects)
    )
    return count, rejects


def load_fact_casting(
    session: Session, bridge_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Resolve and upsert Silver credits_bridge into fact_casting. Returns (count, rejects)."""
    valid_movie_ids = _existing_ids(session, "dim_movie", "movie_id")
    valid_actor_ids = _existing_ids(session, "dim_actor", "actor_id")
    valid_director_ids = _existing_ids(session, "dim_director", "director_id")

    rows, rejects = _build_casting_rows(
        bridge_df, valid_movie_ids, valid_actor_ids, valid_director_ids, ingestion_date,
    )
    columns = ["movie_id", "actor_id", "director_id", "role", "ordering", "ingestion_date"]
    count = _upsert(session, "fact_casting", ["movie_id", "actor_id", "director_id"], columns, _records(rows))
    logger.info("fact_casting: upserted %d row(s), rejected %d row(s)", count, len(rejects))
    return count, rejects


def load_facts(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    rejected_dir: Path | None = None,
) -> dict[str, int]:
    """Read Silver Parquet for `ingestion_date`, resolve FKs, and upsert both fact tables.

    Returns a dict of table name -> row count upserted.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET
    if rejected_dir is None:
        rejected_dir = config.REJECTED_DIR

    t0 = time.monotonic()
    logger.info("Starting fact load for ingestion_date=%s", ingestion_date)

    movies_df = _read_silver_parquet(bucket, "movies", ingestion_date, "movies.parquet")
    bridge_df = _read_silver_parquet(bucket, "credits_bridge", ingestion_date, "credits_bridge.parquet")

    counts: dict[str, int] = {}
    with get_session() as session:
        counts["fact_movie_metrics"], metrics_rejects = load_fact_movie_metrics(session, movies_df, ingestion_date)
        counts["fact_casting"], casting_rejects = load_fact_casting(session, bridge_df, ingestion_date)

    _write_rejects(metrics_rejects, "fact_movie_metrics", ingestion_date, rejected_dir)
    _write_rejects(casting_rejects, "fact_casting", ingestion_date, rejected_dir)

    elapsed = time.monotonic() - t0
    logger.info(
        "Fact load complete: %s in %.2fs",
        ", ".join(f"{k}={v}" for k, v in counts.items()), elapsed,
    )
    return counts


def load_facts_incremental(
    bucket: str | None = None,
    rejected_dir: Path | None = None,
) -> dict[str, dict[str, int]]:
    """Process every Silver partition newer than this loader's watermark, in order.

    Mirrors load_dimensions_incremental(): discovers pending dates via
    etl.incremental.pending_partitions(), runs load_facts() for each, and
    advances the watermark only after each date completes successfully.

    Returns a dict of ingestion_date (ISO string) -> per-table row counts.
    """
    if bucket is None:
        bucket = config.S3_BUCKET

    with get_session() as session:
        dates = pending_partitions(session, _LOADER_NAME, bucket, "silver", _WATERMARK_ENTITY)

    if not dates:
        logger.info("No new Silver partitions to process for %s", _LOADER_NAME)
        return {}

    logger.info("%d pending partition(s) for %s: %s", len(dates), _LOADER_NAME, dates)

    results: dict[str, dict[str, int]] = {}
    for ingestion_date in dates:
        counts = load_facts(ingestion_date=ingestion_date, bucket=bucket, rejected_dir=rejected_dir)
        with get_session() as session:
            set_watermark(session, _LOADER_NAME, ingestion_date)
        logger.info("Watermark for %s advanced to %s", _LOADER_NAME, ingestion_date)
        results[ingestion_date.isoformat()] = counts

    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve Silver Parquet against dimensions and upsert the PostgreSQL fact tables."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today. Ignored with --incremental.",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Process every Silver partition newer than the stored watermark, instead of a single date.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("load_facts")
    args = _parse_args()
    if args.incremental:
        load_facts_incremental()
    else:
        load_facts(ingestion_date=args.date)
