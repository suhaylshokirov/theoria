"""Silver transform: country names in each translated language.

Reads `bronze/countries/ingestion_date=.../countries_<lang>.json` (Task 93) —
a bare JSON list of ``{iso_3166_1, english_name, native_name}`` — and writes one
row per (country, language). The translated name is TMDB's `native_name` field
when the request carried `language=xx`.

S3 source:  bronze/countries/ingestion_date=YYYY-MM-DD/countries_<lang>.json
S3 output:  silver/country_translations/ingestion_date=YYYY-MM-DD/country_translations.parquet

country_translations columns:
    country_code  string  — ISO-3166-1 alpha-2, matches dim_country.country_code
    lang          string  — bare ISO-639-1 code
    name          string  — never null: an entry with no name is skipped

Every country TMDB lists is written, not only those already in `dim_country`;
the loader resolves each code against the dimension and quarantines the misses,
so the file stays a faithful picture of what TMDB returned.

Missing or unreadable language files are a hard failure (FileNotFoundError):
unlike the per-film blocks there is no pre-Task-93 partition to tolerate here,
because `run_refresh` fetches these files itself every run.

Idempotent: running twice for the same date overwrites the same key.

Usage:
    python -m etl.silver.transform_country_translations
    python -m etl.silver.transform_country_translations --date 2026-09-24
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
from etl.bronze.ingest_countries import COUNTRY_LANGUAGES

logger = logging.getLogger(__name__)

_COLUMNS = ["country_code", "lang", "name"]


def _read_bronze_countries(bucket: str, ingestion_date: dt.date, lang: str) -> list[dict[str, Any]]:
    """Download and parse one Bronze country list for this date."""
    key = s3_utils.build_path("bronze", "countries", ingestion_date, f"countries_{lang}.json")
    client = s3_utils.get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.NoSuchKey:
        raise FileNotFoundError(
            f"No Bronze country file found for ingestion_date={ingestion_date} (key={key})"
        )
    return json.loads(response["Body"].read())


def _extract_country_rows(payload: list[dict[str, Any]], lang: str) -> list[dict[str, Any]]:
    """One row per country that has both a code and a non-blank translated name."""
    rows: list[dict[str, Any]] = []
    for entry in payload:
        code = entry.get("iso_3166_1")
        name = entry.get("native_name")
        name = name.strip() if isinstance(name, str) else None
        if not code or not name:
            continue
        rows.append({"country_code": code, "lang": lang, "name": name})
    return rows


def transform_country_translations(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Bronze country lists -> flatten -> write Silver Parquet.

    Returns the s3:// URI of the written Parquet file.

    Raises FileNotFoundError if any language's Bronze file is missing.
    Raises ValueError if a language's list yields no usable rows.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver country-translations transform for date=%s", ingestion_date)

    rows: list[dict[str, Any]] = []
    for lang in COUNTRY_LANGUAGES:
        payload = _read_bronze_countries(bucket, ingestion_date, lang)
        lang_rows = _extract_country_rows(payload, lang)
        if not lang_rows:
            raise ValueError(
                f"Bronze country file for lang={lang}, ingestion_date={ingestion_date} "
                "contains no named countries."
            )
        logger.info(
            "Extracted %d country name(s) in %s (%d entries skipped for a blank name)",
            len(lang_rows), lang, len(payload) - len(lang_rows),
        )
        rows.extend(lang_rows)

    df = pd.DataFrame(rows, columns=_COLUMNS).astype("string")
    before = len(df)
    df = df.drop_duplicates(subset=["country_code", "lang"], keep="last")
    if before - len(df):
        logger.info("Dropped %d duplicate (country_code, lang) row(s)", before - len(df))

    output_key = s3_utils.build_path(
        "silver", "country_translations", ingestion_date, "country_translations.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, df)
    logger.info(
        "Silver country-translations transform complete: %d row(s) in %.2fs",
        len(df), time.monotonic() - t0,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze country lists to Silver country-translations Parquet."
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
    setup_logging("transform_country_translations")
    args = _parse_args()
    transform_country_translations(ingestion_date=args.date)
