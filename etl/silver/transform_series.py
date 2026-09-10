"""Silver transform: TV Series.

The TV counterpart of `transform_movies.py`. Reads every Bronze
series-detail JSON file for a given ingestion_date, flattens each payload into
one row per series, casts every field to its target type, deduplicates on
series_id (keeping the last-seen record), and writes a single Parquet file to
the Silver layer.

S3 source:  bronze/series_details/ingestion_date=YYYY-MM-DD/<series_id>.json
S3 output:  silver/series/ingestion_date=YYYY-MM-DD/series.parquet

series.parquet columns (grain: series_id):
    series_id            Int64
    name                 string
    original_name        string
    first_air_date       date    — nullable, unparseable coerced to null
    last_air_date        date    — nullable
    number_of_seasons    Int64
    number_of_episodes   Int64
    status               string  — e.g. "Ended", "Returning Series"
    type                 string  — e.g. "Scripted", "Documentary"
    in_production        boolean
    original_language    string
    overview             string  — nullable, TMDB "" normalised to None
    tagline              string  — nullable, "" -> None
    poster_path          string  — nullable, "" -> None
    backdrop_path        string  — nullable, "" -> None
    homepage             string  — nullable, "" -> None
    imdb_id              string  — nullable; from the inline `external_ids` block
    vote_average         float   — TMDB's own rating, 0.0-10.0 (Task 81)
    vote_count           Int64   — TMDB vote count (Task 81)

No `slug` column. Slugs are assigned in the warehouse loader by
`assign_slugs()` over the whole `dim_series` table (Task 79), exactly as
`dim_movie` / `dim_person` slugs are — the collision-suffix rule needs the
full-table view, which a single day's Silver partition does not have. This is
where `transform_movies.py` also leaves it.

No `episode_run_time`: TMDB returns it empty for many shows (measured empty on
Breaking Bad). The real per-episode runtime comes from the season endpoint in
Task 82.

Rows with a null series_id are dropped with a warning, never silently — the
same convention as every other Silver transform.

Idempotent: running twice for the same date overwrites the same key with the
same content.

Usage:
    python -m etl.silver.transform_series
    python -m etl.silver.transform_series --date 2026-09-09
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from typing import Any

import pandas as pd

import config
from etl import s3_utils

logger = logging.getLogger(__name__)


def _list_bronze_keys(bucket: str, ingestion_date: dt.date) -> list[str]:
    """Return every .json key under the bronze/series_details partition for this date."""
    prefix = s3_utils.build_path("bronze", "series_details", ingestion_date, "")
    client = s3_utils.get_s3_client()
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def _flatten_series(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract and rename the fields we keep from one TMDB series-detail payload.

    `imdb_id` lives in the inline `external_ids` block (appended by
    `ingest_series_details` — Task 77). TMDB returns "" for a missing
    tagline / image path / homepage and null for a missing imdb_id; both
    normalise to None here.
    """
    external_ids = raw.get("external_ids") or {}
    return {
        "series_id": raw.get("id"),
        "name": raw.get("name"),
        "original_name": raw.get("original_name"),
        "first_air_date": raw.get("first_air_date") or None,
        "last_air_date": raw.get("last_air_date") or None,
        "number_of_seasons": raw.get("number_of_seasons"),
        "number_of_episodes": raw.get("number_of_episodes"),
        "status": raw.get("status"),
        "type": raw.get("type"),
        "in_production": raw.get("in_production"),
        "original_language": raw.get("original_language"),
        "overview": raw.get("overview") or None,
        "tagline": raw.get("tagline") or None,
        "poster_path": raw.get("poster_path") or None,
        "backdrop_path": raw.get("backdrop_path") or None,
        "homepage": raw.get("homepage") or None,
        "imdb_id": external_ids.get("imdb_id") or None,
        # Task 81: TMDB's own rating, kept beside the IMDb one for comparison —
        # the same two figures transform_movies.py carries for films.
        "vote_average": raw.get("vote_average"),
        "vote_count": raw.get("vote_count"),
    }


def _cast_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast every column to its intended type; bad values become NaN/NaT, not crashes."""
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    df["number_of_seasons"] = pd.to_numeric(
        df["number_of_seasons"], errors="coerce"
    ).astype("Int64")
    df["number_of_episodes"] = pd.to_numeric(
        df["number_of_episodes"], errors="coerce"
    ).astype("Int64")
    df["vote_average"] = pd.to_numeric(df["vote_average"], errors="coerce")
    df["vote_count"] = pd.to_numeric(df["vote_count"], errors="coerce").astype("Int64")
    df["in_production"] = df["in_production"].astype("boolean")
    # Empty strings from TMDB for missing dates become NaT, not errors.
    df["first_air_date"] = pd.to_datetime(df["first_air_date"], errors="coerce").dt.date
    df["last_air_date"] = pd.to_datetime(df["last_air_date"], errors="coerce").dt.date
    for col in (
        "name", "original_name", "status", "type", "original_language",
        "overview", "tagline", "poster_path", "backdrop_path", "homepage", "imdb_id",
    ):
        df[col] = df[col].astype("string")
    return df


_COLUMNS = [
    "series_id", "name", "original_name", "first_air_date", "last_air_date",
    "number_of_seasons", "number_of_episodes", "status", "type", "in_production",
    "original_language", "overview", "tagline", "poster_path", "backdrop_path",
    "homepage", "imdb_id", "vote_average", "vote_count",
]


def transform_series(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Bronze series-detail JSON → clean → deduplicate → write Silver Parquet.

    Returns the s3:// URI of the written Parquet file.

    Raises FileNotFoundError if no Bronze files exist for the given date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver series transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze series-detail files found for ingestion_date={ingestion_date}"
        )
    logger.info("Found %d Bronze JSON file(s) to process", len(keys))

    rows: list[dict[str, Any]] = []
    errors = 0
    for key, raw, read_err in s3_utils.read_json_objects(bucket, keys):
        try:
            if read_err is not None:
                raise read_err
            rows.append(_flatten_series(raw))
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/flatten %s: %s", key, exc)

    if not rows:
        raise RuntimeError(
            f"Every Bronze file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    df = pd.DataFrame(rows, columns=_COLUMNS)
    df = _cast_types(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["series_id"], keep="last")
    dupes_dropped = before_dedup - len(df)
    if dupes_dropped:
        logger.info("Dropped %d duplicate series_id row(s)", dupes_dropped)

    null_ids = df["series_id"].isna().sum()
    if null_ids:
        logger.warning("Dropping %d row(s) with null series_id", null_ids)
        df = df.dropna(subset=["series_id"])

    output_key = s3_utils.build_path("silver", "series", ingestion_date, "series.parquet")
    uri = s3_utils.write_parquet(bucket, output_key, df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver series transform complete: %d rows written, %d parse errors in %.2fs",
        len(df), errors, elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze series-detail JSON to Silver Parquet."
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
    setup_logging("transform_series")
    args = _parse_args()
    transform_series(ingestion_date=args.date)
