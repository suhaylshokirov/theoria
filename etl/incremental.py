"""Incremental load logic: watermark tracking and S3 partition discovery.

Each warehouse loader (load_dimensions.py, load_facts.py) is idempotent per
ingestion_date, but re-running it for every historical partition on every
invocation is wasteful. This module lets a loader remember the last
ingestion_date it successfully processed (its "watermark", stored in the
etl_watermarks table) and discover which Silver partitions in S3 are newer
than that, so a scheduled run only does work for partitions it hasn't seen.

Watermarks are keyed by an arbitrary `loader_name` (e.g. "load_dimensions"),
so independent loaders can progress independently.

S3 sources:
    <layer>/<entity>/ingestion_date=YYYY-MM-DD/...

Usage:
    from etl.incremental import get_watermark, set_watermark, pending_partitions

    with get_session() as session:
        dates = pending_partitions(session, "load_dimensions", bucket, "silver", "movies")
    for date in dates:
        load_dimensions(ingestion_date=date)
        with get_session() as session:
            set_watermark(session, "load_dimensions", date)
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from etl import s3_utils

logger = logging.getLogger(__name__)


def get_watermark(session: Session, loader_name: str) -> dt.date | None:
    """Return the last ingestion_date successfully processed by `loader_name`, or None."""
    row = session.execute(
        text("SELECT last_ingestion_date FROM etl_watermarks WHERE loader_name = :name"),
        {"name": loader_name},
    ).first()
    return row[0] if row else None


def set_watermark(session: Session, loader_name: str, ingestion_date: dt.date) -> None:
    """Upsert the watermark for `loader_name` to `ingestion_date`."""
    session.execute(
        text(
            "INSERT INTO etl_watermarks (loader_name, last_ingestion_date, updated_at) "
            "VALUES (:name, :date, now()) "
            "ON CONFLICT (loader_name) DO UPDATE SET "
            "last_ingestion_date = EXCLUDED.last_ingestion_date, updated_at = EXCLUDED.updated_at"
        ),
        {"name": loader_name, "date": ingestion_date},
    )


def list_available_partitions(bucket: str, layer: str, entity: str) -> list[dt.date]:
    """Return every ingestion_date partition present under <layer>/<entity>/ in S3, sorted ascending."""
    prefix = f"{layer}/{entity}/"
    client = s3_utils.get_s3_client()
    paginator = client.get_paginator("list_objects_v2")

    dates: list[dt.date] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for common_prefix in page.get("CommonPrefixes", []):
            partition = common_prefix["Prefix"][len(prefix):].rstrip("/")
            if not partition.startswith("ingestion_date="):
                continue
            raw_date = partition.split("=", 1)[1]
            try:
                dates.append(dt.date.fromisoformat(raw_date))
            except ValueError:
                logger.warning("Skipping unparseable partition: %s", common_prefix["Prefix"])

    return sorted(dates)


def pending_partitions(
    session: Session,
    loader_name: str,
    bucket: str,
    layer: str,
    entity: str,
) -> list[dt.date]:
    """Return partitions newer than `loader_name`'s watermark, sorted ascending.

    If no watermark has ever been recorded, every available partition is pending.
    """
    watermark = get_watermark(session, loader_name)
    available = list_available_partitions(bucket, layer, entity)
    if watermark is None:
        return available
    return [date for date in available if date > watermark]
