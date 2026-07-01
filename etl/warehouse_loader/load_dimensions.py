"""Warehouse loader: Dimensions.

Reads the Silver Parquet files for a given ingestion_date and upserts them
into the PostgreSQL dimension tables (dim_movie, dim_actor, dim_director,
dim_genre). dim_date is populated separately as a full calendar table that
does not depend on any Silver data.

Upserts use ON CONFLICT (pk) DO UPDATE, so re-running the loader for the
same or a later ingestion_date is idempotent — existing rows are refreshed
in place rather than duplicated.

S3 sources:
    silver/movies/ingestion_date=YYYY-MM-DD/movies.parquet
    silver/actors/ingestion_date=YYYY-MM-DD/actors.parquet
    silver/directors/ingestion_date=YYYY-MM-DD/directors.parquet
    silver/genres/ingestion_date=YYYY-MM-DD/genres.parquet

Usage:
    python -m etl.warehouse_loader.load_dimensions
    python -m etl.warehouse_loader.load_dimensions --date 2026-06-22
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import logging
import time
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from etl import s3_utils
from warehouse.db import get_session

logger = logging.getLogger(__name__)

_DEFAULT_CALENDAR_START = dt.date(1900, 1, 1)
_DEFAULT_CALENDAR_END = dt.date(2035, 12, 31)


def _read_silver_parquet(bucket: str, entity: str, ingestion_date: dt.date, filename: str) -> pd.DataFrame:
    """Download and parse a Silver Parquet file from S3."""
    key = s3_utils.build_path("silver", entity, ingestion_date, filename)
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return pd.read_parquet(io.BytesIO(response["Body"].read()))


def _records(df: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    """Convert selected columns of a DataFrame to a list of dicts, with NA -> None."""
    subset = df[columns].astype(object).where(pd.notnull(df[columns]), None)
    return subset.to_dict("records")


def _upsert(session: Session, table: str, pk_cols: list[str], columns: list[str],
            records: list[dict[str, Any]]) -> int:
    """Bulk upsert records into `table`, updating non-PK columns on conflict."""
    if not records:
        return 0
    update_cols = [c for c in columns if c not in pk_cols]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(f':{c}' for c in columns)}) "
        f"ON CONFLICT ({', '.join(pk_cols)}) DO UPDATE SET {set_clause}"
    )
    session.execute(text(sql), records)
    return len(records)


def load_dim_movie(session: Session, df: pd.DataFrame) -> int:
    """Upsert Silver movies into dim_movie."""
    columns = ["movie_id", "title", "release_date", "runtime", "budget", "revenue",
               "original_language", "status"]
    records = _records(df, columns)
    count = _upsert(session, "dim_movie", ["movie_id"], columns, records)
    logger.info("dim_movie: upserted %d row(s)", count)
    return count


def load_dim_actor(session: Session, df: pd.DataFrame) -> int:
    """Upsert Silver actors into dim_actor (person_id -> actor_id)."""
    df = df.rename(columns={"person_id": "actor_id"})
    columns = ["actor_id", "name", "gender", "popularity"]
    records = _records(df, columns)
    count = _upsert(session, "dim_actor", ["actor_id"], columns, records)
    logger.info("dim_actor: upserted %d row(s)", count)
    return count


def load_dim_director(session: Session, df: pd.DataFrame) -> int:
    """Upsert Silver directors into dim_director (person_id -> director_id)."""
    df = df.rename(columns={"person_id": "director_id"})
    columns = ["director_id", "name", "gender", "popularity"]
    records = _records(df, columns)
    count = _upsert(session, "dim_director", ["director_id"], columns, records)
    logger.info("dim_director: upserted %d row(s)", count)
    return count


def load_dim_genre(session: Session, df: pd.DataFrame) -> int:
    """Upsert Silver genres into dim_genre."""
    columns = ["genre_id", "genre_name"]
    records = _records(df, columns)
    count = _upsert(session, "dim_genre", ["genre_id"], columns, records)
    logger.info("dim_genre: upserted %d row(s)", count)
    return count


def _build_calendar(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Build a full day-granularity calendar DataFrame between start and end (inclusive)."""
    dates = pd.date_range(start=start, end=end, freq="D")
    df = pd.DataFrame({"full_date": dates})
    df["date_id"] = df["full_date"].dt.strftime("%Y%m%d").astype("int64")
    df["year"] = df["full_date"].dt.year.astype("int64")
    df["month"] = df["full_date"].dt.month.astype("int64")
    df["day"] = df["full_date"].dt.day.astype("int64")
    df["decade"] = (df["year"] // 10 * 10).astype("int64")
    df["full_date"] = df["full_date"].dt.date
    return df


def load_dim_date(session: Session, start: dt.date = _DEFAULT_CALENDAR_START,
                   end: dt.date = _DEFAULT_CALENDAR_END) -> int:
    """Populate dim_date as a full calendar table between start and end (inclusive)."""
    df = _build_calendar(start, end)
    columns = ["date_id", "full_date", "year", "month", "day", "decade"]
    records = _records(df, columns)
    count = _upsert(session, "dim_date", ["date_id"], columns, records)
    logger.info("dim_date: upserted %d row(s) (%s to %s)", count, start, end)
    return count


def load_dimensions(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    calendar_start: dt.date = _DEFAULT_CALENDAR_START,
    calendar_end: dt.date = _DEFAULT_CALENDAR_END,
) -> dict[str, int]:
    """Read Silver Parquet for `ingestion_date` and upsert all dimension tables.

    Returns a dict of table name -> row count upserted.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting dimension load for ingestion_date=%s", ingestion_date)

    movies_df = _read_silver_parquet(bucket, "movies", ingestion_date, "movies.parquet")
    actors_df = _read_silver_parquet(bucket, "actors", ingestion_date, "actors.parquet")
    directors_df = _read_silver_parquet(bucket, "directors", ingestion_date, "directors.parquet")
    genres_df = _read_silver_parquet(bucket, "genres", ingestion_date, "genres.parquet")

    counts: dict[str, int] = {}
    with get_session() as session:
        counts["dim_movie"] = load_dim_movie(session, movies_df)
        counts["dim_actor"] = load_dim_actor(session, actors_df)
        counts["dim_director"] = load_dim_director(session, directors_df)
        counts["dim_genre"] = load_dim_genre(session, genres_df)
        counts["dim_date"] = load_dim_date(session, calendar_start, calendar_end)

    elapsed = time.monotonic() - t0
    logger.info(
        "Dimension load complete: %s in %.2fs",
        ", ".join(f"{k}={v}" for k, v in counts.items()), elapsed,
    )
    return counts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upsert Silver Parquet into the PostgreSQL dimension tables."
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
    setup_logging("load_dimensions")
    args = _parse_args()
    load_dimensions(ingestion_date=args.date)
