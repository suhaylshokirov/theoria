"""Shared plumbing for the warehouse loaders (dimensions and facts).

Houses the S3 read helper and the generic upsert builder that both
load_dimensions.py and load_facts.py depend on, so the ON CONFLICT SQL and
the Silver Parquet read path only need to change in one place.
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from etl import s3_utils


def _read_silver_parquet(bucket: str, entity: str, ingestion_date: dt.date, filename: str) -> pd.DataFrame:
    """Download and parse a Silver Parquet file from S3."""
    key = s3_utils.build_path("silver", entity, ingestion_date, filename)
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return pd.read_parquet(io.BytesIO(response["Body"].read()))


def _upsert(session: Session, table: str, pk_cols: list[str], columns: list[str],
            records: list[dict[str, Any]]) -> int:
    """Bulk upsert records into `table`, updating non-PK columns on conflict."""
    if not records:
        return 0
    update_cols = [c for c in columns if c not in pk_cols]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(f':{c}' for c in columns)}) "
        f"ON CONFLICT ({', '.join(pk_cols)}) DO UPDATE SET {set_clause}"
    )
    session.execute(text(sql), records)
    return len(records)


def _existing_ids(session: Session, table: str, pk_col: str) -> set[int]:
    """Return the set of PK values currently present in a table."""
    rows = session.execute(text(f"SELECT {pk_col} FROM {table}")).scalars().all()
    return {int(v) for v in rows}
