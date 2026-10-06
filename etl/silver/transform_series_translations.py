"""Silver transform: Russian and Uzbek name/overview/tagline for every show.

The TV counterpart of `transform_movie_translations.py`. `GET /tv/{id}` gains a
nested `"translations"` block when called with `append_to_response=translations`
(Task 109) — one entry per language TMDB has text in, each `data` object
carrying `name`, `overview`, `tagline`. It rides inside the payload
`bronze/series_details` already holds, so this reads it straight out of there
rather than giving it a Bronze entity of its own. `etl.translations` does the
picking; the Bronze writer has already trimmed the block to the shipped
languages, and `select_translations()` filters again here so a hand-fed or older
payload cannot leak a third language into the warehouse.

**Backfill needs no special pass.** Bronze is immutable and every
`series_details` partition written before Task 109 has no `translations` key —
but the nightly refresh re-fetches every show in `dim_series`, so the first
refresh after this ships writes a fresh partition carrying the block for all of
them. A payload without the key yields zero rows plus one aggregate warning,
never an exception, and a partition where no file carries the key still writes a
well-formed empty Parquet so the loader always has something to read.

S3 source:  bronze/series_details/ingestion_date=YYYY-MM-DD/<series_id>.json
S3 output:  silver/series_translations/ingestion_date=YYYY-MM-DD/series_translations.parquet

series_translations columns:
    series_id  Int64   — TMDB series ID
    lang       string  — bare ISO-639-1 code, one of etl.translations.TRANSLATION_LANGUAGES
    name       string  — nullable
    overview   string  — nullable, "" normalised to None
    tagline    string  — nullable, "" normalised to None

Dedup key: (series_id, lang). A language whose name, overview and tagline are
all empty is not written. Rows with a null series_id are dropped with a warning,
never silently.

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_series_translations
    python -m etl.silver.transform_series_translations --date 2026-10-06
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

_FIELDS = ("name", "overview", "tagline")
_COLUMNS = ["series_id", "lang", *_FIELDS]


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


def _extract_translation_rows(raw: dict[str, Any]) -> list[dict[str, Any]] | None:
    """One row per shipped language from a TMDB series-detail payload.

    Returns ``None`` when the payload has no ``translations`` key at all — a
    partition written before Task 109. That is distinct from a show with a
    ``translations`` block but no Russian or Uzbek text, which returns ``[]``.
    """
    picked = select_translations(raw, _FIELDS)
    if picked is None:
        return None
    series_id = raw.get("id")
    return [{"series_id": series_id, "lang": lang, **values} for lang, values in picked.items()]


def _cast_translation_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    for col in ("lang", *_FIELDS):
        df[col] = df[col].astype("string")
    return df


def transform_series_translations(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Bronze series-detail JSON -> extract translations -> write Silver.

    Returns the s3:// URI of the series_translations Parquet.

    Raises FileNotFoundError if no Bronze series-detail files exist for the date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver series-translations transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze series-detail files found for ingestion_date={ingestion_date}"
        )
    logger.info("Found %d Bronze JSON file(s) to process", len(keys))

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
            f"Every Bronze file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    if missing_key:
        logger.warning(
            "%d of %d Bronze payload(s) had no `translations` key — written before "
            "Task 109 appended `append_to_response=translations`; those shows "
            "contribute no translation rows this partition.",
            missing_key, len(keys),
        )

    # columns= keeps an all-empty partition (every payload pre-Task-109) producing
    # a well-formed empty Parquet rather than a column-less frame.
    df = _cast_translation_types(pd.DataFrame(rows, columns=_COLUMNS))

    before = len(df)
    df = df.drop_duplicates(subset=["series_id", "lang"], keep="last")
    if before - len(df):
        logger.info(
            "[series_translations] Dropped %d duplicate row(s) on (series_id, lang)",
            before - len(df),
        )

    n_null = int(df["series_id"].isna().sum())
    if n_null:
        logger.warning("[series_translations] Dropping %d row(s) with null series_id", n_null)
    df = df.dropna(subset=["series_id"])

    output_key = s3_utils.build_path(
        "silver", "series_translations", ingestion_date, "series_translations.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, df)

    per_lang = df["lang"].value_counts().to_dict()
    logger.info(
        "Silver series-translations transform complete: %d row(s) across %d show(s) "
        "(%s), %d payload(s) without a translations key, %d parse error(s) in %.2fs",
        len(df), df["series_id"].nunique(),
        ", ".join(f"{k}={v}" for k, v in sorted(per_lang.items())) or "none",
        missing_key, errors, time.monotonic() - t0,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze series-detail JSON to Silver series-translations Parquet."
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
    setup_logging("transform_series_translations")
    args = _parse_args()
    transform_series_translations(ingestion_date=args.date)
