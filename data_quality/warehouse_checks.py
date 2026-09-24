"""End-to-end data quality validation for the warehouse layer.

Runs three families of checks after the dimension and fact loaders have run
for a given ingestion_date (the third, translations, is described at the end):

    1. FK integrity — every fact row's foreign keys resolve to an existing
       dimension row. The database's FOREIGN KEY constraints (see
       warehouse/ddl/02_facts.sql) already forbid this at insert time; this
       check is a defense-in-depth sanity pass that would catch corruption
       introduced outside the loaders (manual edits, restored backups,
       constraints disabled for a bulk load).

    2. Row-count sanity, Bronze -> Silver -> Gold -> Warehouse. For a given
       ingestion_date:
         - Silver must never have *more* rows than Bronze provided (a
           transform cannot fabricate records).
         - Warehouse dimension tables must never have *fewer* rows than the
           Silver partition just loaded — dimensions accumulate across every
           ingestion_date via upsert (see etl/warehouse_loader/load_dimensions.py),
           so they can only grow or hold steady, never shrink below what was
           just loaded.
         - Every Gold dataset must exist and be non-empty for the date
           whenever the Silver movies partition was non-empty.
         - Every fact table must be non-empty whenever the Silver data that
           feeds it was non-empty — a loader that silently produced zero rows
           from real input is a bug, not "clean data" (genuine zero-row loads
           only happen when Silver itself is empty). Checked against the whole
           table, not this date's rows: an unchanged row keeps its old
           ingestion_date (see _upsert), so a no-change night tags none.

    3. Translations (Task 93) — the four *_translation tables carry only
       languages the site ships, and are non-empty whenever their Silver file
       was. See check_translation_sanity().

Produces one CheckResult per check; the overall run passes only if every
CheckResult has passed=True.

Usage:
    python -m data_quality.warehouse_checks
    python -m data_quality.warehouse_checks --date 2026-06-22
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import logging
import time
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from etl import s3_utils
from etl.translations import TRANSLATION_LANGUAGES
from etl.warehouse_loader.common import _read_silver_parquet
from warehouse.db import get_session

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    check: str
    passed: bool
    detail: str


# ---------------------------------------------------------------------------
# 1. FK integrity
# ---------------------------------------------------------------------------

# (fact_table, fk_column, dim_table, dim_pk_column)
_FK_CHECKS = [
    ("fact_movie_metrics", "movie_id", "dim_movie", "movie_id"),
    ("fact_movie_metrics", "date_id", "dim_date", "date_id"),
    ("fact_movie_metrics", "genre_id", "dim_genre", "genre_id"),
    ("fact_credit", "movie_id", "dim_movie", "movie_id"),
    ("fact_credit", "person_id", "dim_person", "person_id"),
    ("bridge_movie_company", "movie_id", "dim_movie", "movie_id"),
    ("bridge_movie_company", "company_id", "dim_company", "company_id"),
    ("bridge_movie_country", "movie_id", "dim_movie", "movie_id"),
    ("bridge_movie_country", "country_code", "dim_country", "country_code"),
    ("bridge_movie_language", "movie_id", "dim_movie", "movie_id"),
    ("bridge_movie_language", "language_code", "dim_language", "language_code"),
    ("fact_movie_rating", "movie_id", "dim_movie", "movie_id"),
    ("dim_movie_video", "movie_id", "dim_movie", "movie_id"),
    # Task 79: the series bridges. series_id -> dim_series is checked once via
    # the genre bridge as representative (every series bridge shares that FK);
    # the other five entries cover each distinct entity linkage once. On a
    # movie-only warehouse every bridge_series_* table is empty, so these pass
    # trivially (0 orphan rows) — the dim_movie_video precedent from Task 74.
    ("bridge_series_genre", "series_id", "dim_series", "series_id"),
    ("bridge_series_genre", "genre_id", "dim_genre", "genre_id"),
    ("bridge_series_company", "company_id", "dim_company", "company_id"),
    ("bridge_series_country", "country_code", "dim_country", "country_code"),
    ("bridge_series_language", "language_code", "dim_language", "language_code"),
    ("bridge_series_network", "network_id", "dim_network", "network_id"),
    # Task 80: fact_series_credit. Empty on a movie-only warehouse, so both
    # pass trivially until the first --with-tv load — the same free ride.
    ("fact_series_credit", "series_id", "dim_series", "series_id"),
    ("fact_series_credit", "person_id", "dim_person", "person_id"),
    # Task 81: fact_series_rating. Same free ride until the first --with-tv load.
    ("fact_series_rating", "series_id", "dim_series", "series_id"),
    # Task 84: the episode grain. dim_season / dim_episode reference dim_series;
    # fact_episode_rating references dim_episode. All empty on a movie-only
    # warehouse, so all three pass trivially until the first --with-tv load.
    ("dim_season", "series_id", "dim_series", "series_id"),
    ("dim_episode", "series_id", "dim_series", "series_id"),
    ("fact_episode_rating", "episode_id", "dim_episode", "episode_id"),
    # Task 90: dim_series_video, the dim_movie_video precedent's TV twin.
    # Empty on a warehouse with no series videos loaded yet, so this passes
    # trivially until the first load.
    ("dim_series_video", "series_id", "dim_series", "series_id"),
    # Task 93: the four translation tables. Empty until the first partition
    # carrying translations is loaded, so they pass trivially until then.
    ("movie_translation", "movie_id", "dim_movie", "movie_id"),
    ("person_translation", "person_id", "dim_person", "person_id"),
    ("genre_translation", "genre_id", "dim_genre", "genre_id"),
    ("country_translation", "country_code", "dim_country", "country_code"),
]


def _count_orphans(session: Session, fact_table: str, fk_col: str,
                    dim_table: str, dim_pk: str) -> int:
    """Count rows in fact_table whose fk_col has no matching row in dim_table."""
    sql = (
        f"SELECT COUNT(*) FROM {fact_table} f "
        f"LEFT JOIN {dim_table} d ON f.{fk_col} = d.{dim_pk} "
        f"WHERE d.{dim_pk} IS NULL"
    )
    return session.execute(text(sql)).scalar()


def check_fk_integrity(session: Session) -> list[CheckResult]:
    """Verify every fact table foreign key resolves to an existing dimension row."""
    results: list[CheckResult] = []
    for fact_table, fk_col, dim_table, dim_pk in _FK_CHECKS:
        orphans = _count_orphans(session, fact_table, fk_col, dim_table, dim_pk)
        check_name = f"fk:{fact_table}.{fk_col}->{dim_table}.{dim_pk}"
        if orphans:
            results.append(CheckResult(check_name, False,
                f"{orphans} row(s) in {fact_table} have {fk_col} not present in {dim_table}"))
            logger.error("[%s] FAIL — %d orphan row(s)", check_name, orphans)
        else:
            results.append(CheckResult(check_name, True,
                f"All {fact_table}.{fk_col} values resolve to {dim_table}.{dim_pk}"))
            logger.info("[%s] OK", check_name)
    return results


# ---------------------------------------------------------------------------
# 2. Row-count sanity — Bronze
# ---------------------------------------------------------------------------

def _count_s3_objects(bucket: str, prefix: str) -> int:
    """Count objects under an S3 prefix, ignoring the prefix itself if present as a key."""
    client = s3_utils.get_s3_client()
    paginator = client.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].rstrip("/") != prefix.rstrip("/"):
                count += 1
    return count


def _bronze_movie_count(bucket: str, ingestion_date: dt.date) -> int:
    """Count Bronze movie_details files (one per movie) for a date."""
    prefix = s3_utils.build_path("bronze", "movie_details", ingestion_date, "")
    return _count_s3_objects(bucket, prefix)


def _bronze_credits_file_count(bucket: str, ingestion_date: dt.date) -> int:
    """Count Bronze credits files (one per movie) for a date."""
    prefix = s3_utils.build_path("bronze", "credits", ingestion_date, "")
    return _count_s3_objects(bucket, prefix)


def _bronze_imdb_ratings_row_count(bucket: str, ingestion_date: dt.date) -> int:
    """Count rows in the single Bronze IMDb ratings snapshot for a date. 0 if missing.

    Unlike every other Bronze source here, this one is a single bulk file
    rather than one-per-entity — so "how many rows did Bronze provide" means
    parsing the file itself, not counting S3 objects.
    """
    key = s3_utils.build_path("bronze", "imdb_ratings", ingestion_date, "title.ratings.tsv.gz")
    client = s3_utils.get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        raw = response["Body"].read()
        return len(pd.read_csv(io.BytesIO(raw), sep="\t", compression="gzip", usecols=[0]))
    except Exception:
        return 0


def _bronze_genre_ids(bucket: str, ingestion_date: dt.date, filename: str) -> set[int]:
    """genre ids from one Bronze genres payload for a date. Empty set if missing."""
    key = s3_utils.build_path("bronze", "genres", ingestion_date, filename)
    client = s3_utils.get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.NoSuchKey:
        return set()
    except Exception:
        return set()
    payload = json.loads(response["Body"].read())
    return {g["id"] for g in payload.get("genres", []) if g.get("id") is not None}


def _bronze_genre_count(bucket: str, ingestion_date: dt.date) -> int:
    """Distinct genre ids Bronze provided for a date: genres.json, unioned with
    genres_tv.json when present (Task 78's with_tv merge — 8 ids are shared
    between the two TMDB lists, so summing the two files' lengths would
    overcount; transform_genres dedupes on genre_id and this must match it).
    0 if the movie file itself is missing."""
    ids = _bronze_genre_ids(bucket, ingestion_date, "genres.json")
    ids |= _bronze_genre_ids(bucket, ingestion_date, "genres_tv.json")
    return len(ids)


# ---------------------------------------------------------------------------
# 2. Row-count sanity — Warehouse
# ---------------------------------------------------------------------------

def _table_row_count(session: Session, table: str) -> int:
    return session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def _fact_loaded_count(session: Session, table: str) -> int:
    """Rows in `table`, regardless of which run wrote them.

    Not ``WHERE ingestion_date = <run date>``: _upsert() leaves an unchanged
    row alone, so a night where nothing changed legitimately tags zero rows
    with that night's date. The guard is "non-empty Silver never yields an
    empty table"; the silver_to_warehouse checks cover the cumulative count.
    """
    return session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


# ---------------------------------------------------------------------------
# 2. Row-count sanity — per-entity checks
# ---------------------------------------------------------------------------

def _check_entity_counts(
    session: Session, bucket: str, ingestion_date: dt.date,
    entity_label: str, bronze_count: int,
    silver_entity: str, silver_filename: str, warehouse_table: str,
) -> list[CheckResult]:
    """Bronze->Silver (no fabrication) and Silver->Warehouse (no shrinkage) checks."""
    results: list[CheckResult] = []
    try:
        silver_count = len(_read_silver_parquet(bucket, silver_entity, ingestion_date, silver_filename))
    except Exception as exc:
        results.append(CheckResult(f"rowcount:{entity_label}:bronze_to_silver", False,
            f"Could not read Silver {silver_entity}: {exc}"))
        logger.error("[rowcount:%s] could not read Silver: %s", entity_label, exc)
        return results

    b2s_name = f"rowcount:{entity_label}:bronze_to_silver"
    if silver_count > bronze_count:
        results.append(CheckResult(b2s_name, False,
            f"Silver has {silver_count} row(s) but Bronze only provided {bronze_count}"))
        logger.error("[%s] FAIL — silver=%d > bronze=%d", b2s_name, silver_count, bronze_count)
    else:
        results.append(CheckResult(b2s_name, True, f"Bronze={bronze_count}, Silver={silver_count}"))
        logger.info("[%s] OK (bronze=%d, silver=%d)", b2s_name, bronze_count, silver_count)

    s2w_name = f"rowcount:{entity_label}:silver_to_warehouse"
    warehouse_count = _table_row_count(session, warehouse_table)
    if warehouse_count < silver_count:
        results.append(CheckResult(s2w_name, False,
            f"{warehouse_table} has only {warehouse_count} row(s), fewer than the "
            f"{silver_count} just loaded from Silver"))
        logger.error("[%s] FAIL — warehouse=%d < silver=%d", s2w_name, warehouse_count, silver_count)
    else:
        results.append(CheckResult(s2w_name, True,
            f"Silver={silver_count}, {warehouse_table}={warehouse_count} (cumulative)"))
        logger.info("[%s] OK (silver=%d, warehouse=%d)", s2w_name, silver_count, warehouse_count)

    return results


def check_row_count_sanity(session: Session, bucket: str, ingestion_date: dt.date) -> list[CheckResult]:
    """Row-count sanity for every dimension-backed Silver entity."""
    results: list[CheckResult] = []

    results.extend(_check_entity_counts(
        session, bucket, ingestion_date,
        entity_label="movies", bronze_count=_bronze_movie_count(bucket, ingestion_date),
        silver_entity="movies", silver_filename="movies.parquet", warehouse_table="dim_movie",
    ))
    results.extend(_check_entity_counts(
        session, bucket, ingestion_date,
        entity_label="genres", bronze_count=_bronze_genre_count(bucket, ingestion_date),
        silver_entity="genres", silver_filename="genres.parquet", warehouse_table="dim_genre",
    ))

    # Actors/directors: Bronze credits files are one-per-movie, not one-per-person, so a
    # strict silver<=bronze count comparison doesn't apply. We only assert that Silver
    # people rows never appear out of nowhere (Bronze credits must exist), and that the
    # warehouse has accumulated at least what Silver just produced.
    bronze_credits_files = _bronze_credits_file_count(bucket, ingestion_date)
    for entity_label, silver_entity, filename, warehouse_table in [
        ("people", "people", "people.parquet", "dim_person"),
    ]:
        try:
            silver_count = len(_read_silver_parquet(bucket, silver_entity, ingestion_date, filename))
        except Exception as exc:
            results.append(CheckResult(f"rowcount:{entity_label}:bronze_to_silver", False,
                f"Could not read Silver {silver_entity}: {exc}"))
            logger.error("[rowcount:%s] could not read Silver: %s", entity_label, exc)
            continue

        b2s_name = f"rowcount:{entity_label}:bronze_to_silver"
        if bronze_credits_files == 0 and silver_count > 0:
            results.append(CheckResult(b2s_name, False,
                f"Silver has {silver_count} {entity_label} row(s) but no Bronze credits files were found"))
            logger.error("[%s] FAIL — silver=%d but bronze credits files=0", b2s_name, silver_count)
        else:
            results.append(CheckResult(b2s_name, True,
                f"Bronze credits files={bronze_credits_files}, Silver {entity_label}={silver_count}"))
            logger.info("[%s] OK", b2s_name)

        s2w_name = f"rowcount:{entity_label}:silver_to_warehouse"
        warehouse_count = _table_row_count(session, warehouse_table)
        if warehouse_count < silver_count:
            results.append(CheckResult(s2w_name, False,
                f"{warehouse_table} has only {warehouse_count} row(s), fewer than the "
                f"{silver_count} just loaded from Silver"))
            logger.error("[%s] FAIL — warehouse=%d < silver=%d", s2w_name, warehouse_count, silver_count)
        else:
            results.append(CheckResult(s2w_name, True,
                f"Silver={silver_count}, {warehouse_table}={warehouse_count} (cumulative)"))
            logger.info("[%s] OK", s2w_name)

    # Companies: movie_companies is a link table (one row per movie/company
    # pair), not one row per company, so the plain row-count comparison above
    # doesn't apply — a single studio backing 128 films is 128 Silver rows and
    # 1 warehouse row. Compare against the *distinct* company_id count instead.
    try:
        companies_df = _read_silver_parquet(
            bucket, "movie_companies", ingestion_date, "movie_companies.parquet"
        )
        distinct_companies = companies_df["company_id"].nunique()
    except Exception as exc:
        results.append(CheckResult("rowcount:companies:bronze_to_silver", False,
            f"Could not read Silver movie_companies: {exc}"))
        logger.error("[rowcount:companies] could not read Silver: %s", exc)
        return results

    b2s_name = "rowcount:companies:bronze_to_silver"
    if bronze_credits_files == 0 and len(companies_df) > 0:
        results.append(CheckResult(b2s_name, False,
            f"Silver has {len(companies_df)} movie_companies row(s) but no Bronze files were found"))
        logger.error("[%s] FAIL — silver=%d but bronze files=0", b2s_name, len(companies_df))
    else:
        results.append(CheckResult(b2s_name, True,
            f"Bronze files present, Silver movie_companies={len(companies_df)} row(s), "
            f"{distinct_companies} distinct company_id(s)"))
        logger.info("[%s] OK", b2s_name)

    s2w_name = "rowcount:companies:silver_to_warehouse"
    warehouse_company_count = _table_row_count(session, "dim_company")
    if warehouse_company_count < distinct_companies:
        results.append(CheckResult(s2w_name, False,
            f"dim_company has only {warehouse_company_count} row(s), fewer than the "
            f"{distinct_companies} distinct company_id(s) just loaded from Silver"))
        logger.error("[%s] FAIL — warehouse=%d < silver_distinct=%d",
                      s2w_name, warehouse_company_count, distinct_companies)
    else:
        results.append(CheckResult(s2w_name, True,
            f"Silver distinct={distinct_companies}, dim_company={warehouse_company_count} (cumulative)"))
        logger.info("[%s] OK", s2w_name)

    # Countries/languages: same link-table shape as companies above — compare
    # against the distinct natural-key count, not the raw link row count.
    for entity_label, silver_entity, filename, id_col, warehouse_table in [
        ("countries", "movie_countries", "movie_countries.parquet", "country_code", "dim_country"),
        ("languages", "movie_languages", "movie_languages.parquet", "language_code", "dim_language"),
    ]:
        try:
            link_df = _read_silver_parquet(bucket, silver_entity, ingestion_date, filename)
            distinct_count = link_df[id_col].nunique()
        except Exception as exc:
            results.append(CheckResult(f"rowcount:{entity_label}:bronze_to_silver", False,
                f"Could not read Silver {silver_entity}: {exc}"))
            logger.error("[rowcount:%s] could not read Silver: %s", entity_label, exc)
            continue

        b2s_name = f"rowcount:{entity_label}:bronze_to_silver"
        if bronze_credits_files == 0 and len(link_df) > 0:
            results.append(CheckResult(b2s_name, False,
                f"Silver has {len(link_df)} {silver_entity} row(s) but no Bronze files were found"))
            logger.error("[%s] FAIL — silver=%d but bronze files=0", b2s_name, len(link_df))
        else:
            results.append(CheckResult(b2s_name, True,
                f"Bronze files present, Silver {silver_entity}={len(link_df)} row(s), "
                f"{distinct_count} distinct {id_col}(s)"))
            logger.info("[%s] OK", b2s_name)

        s2w_name = f"rowcount:{entity_label}:silver_to_warehouse"
        warehouse_count = _table_row_count(session, warehouse_table)
        if warehouse_count < distinct_count:
            results.append(CheckResult(s2w_name, False,
                f"{warehouse_table} has only {warehouse_count} row(s), fewer than the "
                f"{distinct_count} distinct {id_col}(s) just loaded from Silver"))
            logger.error("[%s] FAIL — warehouse=%d < silver_distinct=%d",
                          s2w_name, warehouse_count, distinct_count)
        else:
            results.append(CheckResult(s2w_name, True,
                f"Silver distinct={distinct_count}, {warehouse_table}={warehouse_count} (cumulative)"))
            logger.info("[%s] OK", s2w_name)

    # fact_movie_rating (Phase 15): Bronze is a single bulk file rather than
    # one object per movie, so bronze_count comes from parsing that file
    # rather than counting S3 keys. warehouse_table's row count also includes
    # the source='tmdb' rows load_fact_movie_rating() writes alongside the
    # IMDb ones, so this is a looser (but never-wrong) "never shrinks below
    # what Silver just produced" check, same shape as every other entity here.
    results.extend(_check_entity_counts(
        session, bucket, ingestion_date,
        entity_label="imdb_ratings",
        bronze_count=_bronze_imdb_ratings_row_count(bucket, ingestion_date),
        silver_entity="imdb_ratings", silver_filename="imdb_ratings.parquet",
        warehouse_table="fact_movie_rating",
    ))

    # dim_movie_video (Task 74): loaded by load_dimensions, but checked here
    # beside the other dimension-backed link entities. silver/movie_videos is
    # one row per (movie_id, video_id) — a one-to-many table, so the
    # silver_to_warehouse comparison uses nunique(movie_id), not len(df)
    # (the Task 58 fix). A pre-Task-73 partition has an empty movie_videos
    # file: nunique is 0 and both checks pass quietly. The load check is the
    # "a loader that silently wrote zero rows from real input is a bug" guard —
    # dim_movie_video carries ingestion_date, so it works the same way as the
    # fact-load checks.
    try:
        videos_df = _read_silver_parquet(
            bucket, "movie_videos", ingestion_date, "movie_videos.parquet"
        )
    except Exception as exc:
        logger.info(
            "[rowcount:movie_videos] no Silver movie_videos for %s (%s) — skipped",
            ingestion_date, exc,
        )
    else:
        distinct_video_movies = (
            videos_df["movie_id"].nunique() if "movie_id" in videos_df.columns else 0
        )
        s2w_name = "rowcount:movie_videos:silver_to_warehouse"
        warehouse_count = _table_row_count(session, "dim_movie_video")
        if warehouse_count < distinct_video_movies:
            results.append(CheckResult(s2w_name, False,
                f"dim_movie_video has only {warehouse_count} row(s), fewer than the "
                f"{distinct_video_movies} distinct movie_id(s) just loaded from Silver"))
            logger.error("[%s] FAIL — warehouse=%d < silver_distinct=%d",
                          s2w_name, warehouse_count, distinct_video_movies)
        else:
            results.append(CheckResult(s2w_name, True,
                f"Silver distinct movie_id={distinct_video_movies}, "
                f"dim_movie_video={warehouse_count}"))
            logger.info("[%s] OK", s2w_name)

        load_name = "rowcount:movie_videos:load"
        loaded = _fact_loaded_count(session, "dim_movie_video")
        if len(videos_df) > 0 and loaded == 0:
            results.append(CheckResult(load_name, False,
                f"dim_movie_video has 0 row(s) despite "
                f"{len(videos_df)} Silver movie_videos row(s)"))
            logger.error("[%s] FAIL — 0 rows loaded", load_name)
        else:
            results.append(CheckResult(load_name, True,
                f"{loaded} row(s) present"))
            logger.info("[%s] OK (%d rows)", load_name, loaded)

    # dim_series_video (Task 90): the show counterpart of dim_movie_video above.
    # Uses the dim_series/series_credits defensive shape (an explicit "no
    # series_id column" skip) rather than movie_videos' — movie_videos can
    # lean on nunique(...) defaulting to 0 for an absent column because a
    # movie-only run still legitimately produces this check (it's never
    # optional the way every TV entity is); series_videos would otherwise
    # produce a spurious always-passing result on a warehouse with no TV
    # Silver at all, and its check name would collide with "rowcount:series"
    # for anything doing a startswith() scan the way check_row_count_sanity's
    # own tests do.
    try:
        sv_df = _read_silver_parquet(
            bucket, "series_videos", ingestion_date, "series_videos.parquet"
        )
    except Exception as exc:
        logger.info(
            "[rowcount:series_videos] no Silver series_videos for %s (%s) — skipped",
            ingestion_date, exc,
        )
        sv_df = None
    if sv_df is not None and "series_id" not in sv_df.columns:
        logger.info(
            "[rowcount:series_videos] Silver series_videos has no series_id column — skipped"
        )
        sv_df = None
    if sv_df is not None:
        distinct_video_series = sv_df["series_id"].nunique()
        s2w_name = "rowcount:series_videos:silver_to_warehouse"
        warehouse_count = _table_row_count(session, "dim_series_video")
        if warehouse_count < distinct_video_series:
            results.append(CheckResult(s2w_name, False,
                f"dim_series_video has only {warehouse_count} row(s), fewer than the "
                f"{distinct_video_series} distinct series_id(s) just loaded from Silver"))
            logger.error("[%s] FAIL — warehouse=%d < silver_distinct=%d",
                          s2w_name, warehouse_count, distinct_video_series)
        else:
            results.append(CheckResult(s2w_name, True,
                f"Silver distinct series_id={distinct_video_series}, "
                f"dim_series_video={warehouse_count}"))
            logger.info("[%s] OK", s2w_name)

        load_name = "rowcount:series_videos:load"
        loaded = _fact_loaded_count(session, "dim_series_video")
        if len(sv_df) > 0 and loaded == 0:
            results.append(CheckResult(load_name, False,
                f"dim_series_video has 0 row(s) despite "
                f"{len(sv_df)} Silver series_videos row(s)"))
            logger.error("[%s] FAIL — 0 rows loaded", load_name)
        else:
            results.append(CheckResult(load_name, True,
                f"{loaded} row(s) present"))
            logger.info("[%s] OK (%d rows)", load_name, loaded)

    # dim_series / dim_network (Task 79): checked here beside the other
    # dimension-backed entities, same defensive shape as movie_videos above —
    # a movie-only run has no Silver series file and the whole block is
    # skipped. Both Silver Parquets are already one row per entity, so the
    # silver_to_warehouse comparison is a plain len(df), no nunique().
    for silver_entity, warehouse_table, id_col in [
        ("series", "dim_series", "series_id"),
        ("networks", "dim_network", "network_id"),
    ]:
        try:
            sdf = _read_silver_parquet(
                bucket, silver_entity, ingestion_date, f"{silver_entity}.parquet"
            )
        except Exception as exc:
            logger.info(
                "[rowcount:%s] no Silver %s for %s (%s) — skipped",
                silver_entity, silver_entity, ingestion_date, exc,
            )
            continue
        if id_col not in sdf.columns:
            logger.info(
                "[rowcount:%s] Silver %s has no %s column — skipped",
                silver_entity, silver_entity, id_col,
            )
            continue

        silver_count = len(sdf)
        s2w_name = f"rowcount:{silver_entity}:silver_to_warehouse"
        warehouse_count = _table_row_count(session, warehouse_table)
        if warehouse_count < silver_count:
            results.append(CheckResult(s2w_name, False,
                f"{warehouse_table} has only {warehouse_count} row(s), fewer than the "
                f"{silver_count} just loaded from Silver"))
            logger.error("[%s] FAIL — warehouse=%d < silver=%d",
                          s2w_name, warehouse_count, silver_count)
        else:
            results.append(CheckResult(s2w_name, True,
                f"Silver={silver_count}, {warehouse_table}={warehouse_count} (cumulative)"))
            logger.info("[%s] OK (silver=%d, warehouse=%d)",
                         s2w_name, silver_count, warehouse_count)

    # fact_series_credit (Task 80): same defensive shape as dim_series above —
    # a movie-only run has no Silver series_credits file and this is skipped.
    # Silver is already deduped on the 5-column grain the fact PK holds, and
    # dim_person now carries TV-only people (transform_people --with-tv), so
    # FK rejects are ~0 and the cumulative table can only be >= this
    # partition's Silver row count. The load check is the "a loader that
    # silently wrote zero rows from real input is a bug" guard.
    try:
        sc_df = _read_silver_parquet(
            bucket, "series_credits", ingestion_date, "series_credits.parquet"
        )
    except Exception as exc:
        logger.info(
            "[rowcount:series_credits] no Silver series_credits for %s (%s) — skipped",
            ingestion_date, exc,
        )
        sc_df = None
    if sc_df is not None and "series_id" not in sc_df.columns:
        logger.info(
            "[rowcount:series_credits] Silver series_credits has no series_id column — skipped"
        )
        sc_df = None
    if sc_df is not None:
        s2w_name = "rowcount:series_credits:silver_to_warehouse"
        warehouse_count = _table_row_count(session, "fact_series_credit")
        if warehouse_count < len(sc_df):
            results.append(CheckResult(s2w_name, False,
                f"fact_series_credit has only {warehouse_count} row(s), fewer than the "
                f"{len(sc_df)} just loaded from Silver"))
            logger.error("[%s] FAIL — warehouse=%d < silver=%d",
                          s2w_name, warehouse_count, len(sc_df))
        else:
            results.append(CheckResult(s2w_name, True,
                f"Silver={len(sc_df)}, fact_series_credit={warehouse_count} (cumulative)"))
            logger.info("[%s] OK", s2w_name)

        load_name = "rowcount:series_credits:load"
        loaded = _fact_loaded_count(session, "fact_series_credit")
        if len(sc_df) > 0 and loaded == 0:
            results.append(CheckResult(load_name, False,
                f"fact_series_credit has 0 row(s) despite "
                f"{len(sc_df)} Silver series_credits row(s)"))
            logger.error("[%s] FAIL — 0 rows loaded", load_name)
        else:
            results.append(CheckResult(load_name, True,
                f"{loaded} row(s) present"))
            logger.info("[%s] OK (%d rows)", load_name, loaded)

    # fact_series_rating (Task 81): same defensive shape. The Silver anchor is
    # silver/series (it guarantees the source='tmdb' rows); silver/series_ratings
    # is the source='imdb' half and may be absent even on a --with-tv run. The
    # warehouse table also holds the source='tmdb' rows, so like fact_movie_rating
    # this is a "never shrinks below the Silver series count" check, not equality.
    try:
        srs_df = _read_silver_parquet(
            bucket, "series", ingestion_date, "series.parquet"
        )
    except Exception as exc:
        logger.info(
            "[rowcount:series_ratings] no Silver series for %s (%s) — skipped",
            ingestion_date, exc,
        )
        srs_df = None
    if srs_df is not None and "series_id" not in srs_df.columns:
        logger.info(
            "[rowcount:series_ratings] Silver series has no series_id column — skipped"
        )
        srs_df = None
    if srs_df is not None:
        s2w_name = "rowcount:series_ratings:silver_to_warehouse"
        warehouse_count = _table_row_count(session, "fact_series_rating")
        if warehouse_count < len(srs_df):
            results.append(CheckResult(s2w_name, False,
                f"fact_series_rating has only {warehouse_count} row(s), fewer than the "
                f"{len(srs_df)} Silver series row(s) that seed its source='tmdb' rows"))
            logger.error("[%s] FAIL — warehouse=%d < silver=%d",
                          s2w_name, warehouse_count, len(srs_df))
        else:
            results.append(CheckResult(s2w_name, True,
                f"Silver series={len(srs_df)}, fact_series_rating={warehouse_count} (cumulative)"))
            logger.info("[%s] OK", s2w_name)

        load_name = "rowcount:series_ratings:load"
        loaded = _fact_loaded_count(session, "fact_series_rating")
        if len(srs_df) > 0 and loaded == 0:
            results.append(CheckResult(load_name, False,
                f"fact_series_rating has 0 row(s) despite "
                f"{len(srs_df)} Silver series row(s)"))
            logger.error("[%s] FAIL — 0 rows loaded", load_name)
        else:
            results.append(CheckResult(load_name, True,
                f"{loaded} row(s) present"))
            logger.info("[%s] OK (%d rows)", load_name, loaded)

    # dim_episode (Task 84): same defensive shape as dim_series / series_credits
    # above — a movie-only run has no Silver episodes file and the whole block
    # (row-count sanity *and* the natural-grain guard) is skipped. Silver
    # episodes.parquet is one row per episode_id, so silver_to_warehouse is a
    # plain len(df). dim_episode is loaded by _replace_by_parent, so the
    # cumulative table can shrink for a given series between runs — but never
    # below this partition's Silver row count on the load-date check.
    try:
        ep_df = _read_silver_parquet(
            bucket, "episodes", ingestion_date, "episodes.parquet"
        )
    except Exception as exc:
        logger.info(
            "[rowcount:episodes] no Silver episodes for %s (%s) — skipped",
            ingestion_date, exc,
        )
        ep_df = None
    if ep_df is not None and "episode_id" not in ep_df.columns:
        logger.info(
            "[rowcount:episodes] Silver episodes has no episode_id column — skipped"
        )
        ep_df = None
    if ep_df is not None:
        s2w_name = "rowcount:episodes:silver_to_warehouse"
        warehouse_count = _table_row_count(session, "dim_episode")
        if warehouse_count < len(ep_df):
            results.append(CheckResult(s2w_name, False,
                f"dim_episode has only {warehouse_count} row(s), fewer than the "
                f"{len(ep_df)} just loaded from Silver"))
            logger.error("[%s] FAIL — warehouse=%d < silver=%d",
                          s2w_name, warehouse_count, len(ep_df))
        else:
            results.append(CheckResult(s2w_name, True,
                f"Silver={len(ep_df)}, dim_episode={warehouse_count} (cumulative)"))
            logger.info("[%s] OK", s2w_name)

        load_name = "rowcount:episodes:load"
        loaded = _fact_loaded_count(session, "dim_episode")
        if len(ep_df) > 0 and loaded == 0:
            results.append(CheckResult(load_name, False,
                f"dim_episode has 0 row(s) despite "
                f"{len(ep_df)} Silver episodes row(s)"))
            logger.error("[%s] FAIL — 0 rows loaded", load_name)
        else:
            results.append(CheckResult(load_name, True,
                f"{loaded} row(s) present"))
            logger.info("[%s] OK (%d rows)", load_name, loaded)

        # Natural-grain guard: dim_episode's PK is TMDB's surrogate episode_id,
        # so a (series_id, season_number, episode_number) collision that slipped
        # the UNIQUE index (or a bug in _replace_by_parent) would not fail the
        # PK. This is the warehouse mirror of the Silver `grain` check on
        # silver/episodes (Task 82). NULLs in the natural key (an unaired,
        # unnumbered episode) are excluded — Postgres already permits them.
        grain_name = "grain:dim_episode"
        dupes = session.execute(text(
            "SELECT COUNT(*) FROM ("
            "  SELECT series_id, season_number, episode_number "
            "  FROM dim_episode "
            "  WHERE season_number IS NOT NULL AND episode_number IS NOT NULL "
            "  GROUP BY series_id, season_number, episode_number "
            "  HAVING COUNT(*) > 1"
            ") d"
        )).scalar()
        if dupes:
            results.append(CheckResult(grain_name, False,
                f"{dupes} (series_id, season_number, episode_number) pair(s) repeat "
                f"in dim_episode"))
            logger.error("[%s] FAIL — %d repeated natural key(s)", grain_name, dupes)
        else:
            results.append(CheckResult(grain_name, True,
                "No (series_id, season_number, episode_number) repeats in dim_episode"))
            logger.info("[%s] OK", grain_name)

    return results


# ---------------------------------------------------------------------------
# 2. Row-count sanity — Gold
# ---------------------------------------------------------------------------

_GOLD_DATASETS = ["genre_metrics", "decade_stats", "actor_filmography", "director_ratings"]


def check_gold_sanity(bucket: str, ingestion_date: dt.date, silver_movies_count: int) -> list[CheckResult]:
    """Verify each Gold dataset exists and is non-empty whenever Silver movies had data."""
    results: list[CheckResult] = []
    client = s3_utils.get_s3_client()

    for name in _GOLD_DATASETS:
        check_name = f"gold:{name}"
        key = s3_utils.build_path("gold", name, ingestion_date, f"{name}.parquet")
        try:
            response = client.get_object(Bucket=bucket, Key=key)
            row_count = len(pd.read_parquet(io.BytesIO(response["Body"].read())))
        except Exception as exc:
            if silver_movies_count > 0:
                results.append(CheckResult(check_name, False,
                    f"Could not read Gold {name} despite {silver_movies_count} Silver movie row(s): {exc}"))
                logger.error("[%s] FAIL — %s", check_name, exc)
            else:
                results.append(CheckResult(check_name, True,
                    f"No Gold {name} and no Silver movies for this date — nothing to build"))
                logger.info("[%s] OK (no data expected)", check_name)
            continue

        if silver_movies_count > 0 and row_count == 0:
            results.append(CheckResult(check_name, False,
                f"Gold {name} is empty despite {silver_movies_count} Silver movie row(s)"))
            logger.error("[%s] FAIL — empty dataset", check_name)
        else:
            results.append(CheckResult(check_name, True, f"{row_count} row(s)"))
            logger.info("[%s] OK (%d rows)", check_name, row_count)

    return results


# ---------------------------------------------------------------------------
# 2. Row-count sanity — Fact load
# ---------------------------------------------------------------------------

def check_fact_load_sanity(
    session: Session, ingestion_date: dt.date,
    silver_movies_count: int, silver_credit_count: int = 0,
    silver_company_count: int = 0, silver_country_count: int = 0,
    silver_language_count: int = 0, silver_rating_count: int = 0,
) -> list[CheckResult]:
    """A loader that silently wrote zero rows from non-empty Silver input is a bug."""
    results: list[CheckResult] = []

    fmm_count = _fact_loaded_count(session, "fact_movie_metrics")
    if silver_movies_count > 0 and fmm_count == 0:
        results.append(CheckResult("facts:fact_movie_metrics", False,
            f"fact_movie_metrics has 0 row(s) despite "
            f"{silver_movies_count} Silver movie row(s)"))
        logger.error("[facts:fact_movie_metrics] FAIL — 0 rows loaded")
    else:
        results.append(CheckResult("facts:fact_movie_metrics", True,
            f"{fmm_count} row(s) present"))
        logger.info("[facts:fact_movie_metrics] OK (%d rows)", fmm_count)

    fcredit_count = _fact_loaded_count(session, "fact_credit")
    if silver_credit_count > 0 and fcredit_count == 0:
        results.append(CheckResult("facts:fact_credit", False,
            f"fact_credit has 0 row(s) despite "
            f"{silver_credit_count} Silver credits_bridge row(s)"))
        logger.error("[facts:fact_credit] FAIL — 0 rows loaded")
    else:
        results.append(CheckResult("facts:fact_credit", True,
            f"{fcredit_count} row(s) present"))
        logger.info("[facts:fact_credit] OK (%d rows)", fcredit_count)

    bmc_count = _fact_loaded_count(session, "bridge_movie_company")
    if silver_company_count > 0 and bmc_count == 0:
        results.append(CheckResult("facts:bridge_movie_company", False,
            f"bridge_movie_company has 0 row(s) despite "
            f"{silver_company_count} Silver movie_companies row(s)"))
        logger.error("[facts:bridge_movie_company] FAIL — 0 rows loaded")
    else:
        results.append(CheckResult("facts:bridge_movie_company", True,
            f"{bmc_count} row(s) present"))
        logger.info("[facts:bridge_movie_company] OK (%d rows)", bmc_count)

    bmco_count = _fact_loaded_count(session, "bridge_movie_country")
    if silver_country_count > 0 and bmco_count == 0:
        results.append(CheckResult("facts:bridge_movie_country", False,
            f"bridge_movie_country has 0 row(s) despite "
            f"{silver_country_count} Silver movie_countries row(s)"))
        logger.error("[facts:bridge_movie_country] FAIL — 0 rows loaded")
    else:
        results.append(CheckResult("facts:bridge_movie_country", True,
            f"{bmco_count} row(s) present"))
        logger.info("[facts:bridge_movie_country] OK (%d rows)", bmco_count)

    bmla_count = _fact_loaded_count(session, "bridge_movie_language")
    if silver_language_count > 0 and bmla_count == 0:
        results.append(CheckResult("facts:bridge_movie_language", False,
            f"bridge_movie_language has 0 row(s) despite "
            f"{silver_language_count} Silver movie_languages row(s)"))
        logger.error("[facts:bridge_movie_language] FAIL — 0 rows loaded")
    else:
        results.append(CheckResult("facts:bridge_movie_language", True,
            f"{bmla_count} row(s) present"))
        logger.info("[facts:bridge_movie_language] OK (%d rows)", bmla_count)

    # fact_movie_rating gets rows from two Silver sources: a non-empty
    # movies partition alone guarantees source='tmdb' rows, so either input
    # being non-empty should produce at least one row for this date.
    fmr_count = _fact_loaded_count(session, "fact_movie_rating")
    if (silver_movies_count > 0 or silver_rating_count > 0) and fmr_count == 0:
        results.append(CheckResult("facts:fact_movie_rating", False,
            f"fact_movie_rating has 0 row(s) despite "
            f"{silver_movies_count} Silver movie row(s) / {silver_rating_count} Silver "
            f"imdb_ratings row(s)"))
        logger.error("[facts:fact_movie_rating] FAIL — 0 rows loaded")
    else:
        results.append(CheckResult("facts:fact_movie_rating", True,
            f"{fmr_count} row(s) present"))
        logger.info("[facts:fact_movie_rating] OK (%d rows)", fmr_count)

    return results


# ---------------------------------------------------------------------------
# 4. Translations (Task 93)
# ---------------------------------------------------------------------------

# (silver entity, warehouse table, parent id column)
_TRANSLATION_ENTITIES = [
    ("movie_translations", "movie_translation", "movie_id"),
    ("person_translations", "person_translation", "person_id"),
    ("genre_translations", "genre_translation", "genre_id"),
    ("country_translations", "country_translation", "country_code"),
]


def _rows_per_lang(session: Session, table: str) -> dict[str, int]:
    """Row count per `lang` value in a translation table."""
    rows = session.execute(
        text(f"SELECT lang, COUNT(*) FROM {table} GROUP BY lang")
    ).all()
    return {lang: int(n) for lang, n in rows}


def check_translation_sanity(
    session: Session, bucket: str, ingestion_date: dt.date,
) -> list[CheckResult]:
    """Row counts and the `lang` domain for the four *_translation tables.

    Two checks per table whose Silver file exists for the date:

      * ``translations:<table>:lang_domain`` — every `lang` in the warehouse is
        one the site ships (etl.translations.TRANSLATION_LANGUAGES). The DDL has
        no CHECK on `lang` on purpose (a fourth language must not need a schema
        change), so this is where a stray ``ru-RU`` or ``en`` row is caught.
      * ``translations:<table>:load`` — the table is non-empty whenever the Silver
        file had rows: a loader that wrote nothing from real input is a bug. Per-
        language counts are put in the detail so a language quietly going to zero
        is visible in the log. Whole-table, not this date's rows, for the reason
        given on _fact_loaded_count().

    A Silver file that is absent or empty (a partition written before Task 93,
    where Bronze could not be backfilled) is skipped for the load check — the
    domain check still runs, since it reads only the warehouse. A missing table
    (DDL not applied yet) makes the whole check skip with a log line rather than
    failing the run, matching how every other optional entity behaves.
    """
    results: list[CheckResult] = []
    for silver_entity, table, _parent_col in _TRANSLATION_ENTITIES:
        try:
            per_lang = _rows_per_lang(session, table)
        except Exception as exc:
            session.rollback()
            logger.info("[translations:%s] table not readable (%s) — skipped", table, exc)
            continue

        stray = sorted(set(per_lang) - set(TRANSLATION_LANGUAGES))
        domain_name = f"translations:{table}:lang_domain"
        if stray:
            results.append(CheckResult(domain_name, False,
                f"{table} has unsupported lang value(s): {', '.join(stray)}"))
            logger.error("[%s] FAIL — stray lang(s): %s", domain_name, stray)
        else:
            results.append(CheckResult(domain_name, True,
                f"all lang values within {list(TRANSLATION_LANGUAGES)}"))
            logger.info("[%s] OK", domain_name)

        try:
            sdf = _read_silver_parquet(
                bucket, silver_entity, ingestion_date, f"{silver_entity}.parquet"
            )
        except Exception as exc:
            logger.info(
                "[translations:%s] no Silver %s for %s (%s) — load check skipped",
                table, silver_entity, ingestion_date, exc,
            )
            continue

        load_name = f"translations:{table}:load"
        total = sum(per_lang.values())
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(per_lang.items())) or "none"
        if len(sdf) > 0 and total == 0:
            results.append(CheckResult(load_name, False,
                f"{table} has 0 row(s) despite {len(sdf)} Silver {silver_entity} row(s)"))
            logger.error("[%s] FAIL — 0 rows loaded", load_name)
        else:
            results.append(CheckResult(load_name, True,
                f"Silver={len(sdf)}, {table}={total} ({breakdown})"))
            logger.info("[%s] OK (%s)", load_name, breakdown)

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_warehouse_checks(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> list[CheckResult]:
    """Run FK integrity and Bronze->Silver->Gold->Warehouse row-count sanity checks.

    Overall pass: all(r.passed for r in results).
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting end-to-end warehouse checks for date=%s", ingestion_date)

    all_results: list[CheckResult] = []

    with get_session() as session:
        all_results.extend(check_fk_integrity(session))
        all_results.extend(check_row_count_sanity(session, bucket, ingestion_date))
        all_results.extend(check_translation_sanity(session, bucket, ingestion_date))

        try:
            silver_movies_count = len(_read_silver_parquet(bucket, "movies", ingestion_date, "movies.parquet"))
        except Exception:
            silver_movies_count = 0
        try:
            silver_bridge_df = _read_silver_parquet(
                bucket, "credits_bridge", ingestion_date, "credits_bridge.parquet"
            )
            silver_credit_count = len(silver_bridge_df)
        except Exception:
            silver_credit_count = 0
        try:
            silver_companies_df = _read_silver_parquet(
                bucket, "movie_companies", ingestion_date, "movie_companies.parquet"
            )
            silver_company_count = len(silver_companies_df)
        except Exception:
            silver_company_count = 0
        try:
            silver_countries_df = _read_silver_parquet(
                bucket, "movie_countries", ingestion_date, "movie_countries.parquet"
            )
            silver_country_count = len(silver_countries_df)
        except Exception:
            silver_country_count = 0
        try:
            silver_languages_df = _read_silver_parquet(
                bucket, "movie_languages", ingestion_date, "movie_languages.parquet"
            )
            silver_language_count = len(silver_languages_df)
        except Exception:
            silver_language_count = 0
        try:
            silver_ratings_df = _read_silver_parquet(
                bucket, "imdb_ratings", ingestion_date, "imdb_ratings.parquet"
            )
            silver_rating_count = len(silver_ratings_df)
        except Exception:
            silver_rating_count = 0

        all_results.extend(check_gold_sanity(bucket, ingestion_date, silver_movies_count))
        all_results.extend(
            check_fact_load_sanity(
                session, ingestion_date, silver_movies_count, silver_credit_count,
                silver_company_count, silver_country_count, silver_language_count,
                silver_rating_count,
            )
        )

    passed = sum(1 for r in all_results if r.passed)
    failed = sum(1 for r in all_results if not r.passed)
    elapsed = time.monotonic() - t0
    logger.info(
        "Warehouse checks complete: %d passed, %d failed in %.2fs", passed, failed, elapsed,
    )
    return all_results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run end-to-end data quality validation on the warehouse."
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
    setup_logging("warehouse_checks")
    args = _parse_args()
    results = run_warehouse_checks(ingestion_date=args.date)
    overall = all(r.passed for r in results)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.check:45s} {r.detail}")
    n_passed = sum(1 for r in results if r.passed)
    print(f"\nOVERALL: {'PASS' if overall else 'FAIL'} ({n_passed}/{len(results)} checks passed)")
    raise SystemExit(0 if overall else 1)
