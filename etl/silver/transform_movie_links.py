"""Silver transform: movie companies, countries, and languages.

Reads all Bronze movie-detail JSON files for a given ingestion_date (the same
source `transform_movies.py` reads) and extracts the three nested arrays that
transform there drops: `production_companies`, `production_countries` /
`origin_country`, and `spoken_languages`. Each becomes its own denormalised
long table — the link plus the entity's own attributes on every row — so a
dimension can later be derived from it with `drop_duplicates`, the same
pattern `load_dim_collection()` already uses for `dim_movie.collection_id`.

A separate module doing its own Bronze pass, mirroring transform_credits_bridge.py's
relationship to transform_movies.py, rather than widening transform_movies() to
return four URIs — consistent with the existing one-module-one-responsibility
split. The cost is one extra pass over bronze/movie_details; the alternative
breaks that rule.

S3 source:  bronze/movie_details/ingestion_date=YYYY-MM-DD/<movie_id>.json
S3 output:  silver/movie_companies/ingestion_date=YYYY-MM-DD/movie_companies.parquet
            silver/movie_countries/ingestion_date=YYYY-MM-DD/movie_countries.parquet
            silver/movie_languages/ingestion_date=YYYY-MM-DD/movie_languages.parquet

movie_companies columns:
    movie_id        Int64   — TMDB movie ID
    company_id      Int64   — TMDB company ID
    company_name    string
    logo_path       string  — nullable, TMDB "" normalised to None
    origin_country  string  — nullable (86% Bronze coverage), the company's
                              home country, not the film's

movie_countries columns:
    movie_id      Int64
    country_code  string  — ISO 3166-1 alpha-2
    country_name  string  — nullable; TMDB's `origin_country` list carries
                            only a code, no name, so an origin row's name is
                            filled from a `production_countries` row for the
                            same code in the same payload when one exists,
                            else left null rather than guessed
    relation      string  — "origin" or "production"

    `relation` is the grain decision of this module. `origin_country` and
    `production_countries` are two different relationships that disagree on
    ~23% of films (a UK-set film shot with US production money, for
    instance), so they cannot be merged into one row set without losing which
    is which. The alternative considered and rejected — one row per
    (movie_id, country_code) with `is_origin`/`is_production` booleans — was
    rejected because it makes a single row mean two things at once, which is
    the same shape of mistake Task 40 already found once (a key claiming a
    finer grain than the data actually has). Here the risk runs the other
    way — a key coarser than the data — so `relation` is folded into the
    dedup key itself.

movie_languages columns:
    movie_id       Int64
    language_code  string  — ISO 639-1
    language_name  string  — TMDB's localised name for the language
    english_name   string  — TMDB's English name for the language

Dedup keys, each matching the true grain and no wider (Task 40's lesson):
    movie_companies: (movie_id, company_id)
    movie_countries: (movie_id, country_code, relation)
    movie_languages: (movie_id, language_code)

Rows with a null movie_id or entity id are dropped with a warning, never
silently — same convention as every other Silver transform.

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_movie_links
    python -m etl.silver.transform_movie_links --date 2026-06-22
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


def _read_json_from_s3(bucket: str, key: str) -> dict[str, Any]:
    """Download and parse a single JSON object from S3."""
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read())


def _extract_company_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one row per production company from a TMDB movie-detail payload."""
    movie_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for company in raw.get("production_companies") or []:
        rows.append({
            "movie_id": movie_id,
            "company_id": company.get("id"),
            "company_name": company.get("name"),
            "logo_path": company.get("logo_path") or None,
            "origin_country": company.get("origin_country") or None,
        })
    return rows


def _extract_country_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one row per (country, relation) from a TMDB movie-detail payload.

    `production_countries` carries both a code and a name; `origin_country` is
    a bare list of codes. A code->name map built from this payload's own
    production_countries fills in an origin row's name when the same code
    appears there too — never guessed from another movie.
    """
    movie_id = raw.get("id")
    production = raw.get("production_countries") or []
    name_by_code = {c.get("iso_3166_1"): c.get("name") for c in production}

    rows: list[dict[str, Any]] = []
    for country in production:
        rows.append({
            "movie_id": movie_id,
            "country_code": country.get("iso_3166_1"),
            "country_name": country.get("name"),
            "relation": "production",
        })
    for code in raw.get("origin_country") or []:
        rows.append({
            "movie_id": movie_id,
            "country_code": code,
            "country_name": name_by_code.get(code),
            "relation": "origin",
        })
    return rows


def _extract_language_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one row per spoken language from a TMDB movie-detail payload."""
    movie_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for language in raw.get("spoken_languages") or []:
        rows.append({
            "movie_id": movie_id,
            "language_code": language.get("iso_639_1"),
            "language_name": language.get("name"),
            "english_name": language.get("english_name"),
        })
    return rows


def _cast_company_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["movie_id"] = pd.to_numeric(df["movie_id"], errors="coerce").astype("Int64")
    df["company_id"] = pd.to_numeric(df["company_id"], errors="coerce").astype("Int64")
    df["company_name"] = df["company_name"].astype("string")
    df["logo_path"] = df["logo_path"].astype("string")
    df["origin_country"] = df["origin_country"].astype("string")
    return df


def _cast_country_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["movie_id"] = pd.to_numeric(df["movie_id"], errors="coerce").astype("Int64")
    df["country_code"] = df["country_code"].astype("string")
    df["country_name"] = df["country_name"].astype("string")
    df["relation"] = df["relation"].astype("string")
    return df


def _cast_language_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["movie_id"] = pd.to_numeric(df["movie_id"], errors="coerce").astype("Int64")
    df["language_code"] = df["language_code"].astype("string")
    df["language_name"] = df["language_name"].astype("string")
    df["english_name"] = df["english_name"].astype("string")
    return df


def _write_link_table(
    rows: list[dict[str, Any]],
    *,
    columns: list[str],
    cast_fn,
    dedup_subset: list[str],
    id_cols: list[str],
    bucket: str,
    entity: str,
    filename: str,
    ingestion_date: dt.date,
) -> str:
    """Shared cast -> dedupe -> drop-nulls -> write pipeline for one link table.

    `columns` guards the case where `rows` is empty (a partition where no
    movie had, say, a homepage-free but company-free payload) — `pd.DataFrame([])`
    has no columns at all, which would make cast_fn fail on a missing column
    rather than produce a legitimately empty Silver file.
    """
    df = pd.DataFrame(rows, columns=columns)
    df = cast_fn(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=dedup_subset, keep="last")
    dupes = before_dedup - len(df)
    if dupes:
        logger.info("[%s] Dropped %d duplicate row(s) on %s", entity, dupes, dedup_subset)

    for col in id_cols:
        n_null = df[col].isna().sum()
        if n_null:
            logger.warning("[%s] Dropping %d row(s) with null %s", entity, n_null, col)
    df = df.dropna(subset=id_cols)

    output_key = s3_utils.build_path("silver", entity, ingestion_date, filename)
    return s3_utils.write_parquet(bucket, output_key, df)


def transform_movie_links(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> tuple[str, str, str]:
    """Read Bronze movie-detail JSON -> extract companies/countries/languages -> write Silver.

    Returns a 3-tuple of s3:// URIs: (movie_companies, movie_countries, movie_languages).

    Raises FileNotFoundError if no Bronze movie-detail files exist for the date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver movie-links transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze movie-detail files found for ingestion_date={ingestion_date}"
        )
    logger.info("Found %d Bronze JSON file(s) to process", len(keys))

    company_rows: list[dict[str, Any]] = []
    country_rows: list[dict[str, Any]] = []
    language_rows: list[dict[str, Any]] = []
    errors = 0

    for key in keys:
        try:
            raw = _read_json_from_s3(bucket, key)
            company_rows.extend(_extract_company_rows(raw))
            country_rows.extend(_extract_country_rows(raw))
            language_rows.extend(_extract_language_rows(raw))
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/extract %s: %s", key, exc)

    if not (company_rows or country_rows or language_rows) and errors == len(keys):
        raise RuntimeError(
            f"Every Bronze file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    companies_uri = _write_link_table(
        company_rows,
        columns=["movie_id", "company_id", "company_name", "logo_path", "origin_country"],
        cast_fn=_cast_company_types,
        dedup_subset=["movie_id", "company_id"],
        id_cols=["movie_id", "company_id"],
        bucket=bucket,
        entity="movie_companies",
        filename="movie_companies.parquet",
        ingestion_date=ingestion_date,
    )
    countries_uri = _write_link_table(
        country_rows,
        columns=["movie_id", "country_code", "country_name", "relation"],
        cast_fn=_cast_country_types,
        dedup_subset=["movie_id", "country_code", "relation"],
        id_cols=["movie_id", "country_code"],
        bucket=bucket,
        entity="movie_countries",
        filename="movie_countries.parquet",
        ingestion_date=ingestion_date,
    )
    languages_uri = _write_link_table(
        language_rows,
        columns=["movie_id", "language_code", "language_name", "english_name"],
        cast_fn=_cast_language_types,
        dedup_subset=["movie_id", "language_code"],
        id_cols=["movie_id", "language_code"],
        bucket=bucket,
        entity="movie_languages",
        filename="movie_languages.parquet",
        ingestion_date=ingestion_date,
    )

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver movie-links transform complete: %d companies, %d countries, "
        "%d languages, %d parse errors in %.2fs",
        len(company_rows), len(country_rows), len(language_rows), errors, elapsed,
    )
    return companies_uri, countries_uri, languages_uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze movie-detail JSON to Silver movie-links Parquet."
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
    setup_logging("transform_movie_links")
    args = _parse_args()
    transform_movie_links(ingestion_date=args.date)
