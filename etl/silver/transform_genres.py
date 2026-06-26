"""Silver transform: Genres.

Reads the Bronze genre list JSON for a given ingestion_date, flattens the
payload into one row per genre, casts types, deduplicates on genre_id, and
writes a single Parquet file to the Silver layer.

S3 source:  bronze/genres/ingestion_date=YYYY-MM-DD/genres.json
S3 output:  silver/genres/ingestion_date=YYYY-MM-DD/genres.parquet

Idempotent: running twice for the same date overwrites the same key with the
same content.

Usage:
    python -m etl.silver.transform_genres
    python -m etl.silver.transform_genres --date 2026-06-22
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


def _read_bronze_genres(bucket: str, ingestion_date: dt.date) -> dict[str, Any]:
    """Download and parse the Bronze genres.json for this date."""
    key = s3_utils.build_path("bronze", "genres", ingestion_date, "genres.json")
    client = s3_utils.get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.NoSuchKey:
        raise FileNotFoundError(
            f"No Bronze genre file found for ingestion_date={ingestion_date} (key={key})"
        )
    return json.loads(response["Body"].read())


def _extract_genres(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one row per genre from a TMDB genre-list payload."""
    return [
        {"genre_id": g.get("id"), "genre_name": g.get("name")}
        for g in payload.get("genres", [])
    ]


def _cast_genre_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast columns to intended types; bad values become NaN, not crashes."""
    df = df.copy()
    df["genre_id"] = pd.to_numeric(df["genre_id"], errors="coerce").astype("Int64")
    df["genre_name"] = df["genre_name"].astype("string")
    return df


def transform_genres(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Bronze genre JSON → clean → deduplicate → write Silver Parquet.

    Reads bronze/genres/ingestion_date=.../genres.json, flattens each entry
    into a (genre_id, genre_name) row, casts types, deduplicates on genre_id,
    and writes silver/genres/ingestion_date=.../genres.parquet.

    Returns the s3:// URI of the written Parquet file.

    Raises FileNotFoundError if no Bronze genre file exists for the given date.
    Raises ValueError if the genres list inside the payload is empty.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver genre transform for date=%s", ingestion_date)

    payload = _read_bronze_genres(bucket, ingestion_date)
    rows = _extract_genres(payload)

    if not rows:
        raise ValueError(
            f"Bronze genre file for ingestion_date={ingestion_date} contains no genres."
        )
    logger.info("Extracted %d genre row(s) from Bronze", len(rows))

    df = pd.DataFrame(rows)
    df = _cast_genre_types(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["genre_id"], keep="last")
    dupes_dropped = before_dedup - len(df)
    if dupes_dropped:
        logger.info("Dropped %d duplicate genre_id row(s)", dupes_dropped)

    null_ids = df["genre_id"].isna().sum()
    if null_ids:
        logger.warning("Dropping %d row(s) with null genre_id", null_ids)
        df = df.dropna(subset=["genre_id"])

    output_key = s3_utils.build_path("silver", "genres", ingestion_date, "genres.parquet")
    uri = s3_utils.write_parquet(bucket, output_key, df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver genre transform complete: %d rows written in %.2fs",
        len(df), elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze genre JSON to Silver Parquet."
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
    setup_logging("transform_genres")
    args = _parse_args()
    transform_genres(ingestion_date=args.date)
