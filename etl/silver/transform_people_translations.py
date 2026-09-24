"""Silver transform: Russian and Uzbek biographies.

`GET /person/{id}?append_to_response=translations` (Task 93) carries a
`translations` block beside the biography fields `transform_people_details`
already reads. Same `etl.translations` picker as the film side, one field
(`biography`) instead of three.

**Reads every `bronze/person_details/` partition, not just one date** — for the
same reason `transform_people_details` does: `ingest_people()` writes a person
once, into whichever partition first discovered them, so the cumulative set is
spread across all of history. The result is written to the given date's Silver
partition so the loader still reads one dated file.

**Backfill is not possible from Bronze.** Everyone enriched before Task 93 has a
`person_details` file with no `translations` key. Such a payload yields zero rows
plus one aggregate warning, never an exception; an all-empty result still writes
a well-formed Parquet. Those people gain a Russian biography only if re-fetched
with `python -m etl.bronze.ingest_people --no-skip-existing`, a deliberate run
rather than something the nightly does.

S3 source:  bronze/person_details/ingestion_date=*/<person_id>.json
S3 output:  silver/person_translations/ingestion_date=YYYY-MM-DD/person_translations.parquet

person_translations columns:
    person_id  Int64
    lang       string  — bare ISO-639-1 code, one of etl.translations.TRANSLATION_LANGUAGES
    biography  string  — never null: a language with no biography is not written

Dedup key: (person_id, lang), a later partition winning. Rows with a null
person_id are dropped with a warning, never silently.

Idempotent: running twice for the same date overwrites the same key.

Usage:
    python -m etl.silver.transform_people_translations
    python -m etl.silver.transform_people_translations --date 2026-09-24
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
from etl.translations import select_translations

logger = logging.getLogger(__name__)

_FIELDS = ("biography",)
_COLUMNS = ["person_id", "lang", *_FIELDS]


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


def _extract_translation_rows(raw: dict[str, Any]) -> list[dict[str, Any]] | None:
    """One row per shipped language from a TMDB person-detail payload.

    Returns ``None`` when the payload has no ``translations`` key (enriched
    before Task 93); ``[]`` when it has the block but no Russian/Uzbek biography.
    """
    picked = select_translations(raw, _FIELDS)
    if picked is None:
        return None
    person_id = raw.get("id")
    return [{"person_id": person_id, "lang": lang, **values} for lang, values in picked.items()]


def _cast_translation_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["person_id"] = pd.to_numeric(df["person_id"], errors="coerce").astype("Int64")
    for col in ("lang", *_FIELDS):
        df[col] = df[col].astype("string")
    return df


def transform_people_translations(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read every Bronze person-detail JSON -> write one Silver Parquet.

    Returns the s3:// URI of the person_translations Parquet.

    Raises FileNotFoundError if no Bronze person-detail files exist at all.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver people-translations transform for date=%s", ingestion_date)

    keys = _list_all_bronze_keys(bucket)
    if not keys:
        raise FileNotFoundError(
            "No Bronze person-detail files found under bronze/person_details/"
        )
    logger.info("Found %d Bronze person JSON file(s) across all partitions", len(keys))

    rows: list[dict[str, Any]] = []
    missing_key = 0
    errors = 0
    for key, raw, read_err in s3_utils.read_json_objects(bucket, keys):
        try:
            if read_err is not None:
                raise read_err
            extracted = _extract_translation_rows(raw)
            if extracted is None:
                missing_key += 1
            else:
                rows.extend(extracted)
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/extract %s: %s", key, exc)

    if not rows and errors == len(keys):
        raise RuntimeError(
            "Every Bronze person-detail file failed to parse — aborting."
        )

    if missing_key:
        logger.warning(
            "%d of %d Bronze person payload(s) had no `translations` key — enriched "
            "before Task 93; they gain a Russian biography only via "
            "`ingest_people --no-skip-existing`.",
            missing_key, len(keys),
        )

    df = _cast_translation_types(pd.DataFrame(rows, columns=_COLUMNS))
    before = len(df)
    # A later partition wins if the same person was somehow enriched twice.
    df = df.drop_duplicates(subset=["person_id", "lang"], keep="last")
    if before - len(df):
        logger.info(
            "[person_translations] Dropped %d duplicate row(s) on (person_id, lang)",
            before - len(df),
        )
    n_null = int(df["person_id"].isna().sum())
    if n_null:
        logger.warning("[person_translations] Dropping %d row(s) with null person_id", n_null)
    df = df.dropna(subset=["person_id"])

    output_key = s3_utils.build_path(
        "silver", "person_translations", ingestion_date, "person_translations.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, df)

    per_lang = df["lang"].value_counts().to_dict()
    logger.info(
        "Silver people-translations transform complete: %d row(s) across %d people "
        "(%s), %d payload(s) without a translations key, %d parse error(s) in %.2fs",
        len(df), df["person_id"].nunique(),
        ", ".join(f"{k}={v}" for k, v in sorted(per_lang.items())) or "none",
        missing_key, errors, time.monotonic() - t0,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze person-detail JSON to Silver person-translations Parquet."
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
    setup_logging("transform_people_translations")
    args = _parse_args()
    transform_people_translations(ingestion_date=args.date)
