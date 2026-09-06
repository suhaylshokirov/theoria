"""Shared plumbing for the warehouse loaders (dimensions and facts).

Houses the S3 read helper, the generic upsert builder, the replace-by-parent
writer, and the reject-quarantine writer that both load_dimensions.py and
load_facts.py depend on, so the ON CONFLICT SQL, the Silver Parquet read path,
and the "never drop a bad row" convention only need to change in one place.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Column, MetaData, Table, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from etl import s3_utils

logger = logging.getLogger(__name__)


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


def _replace_by_parent(
    session: Session,
    table: str,
    parent_col: str,
    parent_ids: list[Any],
    columns: list[str],
    records: list[dict[str, Any]],
) -> int:
    """Replace every row of `table` for the given parent ids: scoped DELETE then insert.

    The one loader pattern here that lets a parent's child set *shrink*. Every
    other loader calls _upsert(), which can add and update but never delete —
    fine for tables that only accumulate. dim_movie_video is different: TMDB
    removes videos and YouTube keys rot, so a pure upsert would leave a dead
    embed on the page forever, with no failing check. This deletes the parent's
    existing rows first, so a video that vanished upstream vanishes here too.

    The delete is scoped to `parent_ids` — the parents present in *this*
    partition — via ``WHERE parent_col = ANY(:ids)``, never a blanket TRUNCATE.
    A partition only knows about the films it ingested; wiping rows for films
    absent from it would destroy data this run has no knowledge of.

    The insert is a Core construct (batched by insertmanyvalues) with no
    ON CONFLICT — the matching rows were just deleted, so there is nothing to
    conflict with.
    """
    if not parent_ids:
        return 0
    session.execute(
        text(f"DELETE FROM {table} WHERE {parent_col} = ANY(:parent_ids)"),
        {"parent_ids": list(parent_ids)},
    )
    if records:
        tbl = Table(table, MetaData(), *(Column(c) for c in columns))
        session.execute(pg_insert(tbl), records)
    return len(records)


def _write_rejects(
    rejects: list[dict[str, Any]], entity: str, ingestion_date: dt.date,
    rejected_dir: Path,
) -> Path | None:
    """Write quarantined rows to a local Parquet file. Returns the path, or None if empty.

    The standing "quarantine bad rows, never silently drop them" rule (since
    Task 58). Shared by both loaders so the reject-file layout is defined once.
    """
    if not rejects:
        return None
    df = pd.DataFrame(rejects)
    rejected_dir.mkdir(parents=True, exist_ok=True)
    path = rejected_dir / f"{entity}_rejected_{ingestion_date.isoformat()}.parquet"
    df.to_parquet(path, engine="pyarrow", index=False)
    logger.warning("Wrote %d rejected row(s) for entity=%s to %s", len(df), entity, path)
    return path


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
