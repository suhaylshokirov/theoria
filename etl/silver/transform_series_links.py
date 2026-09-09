"""Silver transform: series companies, countries, languages, networks, genres.

The TV counterpart of `transform_movie_links.py`. Reads every Bronze
series-detail JSON file for a given ingestion_date (the same source
`transform_series.py` reads) and extracts the nested arrays that transform
drops: `production_companies`, `production_countries` / `origin_country`,
`spoken_languages`, `networks`, and `genres`. Each becomes its own
denormalised long table so a dimension or bridge can be derived from it
downstream with `drop_duplicates`.

A separate module doing its own Bronze pass, mirroring
`transform_movie_links.py`'s relationship to `transform_movies.py`.

S3 source:  bronze/series_details/ingestion_date=YYYY-MM-DD/<series_id>.json
S3 output:  silver/series_companies/ingestion_date=YYYY-MM-DD/series_companies.parquet
            silver/series_countries/ingestion_date=YYYY-MM-DD/series_countries.parquet
            silver/series_languages/ingestion_date=YYYY-MM-DD/series_languages.parquet
            silver/series_networks/ingestion_date=YYYY-MM-DD/series_networks.parquet
            silver/networks/ingestion_date=YYYY-MM-DD/networks.parquet
            silver/series_genres/ingestion_date=YYYY-MM-DD/series_genres.parquet

series_companies columns  (grain: series_id, company_id):
    series_id, company_id, company_name, logo_path, origin_country

series_countries columns  (grain: series_id, country_code, relation):
    series_id, country_code, country_name, relation
    `relation ∈ {origin, production}` is in the grain for the same reason as
    `movie_countries`: `origin_country` and `production_countries` are two
    different relationships that disagree on ~23% of films (Task 61), and
    there is no reason TV differs. An origin row's `country_name` is filled
    from a `production_countries` row for the same code in the same payload
    when one exists, else left null — never guessed from another show.

series_languages columns  (grain: series_id, language_code):
    series_id, language_code, language_name, english_name

series_networks columns  (grain: series_id, network_id):
    series_id, network_id, network_name, logo_path, origin_country
    A network is a TV-only concept (HBO, AMC, Netflix) with no movie
    analogue. TMDB keys networks in a **separate id namespace** from
    companies, so this stays its own link — never folded into
    series_companies.

networks columns  (grain: network_id) — the network dimension input:
    network_id, name, logo_path, origin_country
    Deduplicated across the whole partition, the same pattern
    `load_dim_collection()` uses for `dim_movie.collection_id`.

series_genres columns  (grain: series_id, genre_id) — the genre bridge input:
    series_id, genre_id
    This is the join table movies never got (movie genre membership lives
    only inside `fact_movie_metrics`, forcing a `.distinct()` on every
    aggregate). TV gets it right from the start: a plain
    `bridge_series_genre` with no dedupe guard. Genre *names* for the 8
    TV-only genres are merged into `dim_genre` by `transform_genres` from the
    `genre/tv/list` response — so this table carries only ids.

Rows with a null series_id or entity id are dropped with a warning, never
silently.

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_series_links
    python -m etl.silver.transform_series_links --date 2026-09-09
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


def _extract_company_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    series_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for company in raw.get("production_companies") or []:
        rows.append({
            "series_id": series_id,
            "company_id": company.get("id"),
            "company_name": company.get("name"),
            "logo_path": company.get("logo_path") or None,
            "origin_country": company.get("origin_country") or None,
        })
    return rows


def _extract_country_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    series_id = raw.get("id")
    production = raw.get("production_countries") or []
    name_by_code = {c.get("iso_3166_1"): c.get("name") for c in production}

    rows: list[dict[str, Any]] = []
    for country in production:
        rows.append({
            "series_id": series_id,
            "country_code": country.get("iso_3166_1"),
            "country_name": country.get("name"),
            "relation": "production",
        })
    for code in raw.get("origin_country") or []:
        rows.append({
            "series_id": series_id,
            "country_code": code,
            "country_name": name_by_code.get(code),
            "relation": "origin",
        })
    return rows


def _extract_language_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    series_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for language in raw.get("spoken_languages") or []:
        rows.append({
            "series_id": series_id,
            "language_code": language.get("iso_639_1"),
            "language_name": language.get("name"),
            "english_name": language.get("english_name"),
        })
    return rows


def _extract_network_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    series_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for network in raw.get("networks") or []:
        rows.append({
            "series_id": series_id,
            "network_id": network.get("id"),
            "network_name": network.get("name"),
            "logo_path": network.get("logo_path") or None,
            "origin_country": network.get("origin_country") or None,
        })
    return rows


def _extract_genre_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    series_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for genre in raw.get("genres") or []:
        rows.append({
            "series_id": series_id,
            "genre_id": genre.get("id"),
        })
    return rows


def _cast_company_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    df["company_id"] = pd.to_numeric(df["company_id"], errors="coerce").astype("Int64")
    df["company_name"] = df["company_name"].astype("string")
    df["logo_path"] = df["logo_path"].astype("string")
    df["origin_country"] = df["origin_country"].astype("string")
    return df


def _cast_country_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    df["country_code"] = df["country_code"].astype("string")
    df["country_name"] = df["country_name"].astype("string")
    df["relation"] = df["relation"].astype("string")
    return df


def _cast_language_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    df["language_code"] = df["language_code"].astype("string")
    df["language_name"] = df["language_name"].astype("string")
    df["english_name"] = df["english_name"].astype("string")
    return df


def _cast_series_network_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    df["network_id"] = pd.to_numeric(df["network_id"], errors="coerce").astype("Int64")
    df["network_name"] = df["network_name"].astype("string")
    df["logo_path"] = df["logo_path"].astype("string")
    df["origin_country"] = df["origin_country"].astype("string")
    return df


def _cast_network_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["network_id"] = pd.to_numeric(df["network_id"], errors="coerce").astype("Int64")
    df["name"] = df["name"].astype("string")
    df["logo_path"] = df["logo_path"].astype("string")
    df["origin_country"] = df["origin_country"].astype("string")
    return df


def _cast_genre_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    df["genre_id"] = pd.to_numeric(df["genre_id"], errors="coerce").astype("Int64")
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
    rename: dict[str, str] | None = None,
    select_cols: list[str] | None = None,
) -> str:
    """Shared cast → dedupe → drop-nulls → write pipeline for one output table.

    `columns` guards the empty-`rows` case: `pd.DataFrame([])` has no columns
    at all, which would make `cast_fn` fail on a missing column rather than
    produce a legitimately empty Silver file. `rename` is applied after
    casting and `select_cols` after that — the `networks` dimension is built
    from the same rows as `series_networks` but renames `network_name` → `name`
    and keeps only the network's own columns.
    """
    df = pd.DataFrame(rows, columns=columns)
    df = cast_fn(df)
    if rename:
        df = df.rename(columns=rename)
    if select_cols:
        df = df[select_cols]

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


def transform_series_links(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> tuple[str, str, str, str, str, str]:
    """Read Bronze series-detail JSON → extract five link tables + the network
    dimension input → write Silver.

    Returns a 6-tuple of s3:// URIs: (series_companies, series_countries,
    series_languages, series_networks, networks, series_genres).

    Raises FileNotFoundError if no Bronze series-detail files exist for the date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver series-links transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze series-detail files found for ingestion_date={ingestion_date}"
        )
    logger.info("Found %d Bronze JSON file(s) to process", len(keys))

    company_rows: list[dict[str, Any]] = []
    country_rows: list[dict[str, Any]] = []
    language_rows: list[dict[str, Any]] = []
    network_rows: list[dict[str, Any]] = []
    genre_rows: list[dict[str, Any]] = []
    errors = 0

    for key, raw, read_err in s3_utils.read_json_objects(bucket, keys):
        try:
            if read_err is not None:
                raise read_err
            company_rows.extend(_extract_company_rows(raw))
            country_rows.extend(_extract_country_rows(raw))
            language_rows.extend(_extract_language_rows(raw))
            network_rows.extend(_extract_network_rows(raw))
            genre_rows.extend(_extract_genre_rows(raw))
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/extract %s: %s", key, exc)

    if errors == len(keys):
        raise RuntimeError(
            f"Every Bronze file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    companies_uri = _write_link_table(
        company_rows,
        columns=["series_id", "company_id", "company_name", "logo_path", "origin_country"],
        cast_fn=_cast_company_types,
        dedup_subset=["series_id", "company_id"],
        id_cols=["series_id", "company_id"],
        bucket=bucket,
        entity="series_companies",
        filename="series_companies.parquet",
        ingestion_date=ingestion_date,
    )
    countries_uri = _write_link_table(
        country_rows,
        columns=["series_id", "country_code", "country_name", "relation"],
        cast_fn=_cast_country_types,
        dedup_subset=["series_id", "country_code", "relation"],
        id_cols=["series_id", "country_code"],
        bucket=bucket,
        entity="series_countries",
        filename="series_countries.parquet",
        ingestion_date=ingestion_date,
    )
    languages_uri = _write_link_table(
        language_rows,
        columns=["series_id", "language_code", "language_name", "english_name"],
        cast_fn=_cast_language_types,
        dedup_subset=["series_id", "language_code"],
        id_cols=["series_id", "language_code"],
        bucket=bucket,
        entity="series_languages",
        filename="series_languages.parquet",
        ingestion_date=ingestion_date,
    )
    series_networks_uri = _write_link_table(
        network_rows,
        columns=["series_id", "network_id", "network_name", "logo_path", "origin_country"],
        cast_fn=_cast_series_network_types,
        dedup_subset=["series_id", "network_id"],
        id_cols=["series_id", "network_id"],
        bucket=bucket,
        entity="series_networks",
        filename="series_networks.parquet",
        ingestion_date=ingestion_date,
    )
    networks_uri = _write_link_table(
        network_rows,
        columns=["series_id", "network_id", "network_name", "logo_path", "origin_country"],
        cast_fn=_cast_series_network_types,
        dedup_subset=["network_id"],
        id_cols=["network_id"],
        bucket=bucket,
        entity="networks",
        filename="networks.parquet",
        ingestion_date=ingestion_date,
        rename={"network_name": "name"},
        select_cols=["network_id", "name", "logo_path", "origin_country"],
    )
    genres_uri = _write_link_table(
        genre_rows,
        columns=["series_id", "genre_id"],
        cast_fn=_cast_genre_types,
        dedup_subset=["series_id", "genre_id"],
        id_cols=["series_id", "genre_id"],
        bucket=bucket,
        entity="series_genres",
        filename="series_genres.parquet",
        ingestion_date=ingestion_date,
    )

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver series-links transform complete: %d companies, %d countries, "
        "%d languages, %d network links, %d genre links, %d parse errors in %.2fs",
        len(company_rows), len(country_rows), len(language_rows),
        len(network_rows), len(genre_rows), errors, elapsed,
    )
    # networks_uri is derived from network_rows too — dropped from the count line
    # above because it is the same source rows deduped on a different key.
    return (
        companies_uri, countries_uri, languages_uri,
        series_networks_uri, networks_uri, genres_uri,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze series-detail JSON to Silver series-links Parquet."
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
    setup_logging("transform_series_links")
    args = _parse_args()
    transform_series_links(ingestion_date=args.date)
