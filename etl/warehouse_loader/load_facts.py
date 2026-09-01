"""Warehouse loader: Facts.

Reads the Silver Parquet files for a given ingestion_date, resolves natural
keys against the dimension tables already loaded by load_dimensions.py, and
upserts into three fact tables (fact_movie_metrics, fact_cast, fact_crew).

Rows that fail an FK lookup (reference a movie/genre/date/actor/director not
present in the dimensions) are never inserted and never silently dropped —
they are quarantined to data_quality/rejected/ with a rejection_reason
column, mirroring the pattern used by data_quality/silver_checks.py.

fact_movie_metrics is built by exploding each movie's genre_ids: one row per
(movie_id, date_id, genre_id). date_id is derived from release_date to match
the YYYYMMDD surrogate key produced by load_dim_date().

fact_cast and fact_crew are independent facts built from Silver's
credits_bridge, which stores cast and crew as separate per-person rows.
fact_cast holds one row per (movie_id, actor_id); fact_crew holds one row
per (movie_id, director_id), restricted to crew rows with role == "Director"
(mirroring dim_director, which itself only contains people credited as
director). Earlier this was a single fact_casting table requiring both
actor_id and director_id NOT NULL, built by cross-joining every credited
actor with every credited director per movie — but TMDB's credits endpoint
never pairs an actor with "their" director, so a movie with no director
credit lost its entire cast, not just its director. Splitting the two
removes that coupling: a movie's cast rows no longer depend on whether it
has a resolvable director.

bridge_movie_company (Task 58) is a factless fact table — no measure, just the
existence of a movie/company relationship — built from Silver's
movie_companies link table the same way, resolving both FKs and quarantining
unresolvable rows rather than dropping them.

bridge_movie_country and bridge_movie_language (Task 61) follow the same
pattern from Silver's movie_countries and movie_languages link tables.
bridge_movie_country's grain is (movie_id, country_code, relation) — relation
is resolved as a plain column, not an FK, since it's a fixed two-value tag
("origin"/"production") rather than a reference to another dimension.

fact_movie_rating (Phase 15) loads from two Silver sources at once:
silver/imdb_ratings (source='imdb') and silver/movies' own vote_average/
vote_count columns (source='tmdb'). Both land in the same table at the same
(movie_id, source) grain — one row per film per source, never fanned out by
genre the way fact_movie_metrics is.

person_alias (Task 72) is neither a fact nor a bridge — it attaches one
dimension's repeating text (a person's also_known_as entries) to it. It is
loaded here, alongside the facts, because it follows the same
resolve-FK-against-a-loaded-dimension / quarantine-the-misses shape every
loader in this module uses.

S3 sources:
    silver/movies/ingestion_date=YYYY-MM-DD/movies.parquet
    silver/movie_companies/ingestion_date=YYYY-MM-DD/movie_companies.parquet
    silver/movie_countries/ingestion_date=YYYY-MM-DD/movie_countries.parquet
    silver/movie_languages/ingestion_date=YYYY-MM-DD/movie_languages.parquet
    silver/imdb_ratings/ingestion_date=YYYY-MM-DD/imdb_ratings.parquet
    silver/person_aliases/ingestion_date=YYYY-MM-DD/person_aliases.parquet
    silver/credits_bridge/ingestion_date=YYYY-MM-DD/credits_bridge.parquet

All fact tables carry an ingestion_date column recording which Silver
partition last wrote each row. It does not participate in any table's
PRIMARY KEY — the existing composite PK (movie_id, date_id, genre_id) /
(movie_id, actor_id) / (movie_id, director_id) already guards against
duplicate inserts via the ON CONFLICT upsert, so re-running the same or an
earlier partition is a no-op update rather than a new row.

Usage:
    python -m etl.warehouse_loader.load_facts
    python -m etl.warehouse_loader.load_facts --date 2026-06-22
    python -m etl.warehouse_loader.load_facts --incremental
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

import config
from etl.incremental import pending_partitions, set_watermark
from etl.warehouse_loader.common import _existing_ids, _existing_str_ids, _read_silver_parquet, _upsert
from warehouse.db import get_session

logger = logging.getLogger(__name__)

_LOADER_NAME = "load_facts"
_WATERMARK_ENTITY = "movies"  # reference entity used to discover new Silver partitions


def _records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert NaN/NaT-bearing values in a list of dicts to None."""
    return [{k: (None if pd.isna(v) else v) for k, v in row.items()} for row in rows]


def _write_rejects(rejects: list[dict[str, Any]], entity: str, ingestion_date: dt.date,
                    rejected_dir: Path) -> Path | None:
    """Write quarantined rows to a local Parquet file. Returns the path, or None if empty."""
    if not rejects:
        return None
    df = pd.DataFrame(rejects)
    rejected_dir.mkdir(parents=True, exist_ok=True)
    path = rejected_dir / f"{entity}_rejected_{ingestion_date.isoformat()}.parquet"
    df.to_parquet(path, engine="pyarrow", index=False)
    logger.warning("Wrote %d rejected row(s) for entity=%s to %s", len(df), entity, path)
    return path


def _build_movie_metrics_rows(
    movies_df: pd.DataFrame,
    valid_movie_ids: set[int],
    valid_date_ids: set[int],
    valid_genre_ids: set[int],
    ingestion_date: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Explode Silver movies into (movie_id, date_id, genre_id) fact rows.

    Returns (rows, rejects). A row is rejected if its movie_id/date_id/
    genre_id cannot be resolved against the dimension tables.
    """
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []

    for record in movies_df.to_dict("records"):
        movie_id = record["movie_id"]
        base = {
            "movie_id": movie_id,
            "rating": record.get("vote_average"),
            "vote_count": record.get("vote_count"),
            "revenue": record.get("revenue"),
            "budget": record.get("budget"),
            "popularity": record.get("popularity"),
        }

        if pd.isna(movie_id) or int(movie_id) not in valid_movie_ids:
            rejects.append({**base, "rejection_reason": "unknown movie_id"})
            continue

        release_date = record.get("release_date")
        if release_date is None or pd.isna(release_date):
            rejects.append({**base, "rejection_reason": "missing release_date"})
            continue
        release_ts = pd.Timestamp(release_date)
        date_id = release_ts.year * 10_000 + release_ts.month * 100 + release_ts.day
        if date_id not in valid_date_ids:
            rejects.append({**base, "date_id": date_id, "rejection_reason": "unknown date_id"})
            continue

        genre_ids = record.get("genre_ids")
        genre_ids = list(genre_ids) if genre_ids is not None else []
        if not genre_ids:
            rejects.append({**base, "date_id": date_id, "rejection_reason": "no genres"})
            continue

        for genre_id in genre_ids:
            if genre_id is None or int(genre_id) not in valid_genre_ids:
                rejects.append({
                    **base, "date_id": date_id, "genre_id": genre_id,
                    "rejection_reason": "unknown genre_id",
                })
                continue
            rows.append({
                "movie_id": int(movie_id),
                "date_id": date_id,
                "genre_id": int(genre_id),
                "rating": base["rating"],
                "vote_count": base["vote_count"],
                "revenue": base["revenue"],
                "budget": base["budget"],
                "popularity": base["popularity"],
                "ingestion_date": ingestion_date,
            })

    return rows, rejects


def _build_credit_rows(
    bridge_df: pd.DataFrame,
    valid_movie_ids: set[int],
    valid_person_ids: set[int],
    ingestion_date: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve every Silver credits_bridge row into a fact_credit row.

    No job or department filter: every credit TMDB published is kept. The old
    loaders discarded ~99% of crew here by keeping only role == "Director".

    Cast rows are normalised to department="Acting", job="Actor" so that "what
    they did" is expressed the same way for everyone; the part played moves to
    `character_name`, which is where it belongs once `job` exists.

    Returns (rows, rejects).
    """
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    # fact_credit's PK is finer than the bridge's only for cast, where one
    # person can hold two character credits on a film. Collapse those to the
    # top-billed one rather than letting the upsert pick arbitrarily.
    seen: set[tuple[int, int, str, str]] = set()
    collapsed = 0

    # Sorting by billing makes "keep the first one seen" mean "keep the
    # top-billed one" rather than "keep whichever Bronze row sorted first".
    bridge_df = bridge_df.sort_values("ordering", na_position="last", kind="stable")

    for credit in bridge_df.to_dict("records"):
        movie_id = credit["movie_id"]
        if pd.isna(movie_id) or int(movie_id) not in valid_movie_ids:
            rejects.append({**credit, "rejection_reason": "unknown movie_id"})
            continue

        person_id = credit["person_id"]
        if pd.isna(person_id) or int(person_id) not in valid_person_ids:
            rejects.append({**credit, "rejection_reason": "unknown person_id"})
            continue

        is_cast = credit.get("credit_type") == "cast"
        department = credit.get("department")
        job = "Actor" if is_cast else credit.get("role")
        if not department or not job:
            rejects.append({**credit, "rejection_reason": "missing department or job"})
            continue

        key = (int(movie_id), int(person_id), department, job)
        if key in seen:
            collapsed += 1
            continue
        seen.add(key)

        rows.append({
            "movie_id": int(movie_id),
            "person_id": int(person_id),
            "department": department,
            "job": job,
            "character_name": credit.get("role") if is_cast else None,
            "ordering": credit.get("ordering") if is_cast else None,
            "ingestion_date": ingestion_date,
        })

    if collapsed:
        logger.info(
            "fact_credit: collapsed %d repeat (movie, person, department, job) credit(s) "
            "— usually one actor playing two characters",
            collapsed,
        )
    return rows, rejects


def _build_bridge_company_rows(
    companies_df: pd.DataFrame,
    valid_movie_ids: set[int],
    valid_company_ids: set[int],
    ingestion_date: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve every Silver movie_companies row into a bridge_movie_company row.

    Returns (rows, rejects). Unlike load_gold's collaboration edges, a bridge
    row's ids come from the same Silver partition dim_company/dim_movie were
    just loaded from — so a miss here is a real, quarantinable data problem,
    not a staleness signal, and follows the same reject-don't-drop convention
    as every other fact/bridge loader in this module.
    """
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []

    for link in companies_df.to_dict("records"):
        movie_id = link["movie_id"]
        company_id = link["company_id"]

        if pd.isna(movie_id) or int(movie_id) not in valid_movie_ids:
            rejects.append({**link, "rejection_reason": "unknown movie_id"})
            continue
        if pd.isna(company_id) or int(company_id) not in valid_company_ids:
            rejects.append({**link, "rejection_reason": "unknown company_id"})
            continue

        rows.append({
            "movie_id": int(movie_id),
            "company_id": int(company_id),
            "ingestion_date": ingestion_date,
        })

    return rows, rejects


def _build_bridge_country_rows(
    countries_df: pd.DataFrame,
    valid_movie_ids: set[int],
    valid_country_codes: set[str],
    ingestion_date: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve every Silver movie_countries row into a bridge_movie_country row.

    relation is carried through as-is, not FK-resolved — it's a fixed tag,
    not a reference to dim_country. A country_code with no dim_country row
    (Task 57: an origin-only code with no name anywhere in the partition) is
    quarantined the same as any other unresolvable FK.
    """
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []

    for link in countries_df.to_dict("records"):
        movie_id = link["movie_id"]
        country_code = link["country_code"]

        if pd.isna(movie_id) or int(movie_id) not in valid_movie_ids:
            rejects.append({**link, "rejection_reason": "unknown movie_id"})
            continue
        if pd.isna(country_code) or country_code not in valid_country_codes:
            rejects.append({**link, "rejection_reason": "unknown country_code"})
            continue

        rows.append({
            "movie_id": int(movie_id),
            "country_code": country_code,
            "relation": link.get("relation"),
            "ingestion_date": ingestion_date,
        })

    return rows, rejects


def _build_bridge_language_rows(
    languages_df: pd.DataFrame,
    valid_movie_ids: set[int],
    valid_language_codes: set[str],
    ingestion_date: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve every Silver movie_languages row into a bridge_movie_language row."""
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []

    for link in languages_df.to_dict("records"):
        movie_id = link["movie_id"]
        language_code = link["language_code"]

        if pd.isna(movie_id) or int(movie_id) not in valid_movie_ids:
            rejects.append({**link, "rejection_reason": "unknown movie_id"})
            continue
        if pd.isna(language_code) or language_code not in valid_language_codes:
            rejects.append({**link, "rejection_reason": "unknown language_code"})
            continue

        rows.append({
            "movie_id": int(movie_id),
            "language_code": language_code,
            "ingestion_date": ingestion_date,
        })

    return rows, rejects


def _build_person_alias_rows(
    aliases_df: pd.DataFrame,
    valid_person_ids: set[int],
    ingestion_date: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve every Silver person_aliases row into a person_alias row.

    person_alias is neither a fact nor a bridge — it attaches repeating text
    (also_known_as entries) to one dimension. It still follows the same
    resolve-FK-or-quarantine convention as every other loader here: an alias
    whose person_id has no dim_person row is quarantined, never dropped.

    Returns (rows, rejects).
    """
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []

    for record in aliases_df.to_dict("records"):
        person_id = record.get("person_id")
        if pd.isna(person_id) or int(person_id) not in valid_person_ids:
            rejects.append({**record, "rejection_reason": "unknown person_id"})
            continue

        alias = record.get("alias")
        if alias is None or pd.isna(alias) or not str(alias).strip():
            rejects.append({**record, "rejection_reason": "missing alias"})
            continue

        rows.append({
            "person_id": int(person_id),
            "alias": str(alias),
            "ordering": None if pd.isna(record.get("ordering")) else int(record["ordering"]),
            "ingestion_date": ingestion_date,
        })

    return rows, rejects


def load_person_alias(
    session: Session, aliases_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Resolve and upsert Silver person_aliases into person_alias.

    Must run after load_dim_person() has committed, since person_id is
    resolved against the live dimension. Returns (count, rejects).
    """
    valid_person_ids = _existing_ids(session, "dim_person", "person_id")

    rows, rejects = _build_person_alias_rows(
        aliases_df, valid_person_ids, ingestion_date
    )
    columns = ["person_id", "alias", "ordering", "ingestion_date"]
    count = _upsert(session, "person_alias", ["person_id", "alias"], columns, _records(rows))
    logger.info("person_alias: upserted %d row(s), rejected %d row(s)", count, len(rejects))
    return count, rejects


def load_bridge_movie_country(
    session: Session, countries_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Resolve and upsert Silver movie_countries into bridge_movie_country.

    Must run after load_dim_movie() and load_dim_country() have committed.
    Returns (count, rejects).
    """
    valid_movie_ids = _existing_ids(session, "dim_movie", "movie_id")
    valid_country_codes = _existing_str_ids(session, "dim_country", "country_code")

    rows, rejects = _build_bridge_country_rows(
        countries_df, valid_movie_ids, valid_country_codes, ingestion_date
    )
    columns = ["movie_id", "country_code", "relation", "ingestion_date"]
    count = _upsert(
        session, "bridge_movie_country", ["movie_id", "country_code", "relation"], columns, _records(rows)
    )
    logger.info("bridge_movie_country: upserted %d row(s), rejected %d row(s)", count, len(rejects))
    return count, rejects


def load_bridge_movie_language(
    session: Session, languages_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Resolve and upsert Silver movie_languages into bridge_movie_language.

    Must run after load_dim_movie() and load_dim_language() have committed.
    Returns (count, rejects).
    """
    valid_movie_ids = _existing_ids(session, "dim_movie", "movie_id")
    valid_language_codes = _existing_str_ids(session, "dim_language", "language_code")

    rows, rejects = _build_bridge_language_rows(
        languages_df, valid_movie_ids, valid_language_codes, ingestion_date
    )
    columns = ["movie_id", "language_code", "ingestion_date"]
    count = _upsert(
        session, "bridge_movie_language", ["movie_id", "language_code"], columns, _records(rows)
    )
    logger.info("bridge_movie_language: upserted %d row(s), rejected %d row(s)", count, len(rejects))
    return count, rejects


def load_bridge_movie_company(
    session: Session, companies_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Resolve and upsert Silver movie_companies into bridge_movie_company.

    Must run after both load_dim_movie() and load_dim_company() have
    committed, since both FKs are resolved against the live dimensions.
    Returns (count, rejects).
    """
    valid_movie_ids = _existing_ids(session, "dim_movie", "movie_id")
    valid_company_ids = _existing_ids(session, "dim_company", "company_id")

    rows, rejects = _build_bridge_company_rows(
        companies_df, valid_movie_ids, valid_company_ids, ingestion_date
    )
    columns = ["movie_id", "company_id", "ingestion_date"]
    count = _upsert(session, "bridge_movie_company", ["movie_id", "company_id"], columns, _records(rows))
    logger.info("bridge_movie_company: upserted %d row(s), rejected %d row(s)", count, len(rejects))
    return count, rejects


def _build_movie_rating_rows(
    ratings_df: pd.DataFrame,
    movies_df: pd.DataFrame,
    valid_movie_ids: set[int],
    ingestion_date: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build fact_movie_rating rows from both sources at once.

    silver/imdb_ratings -> source='imdb'; silver/movies' own vote_average/
    vote_count -> source='tmdb'. Loading TMDB here too, not just IMDb, is
    what makes this table the single answer to "what is this film rated"
    rather than a second partial answer sitting alongside
    fact_movie_metrics. Both inputs are already one row per movie_id (Silver
    guarantees it for movies.parquet; imdb_ratings.parquet is built from an
    inner join against that same one-row-per-movie table), so no dedupe is
    needed here — the grain is correct by construction.

    Returns (rows, rejects).
    """
    rows: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []

    for record in ratings_df.to_dict("records"):
        movie_id = record.get("movie_id")
        if pd.isna(movie_id) or int(movie_id) not in valid_movie_ids:
            rejects.append({**record, "source": "imdb", "rejection_reason": "unknown movie_id"})
            continue
        rows.append({
            "movie_id": int(movie_id),
            "source": "imdb",
            "rating": record.get("rating"),
            "vote_count": record.get("vote_count"),
            "ingestion_date": ingestion_date,
        })

    for record in movies_df.to_dict("records"):
        movie_id = record.get("movie_id")
        if pd.isna(movie_id) or int(movie_id) not in valid_movie_ids:
            rejects.append({
                "movie_id": movie_id,
                "source": "tmdb",
                "rating": record.get("vote_average"),
                "vote_count": record.get("vote_count"),
                "rejection_reason": "unknown movie_id",
            })
            continue
        rows.append({
            "movie_id": int(movie_id),
            "source": "tmdb",
            "rating": record.get("vote_average"),
            "vote_count": record.get("vote_count"),
            "ingestion_date": ingestion_date,
        })

    return rows, rejects


def load_fact_movie_rating(
    session: Session, ratings_df: pd.DataFrame, movies_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Resolve and upsert both IMDb and TMDB ratings into fact_movie_rating.

    Must run after load_dim_movie() has committed, since movie_id is
    resolved against the live dimension. Returns (count, rejects).
    """
    valid_movie_ids = _existing_ids(session, "dim_movie", "movie_id")

    rows, rejects = _build_movie_rating_rows(
        ratings_df, movies_df, valid_movie_ids, ingestion_date
    )
    columns = ["movie_id", "source", "rating", "vote_count", "ingestion_date"]
    count = _upsert(session, "fact_movie_rating", ["movie_id", "source"], columns, _records(rows))
    logger.info("fact_movie_rating: upserted %d row(s), rejected %d row(s)", count, len(rejects))
    return count, rejects


def load_fact_movie_metrics(
    session: Session, movies_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Resolve and upsert Silver movies into fact_movie_metrics. Returns (count, rejects)."""
    valid_movie_ids = _existing_ids(session, "dim_movie", "movie_id")
    valid_date_ids = _existing_ids(session, "dim_date", "date_id")
    valid_genre_ids = _existing_ids(session, "dim_genre", "genre_id")

    rows, rejects = _build_movie_metrics_rows(
        movies_df, valid_movie_ids, valid_date_ids, valid_genre_ids, ingestion_date,
    )
    columns = ["movie_id", "date_id", "genre_id", "rating", "vote_count", "revenue",
               "budget", "popularity", "ingestion_date"]
    count = _upsert(session, "fact_movie_metrics", ["movie_id", "date_id", "genre_id"], columns, _records(rows))
    logger.info(
        "fact_movie_metrics: upserted %d row(s), rejected %d row(s)", count, len(rejects)
    )
    return count, rejects


def load_fact_credit(
    session: Session, bridge_df: pd.DataFrame, ingestion_date: dt.date,
) -> tuple[int, list[dict[str, Any]]]:
    """Resolve and upsert every Silver credits_bridge row into fact_credit.

    Returns (count, rejects).
    """
    valid_movie_ids = _existing_ids(session, "dim_movie", "movie_id")
    valid_person_ids = _existing_ids(session, "dim_person", "person_id")

    rows, rejects = _build_credit_rows(
        bridge_df, valid_movie_ids, valid_person_ids, ingestion_date
    )
    columns = ["movie_id", "person_id", "department", "job", "character_name",
               "ordering", "ingestion_date"]
    count = _upsert(
        session, "fact_credit",
        ["movie_id", "person_id", "department", "job"], columns, _records(rows),
    )
    logger.info("fact_credit: upserted %d row(s), rejected %d row(s)", count, len(rejects))
    return count, rejects


def load_facts(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    rejected_dir: Path | None = None,
) -> dict[str, int]:
    """Read Silver Parquet for `ingestion_date`, resolve FKs, and upsert both fact tables.

    Returns a dict of table name -> row count upserted.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET
    if rejected_dir is None:
        rejected_dir = config.REJECTED_DIR

    t0 = time.monotonic()
    logger.info("Starting fact load for ingestion_date=%s", ingestion_date)

    movies_df = _read_silver_parquet(bucket, "movies", ingestion_date, "movies.parquet")
    bridge_df = _read_silver_parquet(bucket, "credits_bridge", ingestion_date, "credits_bridge.parquet")
    companies_df = _read_silver_parquet(
        bucket, "movie_companies", ingestion_date, "movie_companies.parquet"
    )
    countries_df = _read_silver_parquet(
        bucket, "movie_countries", ingestion_date, "movie_countries.parquet"
    )
    languages_df = _read_silver_parquet(
        bucket, "movie_languages", ingestion_date, "movie_languages.parquet"
    )
    ratings_df = _read_silver_parquet(
        bucket, "imdb_ratings", ingestion_date, "imdb_ratings.parquet"
    )
    # Task 72: person aliases. Optional — a partition written before this
    # Silver transform existed degrades to "no alias rows for this date",
    # never crashes the fact load.
    try:
        aliases_df = _read_silver_parquet(
            bucket, "person_aliases", ingestion_date, "person_aliases.parquet"
        )
    except Exception as exc:
        logger.warning(
            "No Silver person_aliases for %s (%s) — skipping person_alias load",
            ingestion_date, exc,
        )
        aliases_df = pd.DataFrame(
            columns=["person_id", "alias", "ordering"]
        )

    counts: dict[str, int] = {}
    with get_session() as session:
        counts["fact_movie_metrics"], metrics_rejects = load_fact_movie_metrics(session, movies_df, ingestion_date)
        counts["fact_movie_rating"], rating_rejects = load_fact_movie_rating(
            session, ratings_df, movies_df, ingestion_date
        )
        counts["fact_credit"], credit_rejects = load_fact_credit(session, bridge_df, ingestion_date)
        counts["person_alias"], alias_rejects = load_person_alias(
            session, aliases_df, ingestion_date
        )
        counts["bridge_movie_company"], company_rejects = load_bridge_movie_company(
            session, companies_df, ingestion_date
        )
        counts["bridge_movie_country"], country_rejects = load_bridge_movie_country(
            session, countries_df, ingestion_date
        )
        counts["bridge_movie_language"], language_rejects = load_bridge_movie_language(
            session, languages_df, ingestion_date
        )

    _write_rejects(metrics_rejects, "fact_movie_metrics", ingestion_date, rejected_dir)
    _write_rejects(rating_rejects, "fact_movie_rating", ingestion_date, rejected_dir)
    _write_rejects(credit_rejects, "fact_credit", ingestion_date, rejected_dir)
    _write_rejects(alias_rejects, "person_alias", ingestion_date, rejected_dir)
    _write_rejects(company_rejects, "bridge_movie_company", ingestion_date, rejected_dir)
    _write_rejects(country_rejects, "bridge_movie_country", ingestion_date, rejected_dir)
    _write_rejects(language_rejects, "bridge_movie_language", ingestion_date, rejected_dir)

    elapsed = time.monotonic() - t0
    logger.info(
        "Fact load complete: %s in %.2fs",
        ", ".join(f"{k}={v}" for k, v in counts.items()), elapsed,
    )
    return counts


def load_facts_incremental(
    bucket: str | None = None,
    rejected_dir: Path | None = None,
) -> dict[str, dict[str, int]]:
    """Process every Silver partition newer than this loader's watermark, in order.

    Mirrors load_dimensions_incremental(): discovers pending dates via
    etl.incremental.pending_partitions(), runs load_facts() for each, and
    advances the watermark only after each date completes successfully.

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
        counts = load_facts(ingestion_date=ingestion_date, bucket=bucket, rejected_dir=rejected_dir)
        with get_session() as session:
            set_watermark(session, _LOADER_NAME, ingestion_date)
        logger.info("Watermark for %s advanced to %s", _LOADER_NAME, ingestion_date)
        results[ingestion_date.isoformat()] = counts

    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve Silver Parquet against dimensions and upsert the PostgreSQL fact tables."
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
    setup_logging("load_facts")
    args = _parse_args()
    if args.incremental:
        load_facts_incremental()
    else:
        load_facts(ingestion_date=args.date)
