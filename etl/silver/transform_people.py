"""Silver transform: People (actors & directors).

Reads all Bronze credits JSON files for a given ingestion_date, splits each
payload into cast and crew records, standardizes and casts types, deduplicates
on person_id, and writes two separate Parquet files to the Silver layer.

S3 source:    bronze/credits/ingestion_date=YYYY-MM-DD/<movie_id>.json
S3 outputs:
    silver/actors/ingestion_date=YYYY-MM-DD/actors.parquet
    silver/directors/ingestion_date=YYYY-MM-DD/directors.parquet

Actors come from the TMDB `cast` array (all entries).
Directors come from the TMDB `crew` array filtered to job == "Director".

Idempotent: running twice for the same date overwrites the same keys with
the same content.

Usage:
    python -m etl.silver.transform_people
    python -m etl.silver.transform_people --date 2026-06-22
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
    """Return every .json key under the bronze/credits partition for this date."""
    prefix = s3_utils.build_path("bronze", "credits", ingestion_date, "")
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


def _extract_actors(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one row per cast member from a TMDB credits payload."""
    rows = []
    for member in payload.get("cast", []):
        rows.append({
            "person_id": member.get("id"),
            "name": member.get("name"),
            "gender": member.get("gender"),
            "popularity": member.get("popularity"),
            "profile_path": member.get("profile_path") or None,
        })
    return rows


def _extract_directors(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one row per Director from the crew array of a TMDB credits payload."""
    rows = []
    for member in payload.get("crew", []):
        if member.get("job") == "Director":
            rows.append({
                "person_id": member.get("id"),
                "name": member.get("name"),
                "gender": member.get("gender"),
                "popularity": member.get("popularity"),
                "profile_path": member.get("profile_path") or None,
            })
    return rows


def _cast_people_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast columns to intended types; bad values become NaN, not crashes."""
    df = df.copy()
    df["person_id"] = pd.to_numeric(df["person_id"], errors="coerce").astype("Int64")
    df["gender"] = pd.to_numeric(df["gender"], errors="coerce").astype("Int64")
    df["popularity"] = pd.to_numeric(df["popularity"], errors="coerce")
    return df


def transform_people(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> tuple[str, str]:
    """Read Bronze credits JSON → extract actors & directors → deduplicate → write Silver Parquet.

    Reads every .json file from the bronze/credits partition for `ingestion_date`,
    extracts cast rows (actors) and crew rows filtered to job=="Director", casts
    fields to target types, deduplicates each group on person_id (keeping
    last-seen record), and writes two Parquet files:
        silver/actors/ingestion_date=YYYY-MM-DD/actors.parquet
        silver/directors/ingestion_date=YYYY-MM-DD/directors.parquet

    Returns (actors_uri, directors_uri).

    Raises FileNotFoundError if no Bronze credits files exist for the given date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver people transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze credits files found for ingestion_date={ingestion_date}"
        )
    logger.info("Found %d Bronze JSON file(s) to process", len(keys))

    actor_rows: list[dict[str, Any]] = []
    director_rows: list[dict[str, Any]] = []
    errors = 0

    for key in keys:
        try:
            payload = _read_json_from_s3(bucket, key)
            actor_rows.extend(_extract_actors(payload))
            director_rows.extend(_extract_directors(payload))
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/parse %s: %s", key, exc)

    if not actor_rows and not director_rows:
        raise RuntimeError(
            f"Every Bronze credits file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    # --- Actors ---
    df_actors = pd.DataFrame(actor_rows)
    df_actors = _cast_people_types(df_actors)

    before_dedup = len(df_actors)
    df_actors = df_actors.drop_duplicates(subset=["person_id"], keep="last")
    dupes = before_dedup - len(df_actors)
    if dupes:
        logger.info("Actors: dropped %d duplicate person_id row(s)", dupes)

    null_ids = df_actors["person_id"].isna().sum()
    if null_ids:
        logger.warning("Actors: dropping %d row(s) with null person_id", null_ids)
        df_actors = df_actors.dropna(subset=["person_id"])

    actors_key = s3_utils.build_path("silver", "actors", ingestion_date, "actors.parquet")
    actors_uri = s3_utils.write_parquet(bucket, actors_key, df_actors)

    # --- Directors ---
    df_directors = pd.DataFrame(director_rows) if director_rows else pd.DataFrame(
        columns=["person_id", "name", "gender", "popularity", "profile_path"]
    )
    df_directors = _cast_people_types(df_directors)

    before_dedup = len(df_directors)
    df_directors = df_directors.drop_duplicates(subset=["person_id"], keep="last")
    dupes = before_dedup - len(df_directors)
    if dupes:
        logger.info("Directors: dropped %d duplicate person_id row(s)", dupes)

    null_ids = df_directors["person_id"].isna().sum()
    if null_ids:
        logger.warning("Directors: dropping %d row(s) with null person_id", null_ids)
        df_directors = df_directors.dropna(subset=["person_id"])

    directors_key = s3_utils.build_path("silver", "directors", ingestion_date, "directors.parquet")
    directors_uri = s3_utils.write_parquet(bucket, directors_key, df_directors)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver people transform complete: %d actors, %d directors written, %d parse errors in %.2fs",
        len(df_actors), len(df_directors), errors, elapsed,
    )
    return actors_uri, directors_uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze credits JSON to Silver actors and directors Parquet."
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
    setup_logging("transform_people")
    args = _parse_args()
    transform_people(ingestion_date=args.date)
