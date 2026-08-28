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
from sqlalchemy import Column, MetaData, Table, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    """Bulk upsert records into `table`, updating non-PK columns on conflict.

    Built as a Core INSERT construct rather than a textual statement on purpose:
    SQLAlchemy's insertmanyvalues only rewrites a multi-row executemany into
    batched ``VALUES (...), (...), ...`` statements when it compiled the INSERT
    itself. A ``text()`` executemany falls through to one round-trip per row —
    fine on a local socket, but ~2 minutes per 1,000 rows against a database in
    another region, and ``dim_person`` / ``fact_credit`` are 120k+ rows each.
    """
    if not records:
        return 0
    update_cols = [c for c in columns if c not in pk_cols]
    tbl = Table(table, MetaData(), *(Column(c) for c in columns))
    stmt = pg_insert(tbl)
    stmt = stmt.on_conflict_do_update(
        index_elements=pk_cols,
        set_={c: stmt.excluded[c] for c in update_cols},
    )
    session.execute(stmt, records)
    return len(records)


def _existing_ids(session: Session, table: str, pk_col: str) -> set[int]:
    """Return the set of PK values currently present in a table."""
    rows = session.execute(text(f"SELECT {pk_col} FROM {table}")).scalars().all()
    return {int(v) for v in rows}


def _existing_str_ids(session: Session, table: str, pk_col: str) -> set[str]:
    """Return the set of string PK values currently present in a table.

    For natural-key dimensions like dim_country/dim_language, whose PK is an
    ISO code rather than an integer surrogate key — _existing_ids() would
    fail to int()-cast these.
    """
    rows = session.execute(text(f"SELECT {pk_col} FROM {table}")).scalars().all()
    return {str(v) for v in rows}
