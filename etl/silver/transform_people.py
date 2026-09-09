"""Silver transform: People.

Reads all Bronze credits JSON files for a given ingestion_date, standardizes and
casts types, deduplicates on person_id, and writes Parquet files to the Silver
layer.

S3 source:    bronze/credits/ingestion_date=YYYY-MM-DD/<movie_id>.json
S3 outputs:
    silver/people/ingestion_date=YYYY-MM-DD/people.parquet

One row per distinct person holding *any* credit, cast or crew, in any
department.

Why one dataset and not three: the previous split defined a person by the credit
that happened to introduce them — cast members became "actors", and crew members
became "directors" only if job == "Director". That filter silently decided who
existed at all, and it excluded roughly 79,500 people (editors, composers,
cinematographers, writers, production designers) whose credits were already
ingested and sitting in Bronze. Identity belongs to the person; what they did on
a given film belongs to the credits bridge.

Idempotent: running twice for the same date overwrites the same keys with
the same content.

Usage:
    python -m etl.silver.transform_people
    python -m etl.silver.transform_people --date 2026-06-22
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import logging
import time
from typing import Any

import pandas as pd

import config
from etl import s3_utils

logger = logging.getLogger(__name__)

PEOPLE_COLUMNS = [
    "person_id", "name", "gender", "popularity", "profile_path", "known_for_department",
]


def _read_series_people(bucket: str, ingestion_date: dt.date) -> pd.DataFrame | None:
    """Read this partition's silver/series_people Parquet, or None if it is absent.

    Written by `transform_series_credits` (Task 80) under --with-tv only. A
    movie-only run never produces it, so the caller degrades to "no TV people"
    rather than crashing — the same posture the warehouse loaders take for
    every optional TV Silver source.
    """
    key = s3_utils.build_path("silver", "series_people", ingestion_date, "series_people.parquet")
    try:
        response = s3_utils.get_s3_client().get_object(Bucket=bucket, Key=key)
        return pd.read_parquet(io.BytesIO(response["Body"].read()))
    except Exception as exc:
        logger.info("No Silver series_people for %s (%s) — TV people not folded in", ingestion_date, exc)
        return None


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


def _person_row(member: dict[str, Any]) -> dict[str, Any]:
    """Map one TMDB cast/crew member object to a person row.

    Both arrays carry the same person fields, so identity is extracted the same
    way regardless of which credit introduced the person.
    """
    return {
        "person_id": member.get("id"),
        "name": member.get("name"),
        "gender": member.get("gender"),
        "popularity": member.get("popularity"),
        "profile_path": member.get("profile_path") or None,
        "known_for_department": member.get("known_for_department") or None,
    }


def _extract_people(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one row per credited person — cast and crew alike — from a payload.

    No job or department filter: a person is a person whether they acted, shot,
    cut, scored or designed the film. Which of those they did is recorded once,
    per film, in the credits bridge — not baked into which table they land in.
    """
    return [
        _person_row(member)
        for member in (*payload.get("cast", []), *payload.get("crew", []))
    ]


def _cast_people_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast columns to intended types; bad values become NaN, not crashes."""
    df = df.copy()
    df["person_id"] = pd.to_numeric(df["person_id"], errors="coerce").astype("Int64")
    df["gender"] = pd.to_numeric(df["gender"], errors="coerce").astype("Int64")
    df["popularity"] = pd.to_numeric(df["popularity"], errors="coerce")
    if "known_for_department" in df.columns:
        df["known_for_department"] = df["known_for_department"].astype("string")
    return df


def _dedupe_people(rows: list[dict[str, Any]], label: str, columns: list[str]) -> pd.DataFrame:
    """Build a typed, person_id-unique frame from raw person rows.

    The same person appears once per film they are credited on, so deduplication
    here is expected and large — it is not a sign of bad input.
    """
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
    df = _cast_people_types(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["person_id"], keep="last")
    dupes = before_dedup - len(df)
    if dupes:
        logger.info("%s: collapsed %d repeat person_id row(s)", label, dupes)

    null_ids = df["person_id"].isna().sum()
    if null_ids:
        logger.warning("%s: dropping %d row(s) with null person_id", label, null_ids)
        df = df.dropna(subset=["person_id"])

    return df


def transform_people(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    with_tv: bool = False,
) -> str:
    """Read Bronze credits JSON → extract every credited person → write Silver Parquet.

    Reads every .json file from the bronze/credits partition for `ingestion_date`,
    extracts one identity row per cast and crew member, casts fields to target
    types, deduplicates on person_id (keeping last-seen record), and writes
    silver/people/ingestion_date=YYYY-MM-DD/people.parquet.

    With `with_tv=True` the identity rows from this partition's
    silver/series_people (written by `transform_series_credits`) are folded in
    before the dedupe, so a person who only ever worked on a TV show still gets
    a row here — and therefore a `dim_person` row, and the existing
    `GET /person/{id}` bio enrichment, unchanged. TMDB has one person namespace
    across film and TV, so a person on both a film and a show collapses to one
    row on `person_id` exactly as two film credits already do.

    Returns the s3:// URI of the written Parquet file.

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

    people_rows: list[dict[str, Any]] = []
    errors = 0

    for key, payload, read_err in s3_utils.read_json_objects(bucket, keys):
        try:
            if read_err is not None:
                raise read_err
            people_rows.extend(_extract_people(payload))
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/parse %s: %s", key, exc)

    if not people_rows:
        raise RuntimeError(
            f"Every Bronze credits file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    if with_tv:
        series_people = _read_series_people(bucket, ingestion_date)
        if series_people is not None and not series_people.empty:
            tv_rows = series_people[PEOPLE_COLUMNS].to_dict("records")
            logger.info("Folding in %d TV person row(s) from silver/series_people", len(tv_rows))
            people_rows.extend(tv_rows)

    df_people = _dedupe_people(people_rows, "People", PEOPLE_COLUMNS)
    people_key = s3_utils.build_path("silver", "people", ingestion_date, "people.parquet")
    people_uri = s3_utils.write_parquet(bucket, people_key, df_people)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver people transform complete: %d people written, %d parse errors in %.2fs",
        len(df_people), errors, elapsed,
    )
    return people_uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze credits JSON to a Silver people Parquet."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--with-tv",
        action="store_true",
        help="Also fold in this partition's silver/series_people rows (Task 80).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("transform_people")
    args = _parse_args()
    transform_people(ingestion_date=args.date, with_tv=args.with_tv)
