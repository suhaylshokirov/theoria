"""Silver-layer data quality checks.

Reads each Silver Parquet file for a given ingestion_date and runs four check
types against it:

    1. schema     — all expected columns are present
    2. nulls      — required columns contain no null values
    3. duplicates — primary-key columns are unique
    4. ranges     — numeric columns fall within expected bounds

Rows that fail the null, duplicate, or range checks are written to local
Parquet files under `rejected_dir` (default: data_quality/rejected/) so they
can be investigated later. They are quarantined, never deleted.

A CheckResult is produced for every (entity, check) pair. The overall run
passes only if every CheckResult has passed=True.

Usage:
    python -m data_quality.silver_checks
    python -m data_quality.silver_checks --date 2026-06-22

Auto-run after Tasks 9–12 (Silver transforms for movies, actors, directors,
genres, and credits bridge).
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

import config
from etl import s3_utils

logger = logging.getLogger(__name__)

_DEFAULT_REJECTED_DIR = Path(__file__).parent / "rejected"

# ---------------------------------------------------------------------------
# Per-entity check configuration
# ---------------------------------------------------------------------------

ENTITY_CONFIGS: dict[str, dict[str, Any]] = {
    "movies": {
        "parquet": "movies.parquet",
        "pk_cols": ["movie_id"],
        "required_cols": ["movie_id", "title"],
        "expected_cols": [
            "movie_id", "title", "release_date", "runtime", "budget", "revenue",
            "original_language", "status", "vote_average", "vote_count",
            "popularity", "overview", "tagline", "poster_path", "backdrop_path",
            "genre_ids",
        ],
        "ranges": {
            "vote_average": (0.0, 10.0),
            "vote_count":   (0,   None),
            "popularity":   (0.0, None),
        },
    },
    "actors": {
        "parquet": "actors.parquet",
        "pk_cols": ["person_id"],
        "required_cols": ["person_id", "name"],
        "expected_cols": ["person_id", "name", "gender", "popularity", "profile_path"],
        "ranges": {
            "popularity": (0.0, None),
        },
    },
    "directors": {
        "parquet": "directors.parquet",
        "pk_cols": ["person_id"],
        "required_cols": ["person_id", "name"],
        "expected_cols": ["person_id", "name", "gender", "popularity", "profile_path"],
        "ranges": {
            "popularity": (0.0, None),
        },
    },
    "genres": {
        "parquet": "genres.parquet",
        "pk_cols": ["genre_id"],
        "required_cols": ["genre_id", "genre_name"],
        "expected_cols": ["genre_id", "genre_name"],
        "ranges": {},
    },
    "credits_bridge": {
        "parquet": "credits_bridge.parquet",
        "pk_cols": ["movie_id", "person_id", "credit_type"],
        "required_cols": ["movie_id", "person_id", "credit_type"],
        "expected_cols": ["movie_id", "person_id", "credit_type", "role", "ordering"],
        "ranges": {
            "ordering": (0, None),
        },
    },
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    entity: str
    check: str
    passed: bool
    bad_count: int
    message: str


# ---------------------------------------------------------------------------
# Individual check functions (all return a boolean mask of bad rows)
# ---------------------------------------------------------------------------

def _check_schema(df: pd.DataFrame, expected_cols: list[str]) -> list[str]:
    """Return a list of column names that are missing from `df`."""
    return [c for c in expected_cols if c not in df.columns]


def _null_mask(df: pd.DataFrame, required_cols: list[str]) -> pd.Series:
    """True for every row that has a null in at least one required column."""
    present = [c for c in required_cols if c in df.columns]
    if not present:
        return pd.Series(False, index=df.index)
    return df[present].isnull().any(axis=1)


def _duplicate_mask(df: pd.DataFrame, pk_cols: list[str]) -> pd.Series:
    """True for every row that is a duplicate on the primary-key columns."""
    present = [c for c in pk_cols if c in df.columns]
    if not present:
        return pd.Series(False, index=df.index)
    return df.duplicated(subset=present, keep="first")


def _range_mask(df: pd.DataFrame, ranges: dict[str, tuple]) -> pd.Series:
    """True for every row where a column falls outside its allowed range.

    `ranges` maps column_name → (min_value, max_value).
    Use None for an unbounded side: (0, None) means >= 0 with no upper limit.
    Only non-null values are checked; nulls are not treated as range failures.
    """
    mask = pd.Series(False, index=df.index)
    for col, (lo, hi) in ranges.items():
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        not_null = series.notna()
        if lo is not None:
            mask |= not_null & (series < lo)
        if hi is not None:
            mask |= not_null & (series > hi)
    return mask


# ---------------------------------------------------------------------------
# Reject writer
# ---------------------------------------------------------------------------

def _write_rejects(
    rejects: list[pd.DataFrame],
    entity: str,
    ingestion_date: dt.date,
    rejected_dir: Path,
) -> Path | None:
    """Combine reject DataFrames and write to a local Parquet file.

    Returns the file path written, or None if there were no rejects.
    """
    if not rejects:
        return None
    combined = pd.concat(rejects, ignore_index=True)
    if combined.empty:
        return None

    rejected_dir.mkdir(parents=True, exist_ok=True)
    path = rejected_dir / f"{entity}_rejected_{ingestion_date.isoformat()}.parquet"
    combined.to_parquet(path, engine="pyarrow", index=False)
    logger.warning(
        "Wrote %d rejected row(s) for entity=%s to %s", len(combined), entity, path
    )
    return path


# ---------------------------------------------------------------------------
# Per-entity runner
# ---------------------------------------------------------------------------

def _run_entity_checks(
    df: pd.DataFrame,
    entity: str,
    cfg: dict[str, Any],
    ingestion_date: dt.date,
    rejected_dir: Path,
) -> list[CheckResult]:
    """Run all four check types against one Silver DataFrame."""
    results: list[CheckResult] = []
    rejects: list[pd.DataFrame] = []

    # 1. Schema check
    missing_cols = _check_schema(df, cfg["expected_cols"])
    if missing_cols:
        results.append(CheckResult(
            entity=entity,
            check="schema",
            passed=False,
            bad_count=len(missing_cols),
            message=f"Missing columns: {missing_cols}",
        ))
        logger.error("[%s] schema FAIL — missing columns: %s", entity, missing_cols)
    else:
        results.append(CheckResult(entity=entity, check="schema", passed=True,
                                   bad_count=0, message="All expected columns present"))
        logger.info("[%s] schema OK", entity)

    # 2. Null check
    null_bad = _null_mask(df, cfg["required_cols"])
    n_null = int(null_bad.sum())
    if n_null:
        bad = df[null_bad].copy()
        bad["rejection_reason"] = "null_required_field"
        rejects.append(bad)
        results.append(CheckResult(
            entity=entity, check="nulls", passed=False, bad_count=n_null,
            message=f"{n_null} row(s) have null in required column(s): {cfg['required_cols']}",
        ))
        logger.error("[%s] nulls FAIL — %d row(s)", entity, n_null)
    else:
        results.append(CheckResult(entity=entity, check="nulls", passed=True,
                                   bad_count=0, message="No nulls in required columns"))
        logger.info("[%s] nulls OK", entity)

    # 3. Duplicate check
    dup_bad = _duplicate_mask(df, cfg["pk_cols"])
    n_dup = int(dup_bad.sum())
    if n_dup:
        bad = df[dup_bad].copy()
        bad["rejection_reason"] = "duplicate_primary_key"
        rejects.append(bad)
        results.append(CheckResult(
            entity=entity, check="duplicates", passed=False, bad_count=n_dup,
            message=f"{n_dup} duplicate row(s) on pk={cfg['pk_cols']}",
        ))
        logger.error("[%s] duplicates FAIL — %d row(s)", entity, n_dup)
    else:
        results.append(CheckResult(entity=entity, check="duplicates", passed=True,
                                   bad_count=0, message=f"No duplicates on pk={cfg['pk_cols']}"))
        logger.info("[%s] duplicates OK", entity)

    # 4. Range check
    range_bad = _range_mask(df, cfg.get("ranges", {}))
    n_range = int(range_bad.sum())
    if n_range:
        bad = df[range_bad].copy()
        bad["rejection_reason"] = "out_of_range"
        rejects.append(bad)
        results.append(CheckResult(
            entity=entity, check="ranges", passed=False, bad_count=n_range,
            message=f"{n_range} row(s) have out-of-range values in: {list(cfg['ranges'])}",
        ))
        logger.error("[%s] ranges FAIL — %d row(s)", entity, n_range)
    else:
        results.append(CheckResult(entity=entity, check="ranges", passed=True,
                                   bad_count=0, message="All values within expected ranges"))
        logger.info("[%s] ranges OK", entity)

    _write_rejects(rejects, entity, ingestion_date, rejected_dir)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _read_silver_parquet(bucket: str, entity: str, ingestion_date: dt.date,
                         parquet_name: str) -> pd.DataFrame:
    """Download and parse a Silver Parquet file from S3."""
    key = s3_utils.build_path("silver", entity, ingestion_date, parquet_name)
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return pd.read_parquet(io.BytesIO(response["Body"].read()))


def run_silver_checks(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    rejected_dir: Path | None = None,
) -> list[CheckResult]:
    """Run all Silver DQ checks for the given ingestion_date.

    For each entity (movies, actors, directors, genres, credits_bridge):
    - Reads the Silver Parquet from S3.
    - Runs schema, null, duplicate, and range checks.
    - Writes rejected rows to `rejected_dir` (local Parquet files).

    Returns a flat list of CheckResult objects, one per (entity, check) pair.
    Missing Silver files are recorded as failures but do not abort the run.

    Overall pass: all(r.passed for r in results).
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET
    if rejected_dir is None:
        rejected_dir = _DEFAULT_REJECTED_DIR

    t0 = time.monotonic()
    logger.info("Starting Silver DQ checks for date=%s", ingestion_date)

    all_results: list[CheckResult] = []

    for entity, cfg in ENTITY_CONFIGS.items():
        try:
            df = _read_silver_parquet(bucket, entity, ingestion_date, cfg["parquet"])
            logger.info("[%s] loaded %d row(s)", entity, len(df))
        except Exception as exc:
            logger.error("[%s] could not read Silver Parquet: %s", entity, exc)
            all_results.append(CheckResult(
                entity=entity, check="load", passed=False, bad_count=0,
                message=f"Could not read Silver Parquet: {exc}",
            ))
            continue

        all_results.extend(
            _run_entity_checks(df, entity, cfg, ingestion_date, rejected_dir)
        )

    passed = sum(1 for r in all_results if r.passed)
    failed = sum(1 for r in all_results if not r.passed)
    elapsed = time.monotonic() - t0
    logger.info(
        "Silver DQ checks complete: %d passed, %d failed in %.2fs",
        passed, failed, elapsed,
    )
    return all_results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run data quality checks on Silver Parquet files."
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
    setup_logging("silver_checks")
    args = _parse_args()
    results = run_silver_checks(ingestion_date=args.date)
    overall = all(r.passed for r in results)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.entity:20s} {r.check:12s} bad={r.bad_count:4d}  {r.message}")
    raise SystemExit(0 if overall else 1)
