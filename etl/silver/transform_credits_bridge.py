"""Silver transform: Credits bridge.

Reads all Bronze credits JSON files for a given ingestion_date, extracts
(movie_id, person_id, role, ordering, credit_type) rows, deduplicates on
(movie_id, person_id, credit_type), validates referential integrity by
flagging orphan rows, and writes a single Parquet file to the Silver layer.

S3 source:  bronze/credits/ingestion_date=YYYY-MM-DD/<movie_id>.json
S3 output:  silver/credits_bridge/ingestion_date=YYYY-MM-DD/credits_bridge.parquet

Columns:
    movie_id    Int64   — TMDB movie ID (from root payload `id`)
    person_id   Int64   — TMDB person ID (from cast/crew `id`)
    credit_type string  — "cast" or "crew"
    role        string  — character name for cast; job title for crew
    ordering    Int64   — cast order (null for crew)

Dedup key: (movie_id, person_id, credit_type) — the same person can appear
as both an actor and a crew member in the same movie.

Orphan rows (null movie_id / person_id) are flagged with warnings and dropped,
but never cause the transform to crash. Callers may pass `known_movie_ids` and
`known_person_ids` sets to enable full referential-integrity checking; any row
whose ID is absent from those sets is logged as an orphan but still written
(not quarantined) so downstream tasks can decide how to handle them.

Idempotent: running twice for the same date overwrites the same key.

Usage:
    python -m etl.silver.transform_credits_bridge
    python -m etl.silver.transform_credits_bridge --date 2026-06-22
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


def _extract_bridge_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one bridge row per cast/crew entry from a TMDB credits payload.

    movie_id is taken from payload["id"] — the root-level TMDB movie ID.
    """
    movie_id = payload.get("id")
    rows: list[dict[str, Any]] = []

    for member in payload.get("cast", []):
        rows.append({
            "movie_id": movie_id,
            "person_id": member.get("id"),
            "credit_type": "cast",
            "role": member.get("character"),
            "ordering": member.get("order"),
        })

    for member in payload.get("crew", []):
        rows.append({
            "movie_id": movie_id,
            "person_id": member.get("id"),
            "credit_type": "crew",
            "role": member.get("job"),
            "ordering": None,
        })

    return rows


def _cast_bridge_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast columns to intended types; bad values become NaN, not crashes."""
    df = df.copy()
    df["movie_id"] = pd.to_numeric(df["movie_id"], errors="coerce").astype("Int64")
    df["person_id"] = pd.to_numeric(df["person_id"], errors="coerce").astype("Int64")
    df["credit_type"] = df["credit_type"].astype("string")
    df["role"] = df["role"].astype("string")
    df["ordering"] = pd.to_numeric(df["ordering"], errors="coerce").astype("Int64")
    return df


def _check_referential_integrity(
    df: pd.DataFrame,
    known_movie_ids: set[int] | None,
    known_person_ids: set[int] | None,
) -> None:
    """Log (but do not remove) rows whose IDs are absent from known sets."""
    if known_movie_ids is not None:
        orphan_movies = df.loc[
            df["movie_id"].notna() & ~df["movie_id"].isin(known_movie_ids), "movie_id"
        ].unique()
        if len(orphan_movies):
            logger.warning(
                "Referential integrity: %d bridge row(s) reference unknown movie_id(s): %s",
                len(df[df["movie_id"].isin(orphan_movies)]),
                sorted(int(x) for x in orphan_movies)[:20],
            )

    if known_person_ids is not None:
        orphan_people = df.loc[
            df["person_id"].notna() & ~df["person_id"].isin(known_person_ids), "person_id"
        ].unique()
        if len(orphan_people):
            logger.warning(
                "Referential integrity: %d bridge row(s) reference unknown person_id(s): %s",
                len(df[df["person_id"].isin(orphan_people)]),
                sorted(int(x) for x in orphan_people)[:20],
            )


def transform_credits_bridge(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    known_movie_ids: set[int] | None = None,
    known_person_ids: set[int] | None = None,
) -> str:
    """Read Bronze credits JSON → extract bridge rows → deduplicate → write Silver Parquet.

    For each Bronze credits file, extracts rows of
    (movie_id, person_id, credit_type, role, ordering) for every cast and crew
    member. After deduplication on (movie_id, person_id, credit_type), rows
    with null movie_id or person_id are dropped with a warning. If
    `known_movie_ids` / `known_person_ids` are provided, orphan IDs are flagged
    but rows are kept so downstream tasks can decide.

    Returns the s3:// URI of the written Parquet file.

    Raises FileNotFoundError if no Bronze credits files exist for the given date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver credits-bridge transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze credits files found for ingestion_date={ingestion_date}"
        )
    logger.info("Found %d Bronze JSON file(s) to process", len(keys))

    all_rows: list[dict[str, Any]] = []
    errors = 0

    for key in keys:
        try:
            payload = _read_json_from_s3(bucket, key)
            all_rows.extend(_extract_bridge_rows(payload))
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/parse %s: %s", key, exc)

    if not all_rows:
        raise RuntimeError(
            f"Every Bronze credits file failed to parse for ingestion_date={ingestion_date} — aborting."
        )
    logger.info("Extracted %d raw bridge row(s) from %d file(s)", len(all_rows), len(keys))

    df = pd.DataFrame(all_rows)
    df = _cast_bridge_types(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["movie_id", "person_id", "credit_type"], keep="last")
    dupes = before_dedup - len(df)
    if dupes:
        logger.info("Dropped %d duplicate (movie_id, person_id, credit_type) row(s)", dupes)

    null_movie = df["movie_id"].isna().sum()
    null_person = df["person_id"].isna().sum()
    if null_movie:
        logger.warning("Dropping %d row(s) with null movie_id", null_movie)
    if null_person:
        logger.warning("Dropping %d row(s) with null person_id", null_person)
    df = df.dropna(subset=["movie_id", "person_id"])

    _check_referential_integrity(df, known_movie_ids, known_person_ids)

    output_key = s3_utils.build_path(
        "silver", "credits_bridge", ingestion_date, "credits_bridge.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver credits-bridge transform complete: %d rows written, %d parse errors in %.2fs",
        len(df), errors, elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze credits JSON to Silver credits-bridge Parquet."
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
    setup_logging("transform_credits_bridge")
    args = _parse_args()
    transform_credits_bridge(ingestion_date=args.date)
