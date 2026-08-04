"""Warehouse loader: Gold.

Loads pre-aggregated Gold datasets into the warehouse. Deliberately a separate
module from load_facts.py, whose source is Silver — the layer a table is built
from is a real distinction, not a filing detail:

    Silver -> warehouse   raw grain, one row per real-world event
    Gold   -> warehouse   derived grain, one row per computed relationship

Until this module existed, Gold was written on every pipeline run and read by
nothing. `fact_collaboration` is the dataset that justifies the layer: a
quadratic expansion over every film's key credits, far too expensive to compute
per web request, and shaped for a read the star schema can't serve without a
self-join every time.

Unlike the fact loaders, this one does *not* quarantine unresolvable rows. A
Gold edge references two people the pipeline itself just derived from the same
Silver partition, so an FK miss here would mean the Gold build and the dimension
load disagree — a bug to surface loudly, not a bad record to set aside. Rows are
filtered against dim_person and the count is logged; a non-zero count means
something upstream is wrong.

S3 source:
    gold/collaboration_edges/ingestion_date=YYYY-MM-DD/collaboration_edges.parquet

Usage:
    python -m etl.warehouse_loader.load_gold
    python -m etl.warehouse_loader.load_gold --date 2026-06-22
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import logging
import time
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

import config
from etl import s3_utils
from etl.warehouse_loader.common import _existing_ids, _upsert
from warehouse.db import get_session

logger = logging.getLogger(__name__)


def _read_gold_parquet(bucket: str, dataset: str, ingestion_date: dt.date) -> pd.DataFrame:
    """Download and parse one Gold Parquet file from S3."""
    key = s3_utils.build_path("gold", dataset, ingestion_date, f"{dataset}.parquet")
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    df = pd.read_parquet(io.BytesIO(response["Body"].read()), engine="pyarrow")
    logger.info("Read Gold %s: %d row(s) from s3://%s/%s", dataset, len(df), bucket, key)
    return df


def _records(df: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    """Convert selected columns of a DataFrame to a list of dicts, with NA -> None."""
    subset = df[columns].astype(object).where(pd.notnull(df[columns]), None)
    return subset.to_dict("records")


def load_fact_collaboration(session: Session, edges_df: pd.DataFrame) -> int:
    """Upsert Gold collaboration edges into fact_collaboration."""
    valid_person_ids = _existing_ids(session, "dim_person", "person_id")

    resolved = edges_df[
        edges_df["person_a_id"].isin(valid_person_ids)
        & edges_df["person_b_id"].isin(valid_person_ids)
    ]
    dropped = len(edges_df) - len(resolved)
    if dropped:
        logger.error(
            "fact_collaboration: %d edge(s) reference a person absent from dim_person — "
            "the Gold build and the dimension load disagree about this partition",
            dropped,
        )

    columns = ["person_a_id", "person_b_id", "films_together", "first_year", "last_year"]
    count = _upsert(
        session, "fact_collaboration", ["person_a_id", "person_b_id"],
        columns, _records(resolved, columns),
    )
    logger.info("fact_collaboration: upserted %d row(s)", count)
    return count


def load_gold(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> dict[str, int]:
    """Read the Gold datasets for `ingestion_date` and upsert them into the warehouse.

    Returns a dict of table name -> row count upserted.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Gold load for ingestion_date=%s", ingestion_date)

    edges_df = _read_gold_parquet(bucket, "collaboration_edges", ingestion_date)

    counts: dict[str, int] = {}
    with get_session() as session:
        counts["fact_collaboration"] = load_fact_collaboration(session, edges_df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Gold load complete: %s in %.2fs",
        ", ".join(f"{k}={v}" for k, v in counts.items()), elapsed,
    )
    return counts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load Gold aggregate datasets into the PostgreSQL warehouse."
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
    setup_logging("load_gold")
    args = _parse_args()
    load_gold(ingestion_date=args.date)
