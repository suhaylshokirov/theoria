"""Silver transform: Movies.

Reads all Bronze movie-detail JSON files for a given ingestion_date,
flattens each payload into one row per movie, casts every field to its
target type, deduplicates on movie_id (keeping the last-seen record),
and writes a single Parquet file to the Silver layer.

S3 source:  bronze/movie_details/ingestion_date=YYYY-MM-DD/<movie_id>.json
S3 output:  silver/movies/ingestion_date=YYYY-MM-DD/movies.parquet

Idempotent: running twice for the same date overwrites the same key with
the same content.

Usage:
    python -m etl.silver.transform_movies
    python -m etl.silver.transform_movies --date 2026-06-22
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import time
from typing import Any

import pandas as pd

import config
from etl import s3_utils

logger = logging.getLogger(__name__)


def _list_bronze_keys(bucket: str, ingestion_date: dt.date) -> list[str]:
    """Return every .json key under the bronze/movie_details partition for this date."""
    prefix = s3_utils.build_path("bronze", "movie_details", ingestion_date, "")
    client = s3_utils.get_s3_client()
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def _read_json_from_s3(bucket: str, key: str) -> dict[str, Any]:
    """Download and parse a single JSON object from S3."""
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read())


def _flatten_movie(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract and rename the fields we keep from one TMDB movie-detail payload."""
    return {
        "movie_id": raw.get("id"),
        "title": raw.get("title"),
        "release_date": raw.get("release_date") or None,
        "runtime": raw.get("runtime"),
        "budget": raw.get("budget"),
        "revenue": raw.get("revenue"),
        "original_language": raw.get("original_language"),
        "status": raw.get("status"),
        "vote_average": raw.get("vote_average"),
        "vote_count": raw.get("vote_count"),
        "popularity": raw.get("popularity"),
        "overview": raw.get("overview"),
        # Flatten nested genres list to a list of IDs for the bridge table.
        "genre_ids": [g["id"] for g in raw.get("genres", [])],
    }


def _cast_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast every column to its intended type; bad values become NaN/NaT, not crashes."""
    df = df.copy()
    df["movie_id"] = pd.to_numeric(df["movie_id"], errors="coerce").astype("Int64")
    df["runtime"] = pd.to_numeric(df["runtime"], errors="coerce").astype("Int64")
    df["budget"] = pd.to_numeric(df["budget"], errors="coerce").astype("Int64")
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").astype("Int64")
    df["vote_count"] = pd.to_numeric(df["vote_count"], errors="coerce").astype("Int64")
    df["vote_average"] = pd.to_numeric(df["vote_average"], errors="coerce")
    df["popularity"] = pd.to_numeric(df["popularity"], errors="coerce")
    # Empty strings from TMDB for missing dates become NaT, not errors.
    df["release_date"] = pd.to_datetime(df["release_date"], errors="coerce").dt.date
    return df


def transform_movies(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Bronze movie-detail JSON → clean → deduplicate → write Silver Parquet.

    Reads every .json file from the bronze/movie_details partition for
    `ingestion_date`, flattens and casts each record, drops duplicates on
    movie_id, then writes a single movies.parquet to the silver/movies
    partition for the same date.

    Returns the s3:// URI of the written Parquet file.

    Raises FileNotFoundError if no Bronze files exist for the given date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver movie transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze movie-detail files found for ingestion_date={ingestion_date}"
        )
    logger.info("Found %d Bronze JSON file(s) to process", len(keys))

    rows: list[dict[str, Any]] = []
    errors = 0
    for key in keys:
        try:
            raw = _read_json_from_s3(bucket, key)
            rows.append(_flatten_movie(raw))
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/flatten %s: %s", key, exc)

    if not rows:
        raise RuntimeError(
            f"Every Bronze file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    df = pd.DataFrame(rows)
    df = _cast_types(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["movie_id"], keep="last")
    dupes_dropped = before_dedup - len(df)
    if dupes_dropped:
        logger.info("Dropped %d duplicate movie_id row(s)", dupes_dropped)

    null_ids = df["movie_id"].isna().sum()
    if null_ids:
        logger.warning("Dropping %d row(s) with null movie_id", null_ids)
        df = df.dropna(subset=["movie_id"])

    output_key = s3_utils.build_path("silver", "movies", ingestion_date, "movies.parquet")
    uri = s3_utils.write_parquet(bucket, output_key, df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver movie transform complete: %d rows written, %d parse errors in %.2fs",
        len(df), errors, elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze movie-detail JSON to Silver Parquet."
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
    setup_logging("transform_movies")
    args = _parse_args()
    transform_movies(ingestion_date=args.date)
