"""Warehouse loader: Dimensions.

Reads the Silver Parquet files for a given ingestion_date and upserts them
into the PostgreSQL dimension tables (dim_collection, dim_movie, dim_person,
dim_genre). dim_date is populated separately as a full calendar table that does
not depend on any Silver data.

dim_collection is loaded first: dim_movie.collection_id is an FK to it.

Upserts use ON CONFLICT (pk) DO UPDATE, so re-running the loader for the
same or a later ingestion_date is idempotent — existing rows are refreshed
in place rather than duplicated. After upserting, dim_movie/dim_person/
dim_collection each get a `slug` column recomputed over the whole table
via assign_slugs() — the URL-facing identifier for those pages, so a movie or
person is reachable at a readable path instead of a bare surrogate key.

S3 sources:
    silver/movies/ingestion_date=YYYY-MM-DD/movies.parquet
    silver/people/ingestion_date=YYYY-MM-DD/people.parquet
    silver/genres/ingestion_date=YYYY-MM-DD/genres.parquet
    silver/movie_companies/ingestion_date=YYYY-MM-DD/movie_companies.parquet
    silver/company_details/ingestion_date=YYYY-MM-DD/company_details.parquet  (optional)
    silver/movie_countries/ingestion_date=YYYY-MM-DD/movie_countries.parquet
    silver/movie_languages/ingestion_date=YYYY-MM-DD/movie_languages.parquet

Usage:
    python -m etl.warehouse_loader.load_dimensions
    python -m etl.warehouse_loader.load_dimensions --date 2026-06-22
    python -m etl.warehouse_loader.load_dimensions --incremental
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import time
import unicodedata
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from etl.incremental import pending_partitions, set_watermark
from etl.warehouse_loader.common import _read_silver_parquet, _upsert
from warehouse.db import get_session

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

_DEFAULT_CALENDAR_START = dt.date(1900, 1, 1)
_DEFAULT_CALENDAR_END = dt.date(2035, 12, 31)
_LOADER_NAME = "load_dimensions"
_WATERMARK_ENTITY = "movies"  # reference entity used to discover new Silver partitions


def _records(df: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    """Convert selected columns of a DataFrame to a list of dicts, with NA -> None."""
    subset = df[columns].astype(object).where(pd.notnull(df[columns]), None)
    return subset.to_dict("records")


def load_dim_collection(session: Session, df: pd.DataFrame) -> int:
    """Upsert the distinct franchises referenced by Silver movies into dim_collection.

    Silver carries the collection inline on each movie row, so the dimension is
    the *distinct* set of those values — roughly half the catalog contributes
    nothing here, which is a real property of films rather than missing data.
    Must run before load_dim_movie(), which has an FK to this table.
    """
    named = df[df["collection_id"].notna() & df["collection_name"].notna()]
    collections = (
        named[["collection_id", "collection_name", "collection_poster_path"]]
        .drop_duplicates(subset=["collection_id"], keep="last")
        .rename(columns={"collection_name": "name", "collection_poster_path": "poster_path"})
    )
    columns = ["collection_id", "name", "poster_path"]
    records = _records(collections, columns)
    count = _upsert(session, "dim_collection", ["collection_id"], columns, records)
    logger.info("dim_collection: upserted %d row(s)", count)
    return count


def load_dim_movie(session: Session, df: pd.DataFrame) -> int:
    """Upsert Silver movies into dim_movie."""
    columns = ["movie_id", "title", "release_date", "runtime", "budget", "revenue",
               "original_language", "status", "overview", "tagline", "poster_path",
               "backdrop_path", "collection_id", "imdb_id", "original_title", "homepage"]
    records = _records(df, columns)
    count = _upsert(session, "dim_movie", ["movie_id"], columns, records)
    logger.info("dim_movie: upserted %d row(s)", count)
    return count


def load_dim_person(session: Session, df: pd.DataFrame) -> int:
    """Upsert Silver people into dim_person.

    No rename: person_id is the natural key here, unlike dim_actor/dim_director,
    which had to relabel the same TMDB id twice because the same person could be
    two rows in two tables.
    """
    columns = ["person_id", "name", "gender", "popularity", "profile_path",
               "known_for_department"]
    records = _records(df, columns)
    count = _upsert(session, "dim_person", ["person_id"], columns, records)
    logger.info("dim_person: upserted %d row(s)", count)
    return count


def load_dim_genre(session: Session, df: pd.DataFrame) -> int:
    """Upsert Silver genres into dim_genre."""
    columns = ["genre_id", "genre_name"]
    records = _records(df, columns)
    count = _upsert(session, "dim_genre", ["genre_id"], columns, records)
    logger.info("dim_genre: upserted %d row(s)", count)
    return count


_COMPANY_DETAIL_COLS = [
    "description", "headquarters", "homepage",
    "parent_company_id", "parent_company_name",
]


def load_dim_company(
    session: Session,
    df: pd.DataFrame,
    details_df: pd.DataFrame | None = None,
) -> int:
    """Upsert the distinct companies referenced by Silver movie_companies into dim_company.

    Mirrors load_dim_collection(): the dimension is the *distinct* set of
    companies named across every movie_companies link row, derived with
    drop_duplicates rather than read from its own dedicated dimension source.
    Filtering on id AND name matters here for the same reason it does for
    dim_collection — an id with a null name would violate dim_company's
    `name NOT NULL`, and the two nullability failures shouldn't be conflated.
    Must run before load_bridge_movie_company(), which has an FK to this table.

    `details_df` (Task 65) is the second Silver source — one row per company
    from `silver/company_details`, carrying description / headquarters /
    homepage / parent. It is LEFT-joined on: a company with no company-details
    row yet (freshly discovered this partition, enrichment pending, or a
    company whose one API call failed) still upserts with its five original
    columns and simply leaves the five new ones null. Passing None (a missing
    Silver file) degrades the same way rather than blocking the load.
    """
    named = df[df["company_id"].notna() & df["company_name"].notna()]
    companies = (
        named[["company_id", "company_name", "logo_path", "origin_country"]]
        .drop_duplicates(subset=["company_id"], keep="last")
        .rename(columns={"company_name": "name"})
    )

    columns = ["company_id", "name", "logo_path", "origin_country"]
    if details_df is not None and not details_df.empty:
        details = details_df[["company_id"] + _COMPANY_DETAIL_COLS].drop_duplicates(
            subset=["company_id"], keep="last"
        )
        companies = companies.merge(details, on="company_id", how="left")
        columns = columns + _COMPANY_DETAIL_COLS

    records = _records(companies, columns)
    count = _upsert(session, "dim_company", ["company_id"], columns, records)
    logger.info(
        "dim_company: upserted %d row(s)%s", count,
        "" if details_df is None else f" ({len(records)} with detail join)",
    )
    return count


def load_dim_country(session: Session, df: pd.DataFrame) -> int:
    """Upsert the distinct named countries referenced by Silver movie_countries into dim_country.

    Mirrors load_dim_company(): the dimension is the *distinct* set of
    country_code values across every movie_countries link row. Filtering on
    name as well as code matters more here than for company/collection — an
    origin-only country_code with no matching production_countries row in
    the same payload (Task 57: ~17 rows on the 2026-07-29 partition) has no
    name to give it, and dim_country.name is NOT NULL. Those codes simply
    get no dimension row; load_bridge_movie_country() then quarantines their
    bridge rows via the normal unresolvable-FK path, rather than inventing a
    name. A code named on *any* movie's production_countries list gets a row
    even if this exact link row's name is null, since drop_duplicates keeps
    the last named occurrence across the whole partition.
    """
    named = df[df["country_code"].notna() & df["country_name"].notna()]
    countries = (
        named[["country_code", "country_name"]]
        .drop_duplicates(subset=["country_code"], keep="last")
        .rename(columns={"country_name": "name"})
    )
    columns = ["country_code", "name"]
    records = _records(countries, columns)
    count = _upsert(session, "dim_country", ["country_code"], columns, records)
    logger.info("dim_country: upserted %d row(s)", count)
    return count


def load_dim_language(session: Session, df: pd.DataFrame) -> int:
    """Upsert the distinct languages referenced by Silver movie_languages into dim_language.

    Same shape as load_dim_country(): dedupe the link table down to its
    distinct language_code values. TMDB's spoken_languages entries always
    carry both a name and an english_name, so unlike countries there's no
    partial-coverage case here — just the id/name null-filter every other
    link-derived dimension applies for consistency.
    """
    named = df[df["language_code"].notna() & df["language_name"].notna()]
    languages = (
        named[["language_code", "language_name", "english_name"]]
        .drop_duplicates(subset=["language_code"], keep="last")
        .rename(columns={"language_name": "name"})
    )
    columns = ["language_code", "name", "english_name"]
    records = _records(languages, columns)
    count = _upsert(session, "dim_language", ["language_code"], columns, records)
    logger.info("dim_language: upserted %d row(s)", count)
    return count


def _slugify(name: str) -> str:
    """Lowercase, ASCII, hyphenated form of a name/title for use in a URL.

    Accented characters are folded to their plain-ASCII base (e.g. "Zoe"
    Kravitz stays readable) via NFKD decomposition before anything else is
    stripped, rather than dropping the whole character.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", ascii_name.lower()).strip("-")
    return slug or "untitled"


def assign_slugs(session: Session, table: str, id_col: str, name_col: str) -> int:
    """Recompute every row's slug in `table`, deterministically and idempotently.

    Reruns must never change a slug that's already been linked to or bookmarked,
    so this can't just slugify each new batch in isolation — a name added in a
    later partition could collide with one already in the table. Instead it
    re-derives every row's slug from the *whole* table each time, walked in
    ascending `id_col` order: a genuine collision (two rows slugifying to the
    same base) is broken by numbering in that fixed order, so the same row
    always lands on the same slug across reruns, and a newly discovered row
    (always a new, larger id) can only ever be appended after the existing
    numbering, never insert itself ahead of it.

    The slugs are cleared before they are rewritten. Recomputing over the whole
    table can *permute* slugs — when a newly loaded person with a lower id takes
    a base slug, its previous owner moves to `-2` — and the rewrite is a batched
    executemany, so the unique index is checked after every individual row. The
    row that gains the slug can therefore be written before the row that gives
    it up, and Postgres rejects that transient duplicate even though the final
    state is perfectly unique. Clearing first removes the intermediate collision
    (the index permits many NULLs); both statements run in the caller's
    transaction, so no reader ever observes the table without slugs.
    """
    rows = session.execute(
        text(f"SELECT {id_col}, {name_col} FROM {table} ORDER BY {id_col}")
    ).fetchall()

    seen: dict[str, int] = {}
    records = []
    for row_id, name in rows:
        base = _slugify(name or "")
        n = seen.get(base, 0) + 1
        seen[base] = n
        slug = base if n == 1 else f"{base}-{n}"
        records.append({"id": row_id, "slug": slug})

    if records:
        session.execute(text(f"UPDATE {table} SET slug = NULL WHERE slug IS NOT NULL"))
        _apply_slugs(session, table, id_col, records)
    logger.info("%s: assigned %d slug(s)", table, len(records))
    return len(records)


_SLUG_UPDATE_CHUNK = 1000


def _apply_slugs(session: Session, table: str, id_col: str,
                 records: list[dict[str, Any]]) -> None:
    """Write {id, slug} pairs back to `table` in chunked UPDATE ... FROM (VALUES).

    A textual executemany UPDATE is one driver round-trip per row. That is
    tolerable on a local socket and multi-hour for dim_person's ~122k rows
    against an out-of-region database — insertmanyvalues batches INSERTs but
    nothing batches an executemany UPDATE, so the batching is done by hand:
    ~1,000 rows per statement, matched back by id.
    """
    for start in range(0, len(records), _SLUG_UPDATE_CHUNK):
        chunk = records[start:start + _SLUG_UPDATE_CHUNK]
        tuples = ", ".join(f"(:id_{i}, :slug_{i})" for i in range(len(chunk)))
        params: dict[str, Any] = {}
        for i, rec in enumerate(chunk):
            params[f"id_{i}"] = rec["id"]
            params[f"slug_{i}"] = rec["slug"]
        session.execute(
            text(
                f"UPDATE {table} AS t SET slug = v.slug "
                f"FROM (VALUES {tuples}) AS v(id, slug) "
                f"WHERE t.{id_col} = v.id::bigint"
            ),
            params,
        )


def _build_calendar(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Build a full day-granularity calendar DataFrame between start and end (inclusive)."""
    dates = pd.date_range(start=start, end=end, freq="D")
    df = pd.DataFrame({"full_date": dates})
    df["date_id"] = df["full_date"].dt.strftime("%Y%m%d").astype("int64")
    df["year"] = df["full_date"].dt.year.astype("int64")
    df["month"] = df["full_date"].dt.month.astype("int64")
    df["day"] = df["full_date"].dt.day.astype("int64")
    df["decade"] = (df["year"] // 10 * 10).astype("int64")
    df["full_date"] = df["full_date"].dt.date
    return df


def load_dim_date(session: Session, start: dt.date = _DEFAULT_CALENDAR_START,
                   end: dt.date = _DEFAULT_CALENDAR_END) -> int:
    """Populate dim_date as a full calendar table between start and end (inclusive)."""
    df = _build_calendar(start, end)
    columns = ["date_id", "full_date", "year", "month", "day", "decade"]
    records = _records(df, columns)
    count = _upsert(session, "dim_date", ["date_id"], columns, records)
    logger.info("dim_date: upserted %d row(s) (%s to %s)", count, start, end)
    return count


def load_dimensions(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    calendar_start: dt.date = _DEFAULT_CALENDAR_START,
    calendar_end: dt.date = _DEFAULT_CALENDAR_END,
) -> dict[str, int]:
    """Read Silver Parquet for `ingestion_date` and upsert all dimension tables.

    Returns a dict of table name -> row count upserted.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting dimension load for ingestion_date=%s", ingestion_date)

    movies_df = _read_silver_parquet(bucket, "movies", ingestion_date, "movies.parquet")
    people_df = _read_silver_parquet(bucket, "people", ingestion_date, "people.parquet")
    genres_df = _read_silver_parquet(bucket, "genres", ingestion_date, "genres.parquet")
    companies_df = _read_silver_parquet(
        bucket, "movie_companies", ingestion_date, "movie_companies.parquet"
    )
    # Task 65: the company-detail enrichment. Optional — a partition written
    # before this Silver transform existed, or a transform that failed, must
    # degrade to "load dim_company with null detail columns", never crash the
    # whole dimension load. load_dim_company() treats None the same as an
    # un-joined company.
    try:
        company_details_df = _read_silver_parquet(
            bucket, "company_details", ingestion_date, "company_details.parquet"
        )
    except Exception as exc:
        logger.warning(
            "No Silver company_details for %s (%s) — loading dim_company "
            "without detail columns", ingestion_date, exc,
        )
        company_details_df = None
    countries_df = _read_silver_parquet(
        bucket, "movie_countries", ingestion_date, "movie_countries.parquet"
    )
    languages_df = _read_silver_parquet(
        bucket, "movie_languages", ingestion_date, "movie_languages.parquet"
    )

    counts: dict[str, int] = {}
    with get_session() as session:
        # Before dim_movie: dim_movie.collection_id is an FK to this table.
        counts["dim_collection"] = load_dim_collection(session, movies_df)
        counts["dim_movie"] = load_dim_movie(session, movies_df)
        counts["dim_person"] = load_dim_person(session, people_df)
        counts["dim_genre"] = load_dim_genre(session, genres_df)
        # Before load_facts.load_bridge_movie_company(), which has an FK here.
        counts["dim_company"] = load_dim_company(
            session, companies_df, company_details_df
        )
        # Before load_facts.load_bridge_movie_country/language(), same reason.
        counts["dim_country"] = load_dim_country(session, countries_df)
        counts["dim_language"] = load_dim_language(session, languages_df)
        counts["dim_date"] = load_dim_date(session, calendar_start, calendar_end)
        counts["dim_movie_slugs"] = assign_slugs(session, "dim_movie", "movie_id", "title")
        counts["dim_person_slugs"] = assign_slugs(session, "dim_person", "person_id", "name")
        counts["dim_collection_slugs"] = assign_slugs(session, "dim_collection", "collection_id", "name")
        counts["dim_company_slugs"] = assign_slugs(session, "dim_company", "company_id", "name")

    elapsed = time.monotonic() - t0
    logger.info(
        "Dimension load complete: %s in %.2fs",
        ", ".join(f"{k}={v}" for k, v in counts.items()), elapsed,
    )
    return counts


def load_dimensions_incremental(
    bucket: str | None = None,
    calendar_start: dt.date = _DEFAULT_CALENDAR_START,
    calendar_end: dt.date = _DEFAULT_CALENDAR_END,
) -> dict[str, dict[str, int]]:
    """Process every Silver partition newer than this loader's watermark, in order.

    Discovers pending dates via etl.incremental.pending_partitions() (using the
    "movies" entity as the reference partition list), runs load_dimensions() for
    each, and advances the watermark after each date completes — so a failure
    partway through leaves the watermark at the last fully-processed date rather
    than losing all progress.

    Returns a dict of ingestion_date (ISO string) -> per-table row counts.
    """
    if bucket is None:
        bucket = config.S3_BUCKET

    with get_session() as session:
        dates = pending_partitions(session, _LOADER_NAME, bucket, "silver", _WATERMARK_ENTITY)

    if not dates:
        logger.info("No new Silver partitions to process for %s", _LOADER_NAME)
        return {}

    logger.info("%d pending partition(s) for %s: %s", len(dates), _LOADER_NAME, dates)

    results: dict[str, dict[str, int]] = {}
    for ingestion_date in dates:
        counts = load_dimensions(
            ingestion_date=ingestion_date, bucket=bucket,
            calendar_start=calendar_start, calendar_end=calendar_end,
        )
        with get_session() as session:
            set_watermark(session, _LOADER_NAME, ingestion_date)
        logger.info("Watermark for %s advanced to %s", _LOADER_NAME, ingestion_date)
        results[ingestion_date.isoformat()] = counts

    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upsert Silver Parquet into the PostgreSQL dimension tables."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today. Ignored with --incremental.",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Process every Silver partition newer than the stored watermark, instead of a single date.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("load_dimensions")
    args = _parse_args()
    if args.incremental:
        load_dimensions_incremental()
    else:
        load_dimensions(ingestion_date=args.date)
