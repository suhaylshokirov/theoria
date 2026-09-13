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
    silver/person_details/ingestion_date=YYYY-MM-DD/person_details.parquet  (optional)
    silver/movie_videos/ingestion_date=YYYY-MM-DD/movie_videos.parquet  (optional)
    silver/series_videos/ingestion_date=YYYY-MM-DD/series_videos.parquet  (optional)

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
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from etl.incremental import pending_partitions, set_watermark
from etl.warehouse_loader.common import (
    _existing_ids,
    _optional_silver,
    _read_silver_parquet,
    _replace_by_parent,
    _upsert,
    _write_rejects,
)
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


_PERSON_DETAIL_COLS = [
    "biography", "birthday", "deathday", "place_of_birth", "homepage", "imdb_id",
]


def load_dim_person(
    session: Session,
    df: pd.DataFrame,
    details_df: pd.DataFrame | None = None,
) -> int:
    """Upsert Silver people into dim_person.

    No rename: person_id is the natural key here, unlike dim_actor/dim_director,
    which had to relabel the same TMDB id twice because the same person could be
    two rows in two tables.

    `details_df` (Task 72) is the optional second Silver source — one row per
    person from `silver/person_details`, carrying biography / birthday /
    deathday / place_of_birth / homepage / imdb_id. LEFT-joined exactly like
    load_dim_company()'s detail join: a person with no detail row yet (no
    photo, enrichment pending, or a failed call) still upserts their six
    original columns and leaves the six new ones null. Passing None (a missing
    Silver file) degrades the same way.
    """
    columns = ["person_id", "name", "gender", "popularity", "profile_path",
               "known_for_department"]
    people = df

    if details_df is not None and not details_df.empty:
        details = details_df[["person_id"] + _PERSON_DETAIL_COLS].drop_duplicates(
            subset=["person_id"], keep="last"
        )
        people = people.merge(details, on="person_id", how="left")
        columns = columns + _PERSON_DETAIL_COLS

    records = _records(people, columns)
    count = _upsert(session, "dim_person", ["person_id"], columns, records)
    logger.info(
        "dim_person: upserted %d row(s)%s", count,
        "" if details_df is None else " (with detail join)",
    )
    return count


def load_dim_genre(session: Session, df: pd.DataFrame) -> int:
    """Upsert Silver genres into dim_genre."""
    columns = ["genre_id", "genre_name"]
    records = _records(df, columns)
    count = _upsert(session, "dim_genre", ["genre_id"], columns, records)
    logger.info("dim_genre: upserted %d row(s)", count)
    return count


_DIM_SERIES_COLS = [
    "series_id", "name", "original_name", "first_air_date", "last_air_date",
    "number_of_seasons", "number_of_episodes", "status", "type", "in_production",
    "original_language", "overview", "tagline", "poster_path", "backdrop_path",
    "homepage", "imdb_id",
]


def load_dim_series(session: Session, df: pd.DataFrame) -> int:
    """Upsert Silver series into dim_series (Task 79).

    Plain column-for-column upsert, the same shape as load_dim_movie(). `slug`
    is not written here — assign_slugs() computes it over the whole table
    afterwards, exactly as it does for dim_movie.
    """
    records = _records(df, _DIM_SERIES_COLS)
    count = _upsert(session, "dim_series", ["series_id"], _DIM_SERIES_COLS, records)
    logger.info("dim_series: upserted %d row(s)", count)
    return count


def load_dim_network(session: Session, df: pd.DataFrame) -> int:
    """Upsert the distinct networks referenced by Silver series into dim_network.

    Silver's `networks` Parquet is already one row per network_id, but the
    same id/name null-filter and dedupe every other link-derived dimension
    applies is kept for consistency: a network row with a null name can't
    satisfy dim_network.name NOT NULL, so it gets no dimension row and
    load_bridge_series_network() then quarantines its bridge rows.
    """
    named = df[df["network_id"].notna() & df["name"].notna()]
    networks = named[["network_id", "name", "logo_path", "origin_country"]].drop_duplicates(
        subset=["network_id"], keep="last"
    )
    columns = ["network_id", "name", "logo_path", "origin_country"]
    records = _records(networks, columns)
    count = _upsert(session, "dim_network", ["network_id"], columns, records)
    logger.info("dim_network: upserted %d row(s)", count)
    return count


_COMPANY_DETAIL_COLS = [
    "description", "headquarters", "homepage",
    "parent_company_id", "parent_company_name",
]


_COMPANY_LINK_COLS = ["company_id", "company_name", "logo_path", "origin_country"]


def load_dim_company(
    session: Session,
    df: pd.DataFrame,
    details_df: pd.DataFrame | None = None,
    series_df: pd.DataFrame | None = None,
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

    `series_df` (Task 79) is the TV equivalent of `df` — Silver
    `series_companies` link rows, carrying the same company_id / name / logo /
    origin_country. TMDB has one company namespace across film and TV (the
    feature preamble), so a studio that made a show is the same dim_company
    entity as one that made a film; its link rows are unioned in here so
    bridge_series_company resolves without a systematic quarantine. None
    degrades to "movies only", exactly as before Task 79.
    """
    link = df[_COMPANY_LINK_COLS]
    if series_df is not None and not series_df.empty:
        link = pd.concat([link, series_df[_COMPANY_LINK_COLS]], ignore_index=True)
    named = link[link["company_id"].notna() & link["company_name"].notna()]
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


def load_dim_country(
    session: Session, df: pd.DataFrame, series_df: pd.DataFrame | None = None
) -> int:
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

    `series_df` (Task 79) unions Silver `series_countries` rows the same way
    load_dim_company() unions series companies — countries are ISO-standard
    and shared across film and TV. None degrades to "movies only".
    """
    link = df[["country_code", "country_name"]]
    if series_df is not None and not series_df.empty:
        link = pd.concat([link, series_df[["country_code", "country_name"]]], ignore_index=True)
    named = link[link["country_code"].notna() & link["country_name"].notna()]
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


def load_dim_language(
    session: Session, df: pd.DataFrame, series_df: pd.DataFrame | None = None
) -> int:
    """Upsert the distinct languages referenced by Silver movie_languages into dim_language.

    Same shape as load_dim_country(): dedupe the link table down to its
    distinct language_code values. TMDB's spoken_languages entries always
    carry both a name and an english_name, so unlike countries there's no
    partial-coverage case here — just the id/name null-filter every other
    link-derived dimension applies for consistency.

    `series_df` (Task 79) unions Silver `series_languages` rows — same
    namespace across film and TV. None degrades to "movies only".
    """
    _cols = ["language_code", "language_name", "english_name"]
    link = df[_cols]
    if series_df is not None and not series_df.empty:
        link = pd.concat([link, series_df[_cols]], ignore_index=True)
    named = link[link["language_code"].notna() & link["language_name"].notna()]
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


_MOVIE_VIDEO_COLS = [
    "movie_id", "video_id", "name", "key", "site", "type", "official",
    "size", "iso_639_1", "iso_3166_1", "published_at",
]


def load_dim_movie_video(
    session: Session, videos_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Replace (not upsert) every video row for the films in this Silver partition.

    dim_movie_video is the first table here that must be able to *shrink*: TMDB
    removes videos and YouTube keys rot, so a pure upsert would leave a dead
    embed on the page forever, with no failing check. _replace_by_parent()
    deletes every existing row for the movie_ids present in this partition,
    then inserts the current set — scoped to those movie_ids, never a blanket
    wipe (a partition covers only the films it ingested).

    movie_id is resolved against dim_movie; a row whose movie_id has no
    dimension row is quarantined, never dropped (the standing rule since
    Task 58). Returns (count, rejects).
    """
    valid_movie_ids = _existing_ids(session, "dim_movie", "movie_id")

    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    for record in videos_df.to_dict("records"):
        movie_id = record.get("movie_id")
        if pd.isna(movie_id) or int(movie_id) not in valid_movie_ids:
            rejects.append({**record, "rejection_reason": "unknown movie_id"})
            continue

        video_id = record.get("video_id")
        if video_id is None or pd.isna(video_id) or not str(video_id).strip():
            rejects.append({**record, "rejection_reason": "missing video_id"})
            continue

        row = {col: record.get(col) for col in _MOVIE_VIDEO_COLS}
        row["movie_id"] = int(movie_id)
        row["video_id"] = str(video_id)
        row["official"] = None if pd.isna(record.get("official")) else bool(record.get("official"))
        row["size"] = None if pd.isna(record.get("size")) else int(record.get("size"))
        row["ingestion_date"] = ingestion_date
        # NA -> None for psycopg2, the same scrub _records() does for the
        # upsert loaders (every value here is a scalar).
        rows.append({k: (None if pd.isna(v) else v) for k, v in row.items()})

    parent_ids = sorted({r["movie_id"] for r in rows})
    columns = _MOVIE_VIDEO_COLS + ["ingestion_date"]
    count = _replace_by_parent(
        session, "dim_movie_video", "movie_id", parent_ids, columns, rows
    )
    logger.info(
        "dim_movie_video: replaced %d row(s) across %d film(s), rejected %d row(s)",
        count, len(parent_ids), len(rejects),
    )
    return count, rejects


# --- Task 84: dim_season, dim_episode --------------------------------------

_DIM_SEASON_COLS = [
    "season_id", "series_id", "season_number", "name", "air_date",
    "episode_count", "overview", "poster_path",
]
_DIM_EPISODE_COLS = [
    "episode_id", "series_id", "season_number", "episode_number", "name",
    "air_date", "runtime", "overview", "still_path", "episode_type",
    "production_code", "imdb_id",
]


def load_dim_season(
    session: Session, seasons_df: pd.DataFrame,
) -> tuple[int, list[dict[str, Any]]]:
    """Upsert Silver seasons into dim_season, resolving series_id against dim_series.

    Plain upsert (not _replace_by_parent): a show's season list is stable in a
    way its episode list is not — see 22_episodes.sql. A season row whose
    series_id has no dim_series row is quarantined, never dropped (the standing
    rule). Returns (count, rejects).
    """
    valid_series_ids = _existing_ids(session, "dim_series", "series_id")

    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    for record in seasons_df.to_dict("records"):
        series_id = record.get("series_id")
        if pd.isna(series_id) or int(series_id) not in valid_series_ids:
            rejects.append({**record, "rejection_reason": "unknown series_id"})
            continue
        season_id = record.get("season_id")
        if season_id is None or pd.isna(season_id):
            rejects.append({**record, "rejection_reason": "missing season_id"})
            continue
        row = {col: record.get(col) for col in _DIM_SEASON_COLS}
        row["season_id"] = int(season_id)
        row["series_id"] = int(series_id)
        rows.append({k: (None if pd.isna(v) else v) for k, v in row.items()})

    count = _upsert(session, "dim_season", ["season_id"], _DIM_SEASON_COLS, rows)
    logger.info(
        "dim_season: upserted %d row(s), rejected %d row(s)", count, len(rejects)
    )
    return count, rejects


def load_dim_episode(
    session: Session, episodes_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Replace (not upsert) every episode row for the series in this Silver partition.

    dim_episode is the second table here to use _replace_by_parent() and the
    first whose parent is not a movie: TMDB renumbers and withdraws episodes,
    so a series' episode set must be able to *shrink* — a pure upsert would
    leave a phantom episode on the show page forever (22_episodes.sql). The
    delete is scoped to the series_ids present in this partition, never a
    blanket wipe.

    series_id is resolved against dim_series; a row whose series_id has no
    dimension row is quarantined, never dropped. Returns (count, rejects).
    """
    valid_series_ids = _existing_ids(session, "dim_series", "series_id")

    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    for record in episodes_df.to_dict("records"):
        series_id = record.get("series_id")
        if pd.isna(series_id) or int(series_id) not in valid_series_ids:
            rejects.append({**record, "rejection_reason": "unknown series_id"})
            continue
        episode_id = record.get("episode_id")
        if episode_id is None or pd.isna(episode_id):
            rejects.append({**record, "rejection_reason": "missing episode_id"})
            continue

        row = {col: record.get(col) for col in _DIM_EPISODE_COLS}
        row["episode_id"] = int(episode_id)
        row["series_id"] = int(series_id)
        row["ingestion_date"] = ingestion_date
        rows.append({k: (None if pd.isna(v) else v) for k, v in row.items()})

    parent_ids = sorted({r["series_id"] for r in rows})
    columns = _DIM_EPISODE_COLS + ["ingestion_date"]
    count = _replace_by_parent(
        session, "dim_episode", "series_id", parent_ids, columns, rows
    )
    logger.info(
        "dim_episode: replaced %d row(s) across %d series, rejected %d row(s)",
        count, len(parent_ids), len(rejects),
    )
    return count, rejects


# --- Task 90: dim_series_video ---------------------------------------------

_SERIES_VIDEO_COLS = [
    "series_id", "video_id", "name", "key", "site", "type", "official",
    "size", "iso_639_1", "iso_3166_1", "published_at",
]


def load_dim_series_video(
    session: Session, videos_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Replace (not upsert) every video row for the shows in this Silver partition.

    An exact copy of load_dim_movie_video()'s logic, series_id in place of
    movie_id — same reasoning: TMDB removes videos and YouTube keys rot, so a
    pure upsert would leave a dead embed on the show page forever.
    _replace_by_parent() deletes every existing row for the series_ids present
    in this partition, then inserts the current set.

    series_id is resolved against dim_series; a row whose series_id has no
    dimension row is quarantined, never dropped. Returns (count, rejects).
    """
    valid_series_ids = _existing_ids(session, "dim_series", "series_id")

    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    for record in videos_df.to_dict("records"):
        series_id = record.get("series_id")
        if pd.isna(series_id) or int(series_id) not in valid_series_ids:
            rejects.append({**record, "rejection_reason": "unknown series_id"})
            continue

        video_id = record.get("video_id")
        if video_id is None or pd.isna(video_id) or not str(video_id).strip():
            rejects.append({**record, "rejection_reason": "missing video_id"})
            continue

        row = {col: record.get(col) for col in _SERIES_VIDEO_COLS}
        row["series_id"] = int(series_id)
        row["video_id"] = str(video_id)
        row["official"] = None if pd.isna(record.get("official")) else bool(record.get("official"))
        row["size"] = None if pd.isna(record.get("size")) else int(record.get("size"))
        row["ingestion_date"] = ingestion_date
        rows.append({k: (None if pd.isna(v) else v) for k, v in row.items()})

    parent_ids = sorted({r["series_id"] for r in rows})
    columns = _SERIES_VIDEO_COLS + ["ingestion_date"]
    count = _replace_by_parent(
        session, "dim_series_video", "series_id", parent_ids, columns, rows
    )
    logger.info(
        "dim_series_video: replaced %d row(s) across %d show(s), rejected %d row(s)",
        count, len(parent_ids), len(rejects),
    )
    return count, rejects


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
    rejected_dir: Path | None = None,
) -> dict[str, int]:
    """Read Silver Parquet for `ingestion_date` and upsert all dimension tables.

    Returns a dict of table name -> row count upserted.

    Most dimensions are derived by drop_duplicates from FK-clean Silver and so
    can't produce rejects. dim_movie_video (Task 74) and dim_season /
    dim_episode (Task 84) are the exceptions: each resolves its parent id
    against the parent dimension and quarantines the misses to `rejected_dir`
    (default config.REJECTED_DIR), the same posture the fact loaders take.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET
    if rejected_dir is None:
        rejected_dir = config.REJECTED_DIR

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
    # Task 72: person bios/vitals. Optional, same as company_details above —
    # a partition written before this Silver transform existed, or a failed
    # transform, degrades to "load dim_person with null detail columns".
    try:
        person_details_df = _read_silver_parquet(
            bucket, "person_details", ingestion_date, "person_details.parquet"
        )
    except Exception as exc:
        logger.warning(
            "No Silver person_details for %s (%s) — loading dim_person "
            "without detail columns", ingestion_date, exc,
        )
        person_details_df = None
    # Task 74: movie trailers/clips. Optional in the same way — a partition
    # written before Task 73 appended `videos` has an empty (or absent)
    # movie_videos file and degrades to "no videos loaded", never crashes.
    try:
        videos_df = _read_silver_parquet(
            bucket, "movie_videos", ingestion_date, "movie_videos.parquet"
        )
    except Exception as exc:
        logger.warning(
            "No Silver movie_videos for %s (%s) — skipping dim_movie_video load",
            ingestion_date, exc,
        )
        videos_df = None

    # Task 79: TV series. Every series Silver source is optional in the same
    # way — a movie-only pipeline run (or the nightly refresh, until Task 85)
    # writes none of them, so all of dim_series / dim_network / the series
    # unions below simply do not run and the 19_series.sql migration need not
    # even be applied yet.
    series_df = _optional_silver(bucket, "series", ingestion_date, "series.parquet")
    series_companies_df = _optional_silver(
        bucket, "series_companies", ingestion_date, "series_companies.parquet"
    )
    series_countries_df = _optional_silver(
        bucket, "series_countries", ingestion_date, "series_countries.parquet"
    )
    series_languages_df = _optional_silver(
        bucket, "series_languages", ingestion_date, "series_languages.parquet"
    )
    networks_df = _optional_silver(bucket, "networks", ingestion_date, "networks.parquet")
    # Task 84: the episode grain. Optional in the same way — no Silver seasons /
    # episodes on a movie-only run, so dim_season / dim_episode simply do not
    # load and the 22_episodes.sql migration need not be applied yet.
    seasons_df = _optional_silver(bucket, "seasons", ingestion_date, "seasons.parquet")
    episodes_df = _optional_silver(bucket, "episodes", ingestion_date, "episodes.parquet")
    # Task 90: show trailers/clips. Optional in the same way as movie_videos —
    # a pre-Task-77 partition (there is none live, but the same posture as
    # every other optional Silver source here) simply has no series_videos
    # file and degrades to "no series videos loaded".
    series_videos_df = _optional_silver(
        bucket, "series_videos", ingestion_date, "series_videos.parquet"
    )

    counts: dict[str, int] = {}
    video_rejects: list[dict[str, Any]] = []
    series_video_rejects: list[dict[str, Any]] = []
    season_rejects: list[dict[str, Any]] = []
    episode_rejects: list[dict[str, Any]] = []
    with get_session() as session:
        # Before dim_movie: dim_movie.collection_id is an FK to this table.
        counts["dim_collection"] = load_dim_collection(session, movies_df)
        counts["dim_movie"] = load_dim_movie(session, movies_df)
        # After dim_movie: dim_movie_video has an FK to it. Replace-on-load,
        # not upsert — a film's video set can shrink (18_movie_videos.sql).
        if videos_df is not None and not videos_df.empty:
            counts["dim_movie_video"], video_rejects = load_dim_movie_video(
                session, videos_df, ingestion_date
            )
        else:
            counts["dim_movie_video"] = 0
        # Task 79: dim_series has no FK; load it beside dim_movie_video. Only
        # when there is series Silver to load — otherwise the 19_series.sql
        # migration need not be applied and assign_slugs() below is skipped too.
        tv = series_df is not None and not series_df.empty
        if tv:
            counts["dim_series"] = load_dim_series(session, series_df)
            if networks_df is not None and not networks_df.empty:
                counts["dim_network"] = load_dim_network(session, networks_df)
            else:
                counts["dim_network"] = 0
            # Task 90: dim_series_video has an FK to dim_series, so it runs
            # after the load_dim_series() call above committed (in-transaction)
            # its rows — the dim_season/dim_episode ordering below, and the
            # dim_movie_video precedent for the movie side.
            if series_videos_df is not None and not series_videos_df.empty:
                counts["dim_series_video"], series_video_rejects = load_dim_series_video(
                    session, series_videos_df, ingestion_date
                )
            else:
                counts["dim_series_video"] = 0
        # Task 84: dim_season / dim_episode have an FK to dim_series, so they
        # run after the load_dim_series() above committed (in-transaction) its
        # rows. Gated on their own Silver files, like dim_movie_video — a
        # movie-only run has neither and adds no counts key. dim_episode uses
        # _replace_by_parent keyed on series_id (a series' episode set can
        # shrink); dim_season uses plain upsert.
        if seasons_df is not None and not seasons_df.empty:
            counts["dim_season"], season_rejects = load_dim_season(session, seasons_df)
        if episodes_df is not None and not episodes_df.empty:
            counts["dim_episode"], episode_rejects = load_dim_episode(
                session, episodes_df, ingestion_date
            )
        counts["dim_person"] = load_dim_person(session, people_df, person_details_df)
        counts["dim_genre"] = load_dim_genre(session, genres_df)
        # Before load_facts.load_bridge_movie_company() / load_bridge_series_company(),
        # both of which have an FK here. series_companies_df unions TV studios in.
        counts["dim_company"] = load_dim_company(
            session, companies_df, company_details_df, series_companies_df
        )
        # Before the movie and series country/language bridges, same reason.
        counts["dim_country"] = load_dim_country(session, countries_df, series_countries_df)
        counts["dim_language"] = load_dim_language(session, languages_df, series_languages_df)
        counts["dim_date"] = load_dim_date(session, calendar_start, calendar_end)
        counts["dim_movie_slugs"] = assign_slugs(session, "dim_movie", "movie_id", "title")
        counts["dim_person_slugs"] = assign_slugs(session, "dim_person", "person_id", "name")
        counts["dim_collection_slugs"] = assign_slugs(session, "dim_collection", "collection_id", "name")
        counts["dim_company_slugs"] = assign_slugs(session, "dim_company", "company_id", "name")
        if tv:
            counts["dim_series_slugs"] = assign_slugs(session, "dim_series", "series_id", "name")
            counts["dim_network_slugs"] = assign_slugs(session, "dim_network", "network_id", "name")

    _write_rejects(video_rejects, "dim_movie_video", ingestion_date, rejected_dir)
    _write_rejects(series_video_rejects, "dim_series_video", ingestion_date, rejected_dir)
    _write_rejects(season_rejects, "dim_season", ingestion_date, rejected_dir)
    _write_rejects(episode_rejects, "dim_episode", ingestion_date, rejected_dir)

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
