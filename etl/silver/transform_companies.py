"""Silver transform: production-company detail fields.

Reads the Bronze company-detail JSON files and writes one denormalised long
table, `silver/company_details/company_details.parquet`, with one row per
company_id:

    company_id            Int64
    description           string  — nullable, TMDB "" normalised to None
    headquarters          string  — nullable, TMDB "" normalised to None
    homepage              string  — nullable, TMDB "" normalised to None
    parent_company_id     Int64   — nullable; null when TMDB parent_company is null
    parent_company_name   string  — nullable; ditto

**Reads every `bronze/company_details/` partition, not just one date.** Every
other Silver transform reads a single dated Bronze partition because its
Bronze source is re-fetched fresh every run. Company details are the
exception (see etl/bronze/ingest_companies.py): a studio's description /
headquarters / parent essentially never change, so `ingest_companies()` only
ever writes a *new* company's file, into whatever partition first discovered
it. The cumulative enriched set is therefore spread across every partition,
and this transform has to sweep all of them to produce the full table. The
result is written to the given date's Silver partition, so the warehouse
loader still reads one dated file like every other dimension source.

Rows with a null company_id are dropped with a warning, never silently.

Idempotent: running twice for the same date overwrites the same key.

Usage:
    python -m etl.silver.transform_companies
    python -m etl.silver.transform_companies --date 2026-06-22
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


def _list_all_bronze_keys(bucket: str) -> list[str]:
    """Every .json key under bronze/company_details/, across all ingestion_dates."""
    client = s3_utils.get_s3_client()
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="bronze/company_details/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def _extract_company_row(raw: dict[str, Any]) -> dict[str, Any]:
    """One row from a TMDB /company/{id} payload.

    Every one of description/headquarters/homepage/parent_company is always
    present in the payload (measured: never omitted) — "" for an empty string
    field, null for an absent parent. `or None` collapses "" to None, the
    same normalisation Tasks 36/55 use for TMDB's empty image/identifier
    strings.
    """
    parent = raw.get("parent_company") or None
    return {
        "company_id": raw.get("id"),
        "description": (raw.get("description") or "").strip() or None,
        "headquarters": (raw.get("headquarters") or "").strip() or None,
        "homepage": (raw.get("homepage") or "").strip() or None,
        "parent_company_id": parent.get("id") if parent else None,
        "parent_company_name": parent.get("name") if parent else None,
    }


def _cast_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["company_id"] = pd.to_numeric(df["company_id"], errors="coerce").astype("Int64")
    df["description"] = df["description"].astype("string")
    df["headquarters"] = df["headquarters"].astype("string")
    df["homepage"] = df["homepage"].astype("string")
    df["parent_company_id"] = pd.to_numeric(
        df["parent_company_id"], errors="coerce"
    ).astype("Int64")
    df["parent_company_name"] = df["parent_company_name"].astype("string")
    return df


_COLUMNS = [
    "company_id", "description", "headquarters", "homepage",
    "parent_company_id", "parent_company_name",
]


def transform_companies(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read every Bronze company-detail JSON -> write one Silver Parquet.

    Returns the s3:// URI written.

    Raises FileNotFoundError if no Bronze company-detail files exist at all.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver company-details transform for date=%s", ingestion_date)

    keys = _list_all_bronze_keys(bucket)
    if not keys:
        raise FileNotFoundError(
            "No Bronze company-detail files found under bronze/company_details/"
        )
    logger.info("Found %d Bronze company JSON file(s) across all partitions", len(keys))

    rows: list[dict[str, Any]] = []
    errors = 0
    for key, raw, read_err in s3_utils.read_json_objects(bucket, keys):
        try:
            if read_err is not None:
                raise read_err
            rows.append(_extract_company_row(raw))
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/extract %s: %s", key, exc)

    if not rows and errors == len(keys):
        raise RuntimeError(
            "Every Bronze company-detail file failed to parse — aborting."
        )

    df = pd.DataFrame(rows, columns=_COLUMNS)
    df = _cast_types(df)

    before = len(df)
    # A later partition wins if the same company was somehow enriched twice.
    df = df.drop_duplicates(subset=["company_id"], keep="last")
    dupes = before - len(df)
    if dupes:
        logger.info("Dropped %d duplicate company_id row(s)", dupes)

    n_null = int(df["company_id"].isna().sum())
    if n_null:
        logger.warning("Dropping %d row(s) with null company_id", n_null)
    df = df.dropna(subset=["company_id"])

    output_key = s3_utils.build_path(
        "silver", "company_details", ingestion_date, "company_details.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver company-details transform complete: %d company row(s), "
        "%d with a description, %d with a parent, %d parse error(s) in %.2fs",
        len(df),
        int(df["description"].notna().sum()),
        int(df["parent_company_id"].notna().sum()),
        errors, elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze company-detail JSON to Silver Parquet."
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
    setup_logging("transform_companies")
    args = _parse_args()
    transform_companies(ingestion_date=args.date)
