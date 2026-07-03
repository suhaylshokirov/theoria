"""End-to-end data quality validation for the warehouse layer.

Runs two families of checks after the dimension and fact loaders have run
for a given ingestion_date:

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
         - Both fact tables must have at least one row tagged with this
           ingestion_date whenever the Silver data that feeds them was
           non-empty — a loader that silently produced zero rows from real
           input is a bug, not "clean data" (genuine zero-row loads only
           happen when Silver itself is empty).

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
    ("fact_casting", "movie_id", "dim_movie", "movie_id"),
    ("fact_casting", "actor_id", "dim_actor", "actor_id"),
    ("fact_casting", "director_id", "dim_director", "director_id"),
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


def _bronze_genre_count(bucket: str, ingestion_date: dt.date) -> int:
    """Count genres in the single Bronze genres.json payload for a date. 0 if missing."""
    key = s3_utils.build_path("bronze", "genres", ingestion_date, "genres.json")
    client = s3_utils.get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.NoSuchKey:
        return 0
    except Exception:
        return 0
    payload = json.loads(response["Body"].read())
    return len(payload.get("genres", []))


# ---------------------------------------------------------------------------
# 2. Row-count sanity — Warehouse
# ---------------------------------------------------------------------------

def _table_row_count(session: Session, table: str) -> int:
    return session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def _fact_ingestion_date_count(session: Session, table: str, ingestion_date: dt.date) -> int:
    return session.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE ingestion_date = :date"),
        {"date": ingestion_date},
    ).scalar()


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
        ("actors", "actors", "actors.parquet", "dim_actor"),
        ("directors", "directors", "directors.parquet", "dim_director"),
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
    silver_movies_count: int, silver_bridge_count: int,
) -> list[CheckResult]:
    """A loader that silently wrote zero rows from non-empty Silver input is a bug."""
    results: list[CheckResult] = []

    fmm_count = _fact_ingestion_date_count(session, "fact_movie_metrics", ingestion_date)
    if silver_movies_count > 0 and fmm_count == 0:
        results.append(CheckResult("facts:fact_movie_metrics", False,
            f"fact_movie_metrics has 0 row(s) for ingestion_date={ingestion_date} despite "
            f"{silver_movies_count} Silver movie row(s)"))
        logger.error("[facts:fact_movie_metrics] FAIL — 0 rows loaded")
    else:
        results.append(CheckResult("facts:fact_movie_metrics", True,
            f"{fmm_count} row(s) loaded for ingestion_date={ingestion_date}"))
        logger.info("[facts:fact_movie_metrics] OK (%d rows)", fmm_count)

    fc_count = _fact_ingestion_date_count(session, "fact_casting", ingestion_date)
    if silver_bridge_count > 0 and fc_count == 0:
        results.append(CheckResult("facts:fact_casting", False,
            f"fact_casting has 0 row(s) for ingestion_date={ingestion_date} despite "
            f"{silver_bridge_count} Silver credits_bridge row(s)"))
        logger.error("[facts:fact_casting] FAIL — 0 rows loaded")
    else:
        results.append(CheckResult("facts:fact_casting", True,
            f"{fc_count} row(s) loaded for ingestion_date={ingestion_date}"))
        logger.info("[facts:fact_casting] OK (%d rows)", fc_count)

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

        try:
            silver_movies_count = len(_read_silver_parquet(bucket, "movies", ingestion_date, "movies.parquet"))
        except Exception:
            silver_movies_count = 0
        try:
            silver_bridge_count = len(
                _read_silver_parquet(bucket, "credits_bridge", ingestion_date, "credits_bridge.parquet")
            )
        except Exception:
            silver_bridge_count = 0

        all_results.extend(check_gold_sanity(bucket, ingestion_date, silver_movies_count))
        all_results.extend(
            check_fact_load_sanity(session, ingestion_date, silver_movies_count, silver_bridge_count)
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
