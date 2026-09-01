"""Silver transform: person detail fields and aliases.

Reads the Bronze person-detail JSON files and writes two denormalised long
tables:

    silver/person_details/person_details.parquet — one row per person_id:
        person_id       Int64
        biography       string  — nullable, TMDB "" normalised to None
        birthday        date    — nullable, unparseable dates coerced to null
        deathday        date    — nullable, ditto
        place_of_birth  string  — nullable, "" -> None
        homepage        string  — nullable, "" -> None
        imdb_id         string  — nullable (TMDB returns null, not "")

    silver/person_aliases/person_aliases.parquet — one row per (person_id, alias):
        person_id  Int64
        alias      string
        ordering   Int64   — index of the alias in TMDB's also_known_as list

**Reads every `bronze/person_details/` partition, not just one date.** Same
reasoning as transform_companies.py: a person's bio / birthday / place of
birth essentially never change, so ingest_people() only ever writes a *new*
person's file, into whatever partition first discovered them. The cumulative
enriched set is spread across every partition, and this transform sweeps all
of them. The result is written to the given date's Silver partition so the
warehouse loader still reads one dated file like every other source.

Date handling: `birthday` / `deathday` are parsed with `errors="coerce"`, so
a value TMDB somehow returns in a non-ISO shape becomes null (logged with a
count) rather than crashing the run — the row is kept, since its biography /
place of birth are still worth loading. The 200-person probe found every date
already clean, so this only ever fires on an outlier.

Rows with a null person_id are dropped with a warning, never silently.

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_people_details
    python -m etl.silver.transform_people_details --date 2026-06-22
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


def _list_all_bronze_keys(bucket: str) -> list[str]:
    """Every .json key under bronze/person_details/, across all ingestion_dates."""
    client = s3_utils.get_s3_client()
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="bronze/person_details/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def _extract_detail_row(raw: dict[str, Any]) -> dict[str, Any]:
    """One person_details row from a TMDB /person/{id} payload.

    `biography`/`homepage` come back as "" when empty (never omitted);
    `birthday`/`deathday`/`place_of_birth`/`imdb_id` come back as null. `or None`
    followed by `.strip() or None` collapses "" (and whitespace-only) to None,
    the same normalisation Tasks 36/55/65 use.
    """
    return {
        "person_id": raw.get("id"),
        "biography": (raw.get("biography") or "").strip() or None,
        "birthday": (raw.get("birthday") or "").strip() or None,
        "deathday": (raw.get("deathday") or "").strip() or None,
        "place_of_birth": (raw.get("place_of_birth") or "").strip() or None,
        "homepage": (raw.get("homepage") or "").strip() or None,
        "imdb_id": (raw.get("imdb_id") or "").strip() or None,
    }


def _extract_alias_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Zero or more person_aliases rows from one payload's `also_known_as` list.

    `ordering` is the alias's index in TMDB's list, kept so a reader can show
    them in the order TMDB does. Blank/whitespace-only entries are skipped.
    """
    person_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for i, alias in enumerate(raw.get("also_known_as") or []):
        text = (alias or "").strip() if isinstance(alias, str) else ""
        if not text:
            continue
        rows.append({"person_id": person_id, "alias": text, "ordering": i})
    return rows


_DETAIL_COLUMNS = [
    "person_id", "biography", "birthday", "deathday",
    "place_of_birth", "homepage", "imdb_id",
]
_ALIAS_COLUMNS = ["person_id", "alias", "ordering"]


def _cast_detail_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["person_id"] = pd.to_numeric(df["person_id"], errors="coerce").astype("Int64")
    for col in ("biography", "place_of_birth", "homepage", "imdb_id"):
        df[col] = df[col].astype("string")

    for col in ("birthday", "deathday"):
        parsed = pd.to_datetime(df[col], errors="coerce", format="ISO8601")
        unparsed = int((df[col].notna() & parsed.isna()).sum())
        if unparsed:
            logger.warning(
                "%s: %d value(s) did not parse as a date, coerced to null", col, unparsed
            )
        df[col] = parsed.dt.date
    return df


def _cast_alias_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["person_id"] = pd.to_numeric(df["person_id"], errors="coerce").astype("Int64")
    df["alias"] = df["alias"].astype("string")
    df["ordering"] = pd.to_numeric(df["ordering"], errors="coerce").astype("Int64")
    return df


def transform_people_details(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> tuple[str, str]:
    """Read every Bronze person-detail JSON -> write two Silver Parquet files.

    Returns (person_details_uri, person_aliases_uri).

    Raises FileNotFoundError if no Bronze person-detail files exist at all.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver people-details transform for date=%s", ingestion_date)

    keys = _list_all_bronze_keys(bucket)
    if not keys:
        raise FileNotFoundError(
            "No Bronze person-detail files found under bronze/person_details/"
        )
    logger.info("Found %d Bronze person JSON file(s) across all partitions", len(keys))

    detail_rows: list[dict[str, Any]] = []
    alias_rows: list[dict[str, Any]] = []
    errors = 0
    for key, raw, read_err in s3_utils.read_json_objects(bucket, keys):
        try:
            if read_err is not None:
                raise read_err
            detail_rows.append(_extract_detail_row(raw))
            alias_rows.extend(_extract_alias_rows(raw))
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/extract %s: %s", key, exc)

    if not detail_rows and errors == len(keys):
        raise RuntimeError(
            "Every Bronze person-detail file failed to parse — aborting."
        )

    details = _cast_detail_types(pd.DataFrame(detail_rows, columns=_DETAIL_COLUMNS))
    before = len(details)
    # A later partition wins if the same person was somehow enriched twice.
    details = details.drop_duplicates(subset=["person_id"], keep="last")
    if before - len(details):
        logger.info("Dropped %d duplicate person_id row(s)", before - len(details))
    n_null = int(details["person_id"].isna().sum())
    if n_null:
        logger.warning("Dropping %d detail row(s) with null person_id", n_null)
    details = details.dropna(subset=["person_id"])

    aliases = _cast_alias_types(pd.DataFrame(alias_rows, columns=_ALIAS_COLUMNS))
    a_null = int((aliases["person_id"].isna() | aliases["alias"].isna()).sum())
    if a_null:
        logger.warning("Dropping %d alias row(s) with null person_id or alias", a_null)
    aliases = aliases.dropna(subset=["person_id", "alias"])
    before_a = len(aliases)
    aliases = aliases.drop_duplicates(subset=["person_id", "alias"], keep="first")
    if before_a - len(aliases):
        logger.info("Dropped %d duplicate (person_id, alias) row(s)", before_a - len(aliases))

    details_key = s3_utils.build_path(
        "silver", "person_details", ingestion_date, "person_details.parquet"
    )
    aliases_key = s3_utils.build_path(
        "silver", "person_aliases", ingestion_date, "person_aliases.parquet"
    )
    details_uri = s3_utils.write_parquet(bucket, details_key, details)
    aliases_uri = s3_utils.write_parquet(bucket, aliases_key, aliases)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver people-details transform complete: %d person row(s) "
        "(%d with a bio, %d with a birthday), %d alias row(s) across %d people, "
        "%d parse error(s) in %.2fs",
        len(details), int(details["biography"].notna().sum()),
        int(details["birthday"].notna().sum()),
        len(aliases), aliases["person_id"].nunique(), errors, elapsed,
    )
    return details_uri, aliases_uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze person-detail JSON to Silver Parquet."
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
    setup_logging("transform_people_details")
    args = _parse_args()
    transform_people_details(ingestion_date=args.date)
