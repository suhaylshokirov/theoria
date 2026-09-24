"""Silver transform: Russian and Uzbek title/overview/tagline for every film.

`GET /movie/{id}` gains a nested `"translations"` block when called with
`append_to_response=translations` (Task 93) — one entry per language TMDB has
text in. It rides inside the payload `bronze/movie_details` already holds, so
like `transform_movie_videos` this reads it straight out of there rather than
giving it a Bronze entity of its own. `etl.translations` does the picking; the
Bronze writers have already trimmed the block to the shipped languages, and
`select_translations()` filters again here so a hand-fed or older payload
cannot leak a third language into the warehouse.

**Backfill is not possible.** Bronze is immutable, and every `movie_details`
partition written before Task 93 has no `translations` key. Such a payload
yields zero rows plus one aggregate warning, never an exception, and a
partition where no file carries the key still writes a well-formed empty
Parquet so the loader always has something to read.

S3 source:  bronze/movie_details/ingestion_date=YYYY-MM-DD/<movie_id>.json
S3 output:  silver/movie_translations/ingestion_date=YYYY-MM-DD/movie_translations.parquet

movie_translations columns:
    movie_id  Int64   — TMDB movie ID
    lang      string  — bare ISO-639-1 code, one of etl.translations.TRANSLATION_LANGUAGES
    title     string  — nullable
    overview  string  — nullable, "" normalised to None
    tagline   string  — nullable, "" normalised to None

Dedup key: (movie_id, lang). A language whose title, overview and tagline are
all empty is not written. Rows with a null movie_id are dropped with a warning,
never silently.

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_movie_translations
    python -m etl.silver.transform_movie_translations --date 2026-09-24
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

_FIELDS = ("title", "overview", "tagline")
_COLUMNS = ["movie_id", "lang", *_FIELDS]


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


def _extract_translation_rows(raw: dict[str, Any]) -> list[dict[str, Any]] | None:
    """One row per shipped language from a TMDB movie-detail payload.

    Returns ``None`` when the payload has no ``translations`` key at all — a
    partition written before Task 93. That is distinct from a film with a
    ``translations`` block but no Russian or Uzbek text, which returns ``[]``.
    """
    picked = select_translations(raw, _FIELDS)
    if picked is None:
        return None
    movie_id = raw.get("id")
    return [{"movie_id": movie_id, "lang": lang, **values} for lang, values in picked.items()]


def _cast_translation_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["movie_id"] = pd.to_numeric(df["movie_id"], errors="coerce").astype("Int64")
    for col in ("lang", *_FIELDS):
        df[col] = df[col].astype("string")
    return df


def transform_movie_translations(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Bronze movie-detail JSON -> extract translations -> write Silver.

    Returns the s3:// URI of the movie_translations Parquet.

    Raises FileNotFoundError if no Bronze movie-detail files exist for the date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver movie-translations transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze movie-detail files found for ingestion_date={ingestion_date}"
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
            "Task 93 appended `append_to_response=translations`; those films "
            "contribute no translation rows this partition.",
            missing_key, len(keys),
        )

    # columns= keeps an all-empty partition (every payload pre-Task-93) producing
    # a well-formed empty Parquet rather than a column-less frame.
    df = _cast_translation_types(pd.DataFrame(rows, columns=_COLUMNS))

    before = len(df)
    df = df.drop_duplicates(subset=["movie_id", "lang"], keep="last")
    if before - len(df):
        logger.info(
            "[movie_translations] Dropped %d duplicate row(s) on (movie_id, lang)",
            before - len(df),
        )

    n_null = int(df["movie_id"].isna().sum())
    if n_null:
        logger.warning("[movie_translations] Dropping %d row(s) with null movie_id", n_null)
    df = df.dropna(subset=["movie_id"])

    output_key = s3_utils.build_path(
        "silver", "movie_translations", ingestion_date, "movie_translations.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, df)

    per_lang = df["lang"].value_counts().to_dict()
    logger.info(
        "Silver movie-translations transform complete: %d row(s) across %d film(s) "
        "(%s), %d payload(s) without a translations key, %d parse error(s) in %.2fs",
        len(df), df["movie_id"].nunique(),
        ", ".join(f"{k}={v}" for k, v in sorted(per_lang.items())) or "none",
        missing_key, errors, time.monotonic() - t0,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze movie-detail JSON to Silver movie-translations Parquet."
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
    setup_logging("transform_movie_translations")
    args = _parse_args()
    transform_movie_translations(ingestion_date=args.date)
