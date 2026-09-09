"""Silver transform: series credits (and the people they introduce).

The TV counterpart of `transform_credits_bridge.py` + `transform_people.py`
rolled into one Bronze pass. Reads every Bronze series-detail JSON file for a
given ingestion_date (the same source `transform_series.py` reads) and pulls
apart the inline `aggregate_credits` block.

`aggregate_credits` is shaped differently from a movie's `credits`: instead of
one row per (person, job), TMDB collapses a person to a single object carrying
a `roles[]` array (cast) or a `jobs[]` array (crew), each entry a distinct
(character | job, episode_count) pair. **Flattening those arrays is the whole
job of this transform.** A cast member with three characters becomes three
rows; a crew member with two jobs becomes two.

S3 source:  bronze/series_details/ingestion_date=YYYY-MM-DD/<series_id>.json
S3 output:  silver/series_credits/ingestion_date=YYYY-MM-DD/series_credits.parquet
            silver/series_people/ingestion_date=YYYY-MM-DD/series_people.parquet

series_credits columns  (grain: series_id, person_id, department, job, character_name):
    series_id       Int64
    person_id       Int64
    department      string  — "Acting" for cast; the crew member's own
                              department otherwise (Directing, Writing, …) —
                              matching `fact_credit`'s convention exactly
    job             string  — "Actor" for cast; the crew job title otherwise
    character_name  string  — the part played for cast; "" for crew. Coalesced
                              to "" (never null) because it is in the grain and
                              `fact_series_credit`'s PK cannot hold a null.
    episode_count   Int64   — the measure: how many episodes this person did in
                              this role/job. `fact_series_credit` is the first
                              credit table in the warehouse with a real measure.
    ordering        Int64   — cast billing order (null for crew)

series_people columns  (grain: person_id) — the person-identity hand-off:
    person_id, name, gender, popularity, profile_path, known_for_department
    The same columns `silver/people` carries. `transform_people` reads this
    file (under --with-tv) and folds these rows into its own person dedupe, so
    a TV-only actor gets a `dim_person` row and then flows through the existing
    `GET /person/{id}` bio enrichment with no further change — TMDB has one
    person namespace across film and TV.

Rows with a null series_id / person_id, or an empty department/job, are dropped
with a warning — never silently. `character_name` is never a drop reason; it is
coalesced to "".

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_series_credits
    python -m etl.silver.transform_series_credits --date 2026-09-09
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

CREDIT_COLUMNS = [
    "series_id", "person_id", "department", "job", "character_name",
    "episode_count", "ordering",
]
PEOPLE_COLUMNS = [
    "person_id", "name", "gender", "popularity", "profile_path", "known_for_department",
]
_CREDIT_GRAIN = ["series_id", "person_id", "department", "job", "character_name"]


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


def _extract_credit_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten one series payload's `aggregate_credits` into credit rows.

    One row per entry in each cast member's `roles[]` and each crew member's
    `jobs[]`. A member with an empty/missing array still yields one row (with
    an empty character/job) so the person is not lost from the fact — that row
    is dropped downstream only if `job` ends up empty, which for cast it never
    does ("Actor").
    """
    series_id = payload.get("id")
    agg = payload.get("aggregate_credits") or {}
    rows: list[dict[str, Any]] = []

    for member in agg.get("cast") or []:
        person_id = member.get("id")
        order = member.get("order")
        for role in member.get("roles") or [{}]:
            rows.append({
                "series_id": series_id,
                "person_id": person_id,
                # TMDB gives aggregate cast no department/job — one craft, like movies.
                "department": "Acting",
                "job": "Actor",
                "character_name": role.get("character") or "",
                "episode_count": role.get("episode_count"),
                "ordering": order,
            })

    for member in agg.get("crew") or []:
        person_id = member.get("id")
        member_department = member.get("department")
        for job in member.get("jobs") or [{}]:
            rows.append({
                "series_id": series_id,
                "person_id": person_id,
                "department": job.get("department") or member_department,
                "job": job.get("job"),
                "character_name": "",
                "episode_count": job.get("episode_count"),
                "ordering": None,
            })

    return rows


def _count_role_entries(payload: dict[str, Any]) -> int:
    """How many (role | job) entries this payload's aggregate_credits holds.

    The number `_extract_credit_rows` must produce for this payload before
    dedup (a member with no array counts as 1 — the fallback row). If the two
    ever disagree the flatten is broken and the run says so loudly.
    """
    agg = payload.get("aggregate_credits") or {}
    total = 0
    for member in agg.get("cast") or []:
        total += max(len(member.get("roles") or []), 1)
    for member in agg.get("crew") or []:
        total += max(len(member.get("jobs") or []), 1)
    return total


def _extract_people_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """One identity row per credited person in this series payload, cast and crew."""
    agg = payload.get("aggregate_credits") or {}
    rows: list[dict[str, Any]] = []
    for member in (*(agg.get("cast") or []), *(agg.get("crew") or [])):
        rows.append({
            "person_id": member.get("id"),
            "name": member.get("name"),
            "gender": member.get("gender"),
            "popularity": member.get("popularity"),
            "profile_path": member.get("profile_path") or None,
            "known_for_department": member.get("known_for_department") or None,
        })
    return rows


def _cast_credit_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast columns to intended types; bad values become NaN, not crashes."""
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    df["person_id"] = pd.to_numeric(df["person_id"], errors="coerce").astype("Int64")
    df["department"] = df["department"].astype("string")
    df["job"] = df["job"].astype("string")
    df["character_name"] = df["character_name"].fillna("").astype("string")
    df["episode_count"] = pd.to_numeric(df["episode_count"], errors="coerce").astype("Int64")
    df["ordering"] = pd.to_numeric(df["ordering"], errors="coerce").astype("Int64")
    return df


def _cast_people_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["person_id"] = pd.to_numeric(df["person_id"], errors="coerce").astype("Int64")
    df["gender"] = pd.to_numeric(df["gender"], errors="coerce").astype("Int64")
    df["popularity"] = pd.to_numeric(df["popularity"], errors="coerce")
    df["name"] = df["name"].astype("string")
    df["profile_path"] = df["profile_path"].astype("string")
    df["known_for_department"] = df["known_for_department"].astype("string")
    return df


def transform_series_credits(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> tuple[str, str]:
    """Read Bronze series-detail JSON → flatten aggregate_credits → write two Silver Parquets.

    Returns (series_credits_uri, series_people_uri).

    Raises FileNotFoundError if no Bronze series-detail files exist for the date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver series-credits transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze series-detail files found for ingestion_date={ingestion_date}"
        )
    logger.info("Found %d Bronze JSON file(s) to process", len(keys))

    credit_rows: list[dict[str, Any]] = []
    people_rows: list[dict[str, Any]] = []
    expected_role_entries = 0
    errors = 0

    for key, payload, read_err in s3_utils.read_json_objects(bucket, keys):
        try:
            if read_err is not None:
                raise read_err
            credit_rows.extend(_extract_credit_rows(payload))
            people_rows.extend(_extract_people_rows(payload))
            expected_role_entries += _count_role_entries(payload)
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/parse %s: %s", key, exc)

    if errors == len(keys):
        raise RuntimeError(
            f"Every Bronze series-detail file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    # Loud guard (task step 2): flattening must produce exactly one row per
    # roles[]/jobs[] entry. Fewer means the arrays were collapsed and the grain
    # is wrong — the whole point of this transform.
    if len(credit_rows) < expected_role_entries:
        logger.error(
            "Flatten LOST rows: %d credit row(s) from %d roles[]/jobs[] entrie(s) — "
            "the (series, person, department, job, character) grain is wrong",
            len(credit_rows), expected_role_entries,
        )
    else:
        logger.info(
            "Flattened %d roles[]/jobs[] entrie(s) into %d credit row(s)",
            expected_role_entries, len(credit_rows),
        )

    df = pd.DataFrame(credit_rows, columns=CREDIT_COLUMNS)
    df = _cast_credit_types(df)

    # Multi-role cast — the figure the phase preamble measured at 6.7%.
    cast = df[df["department"] == "Acting"]
    n_cast_people = cast["person_id"].nunique()
    roles_per_person = cast.groupby("person_id")["character_name"].nunique()
    n_multi_role = int((roles_per_person > 1).sum())
    if n_cast_people:
        logger.info(
            "Multi-character cast: %d / %d (%.1f%%) hold more than one character in one series",
            n_multi_role, n_cast_people, 100 * n_multi_role / n_cast_people,
        )

    before_dedup = len(df)
    df = df.drop_duplicates(subset=_CREDIT_GRAIN, keep="last")
    dupes = before_dedup - len(df)
    if dupes:
        logger.info("Dropped %d duplicate row(s) on %s", dupes, _CREDIT_GRAIN)

    null_ids = df["series_id"].isna() | df["person_id"].isna()
    n_null_ids = int(null_ids.sum())
    if n_null_ids:
        logger.warning("Dropping %d row(s) with null series_id/person_id", n_null_ids)
        df = df[~null_ids]

    empty_job = df["job"].isna() | (df["job"].str.len() == 0)
    n_empty_job = int(empty_job.sum())
    if n_empty_job:
        logger.warning("Dropping %d credit row(s) with an empty job", n_empty_job)
        df = df[~empty_job]

    credits_key = s3_utils.build_path(
        "silver", "series_credits", ingestion_date, "series_credits.parquet"
    )
    credits_uri = s3_utils.write_parquet(bucket, credits_key, df)

    # --- series_people: dedupe on person_id, same shape as silver/people ---
    people_df = pd.DataFrame(people_rows, columns=PEOPLE_COLUMNS)
    people_df = _cast_people_types(people_df)
    before = len(people_df)
    people_df = people_df.drop_duplicates(subset=["person_id"], keep="last")
    if before - len(people_df):
        logger.info("series_people: collapsed %d repeat person_id row(s)", before - len(people_df))
    null_person = people_df["person_id"].isna().sum()
    if null_person:
        logger.warning("series_people: dropping %d row(s) with null person_id", int(null_person))
        people_df = people_df.dropna(subset=["person_id"])

    people_key = s3_utils.build_path(
        "silver", "series_people", ingestion_date, "series_people.parquet"
    )
    people_uri = s3_utils.write_parquet(bucket, people_key, people_df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver series-credits transform complete: %d credit row(s), %d people, "
        "%d parse errors in %.2fs",
        len(df), len(people_df), errors, elapsed,
    )
    return credits_uri, people_uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze series-detail JSON to Silver series-credits Parquet."
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
    setup_logging("transform_series_credits")
    args = _parse_args()
    transform_series_credits(ingestion_date=args.date)
