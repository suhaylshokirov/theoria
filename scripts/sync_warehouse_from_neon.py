"""Pull the warehouse from Neon into the local Postgres replica.

Topology (see docs/architecture.md 4.3):

    TMDB / IMDb --> GitHub Actions nightly job --> Neon  (source of truth)
                                                    |
                          scripts/sync_warehouse_from_neon.py  (this file)
                                                    v
                                          local Postgres  <-- Django serves from here

The nightly refresh runs in the cloud and writes Neon. Django, running on the
laptop, would pay a ~90 ms cross-region round-trip on every query if it read
Neon directly — several seconds per page. So Django reads a local copy instead,
and this script refreshes that copy on demand: run it when you sit down to work,
or after you know the nightly job has finished.

It is a full, point-in-time snapshot, not incremental: every warehouse table is
truncated and reloaded from Neon inside one transaction. FK triggers are disabled
for the load (`session_replication_role = replica`) because the snapshot is
already internally consistent and table load order would otherwise matter.

`pg_dump` is deliberately not used: the local client is v16, Neon is v18, and
pg_dump refuses to dump from a newer server. A plain `COPY ... TO/FROM STDOUT`
streamed through libpq has no such version check.

Usage:
    python -m scripts.sync_warehouse_from_neon
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from tempfile import SpooledTemporaryFile
from urllib.parse import urlparse

import psycopg2

import config

logger = logging.getLogger(__name__)

# Every table the ETL pipeline owns. dim/fact split is cosmetic here — the whole
# warehouse is replaced as one unit.
WAREHOUSE_TABLES = [
    "dim_movie",
    "dim_person",
    "dim_genre",
    "dim_collection",
    "dim_company",
    "dim_country",
    "dim_language",
    "dim_date",
    "fact_movie_metrics",
    "fact_movie_rating",
    "fact_credit",
    "fact_collaboration",
    "bridge_movie_company",
    "bridge_movie_country",
    "bridge_movie_language",
    "etl_watermarks",
]

# Buffer a table in memory up to this size, then spill to a temp file on disk.
_SPILL_THRESHOLD_BYTES = 64 * 1024 * 1024

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _to_libpq(url: str) -> str:
    """SQLAlchemy-style 'postgresql+psycopg2://' -> plain 'postgresql://'."""
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def _host_of(url: str) -> str:
    return (urlparse(_to_libpq(url)).hostname or "").lower()


def _guard(source_url: str, target_url: str) -> None:
    """Refuse to run unless we are pulling FROM remote INTO a local database.

    The one irreversible mistake this script could make is swapping the two
    endpoints and truncating Neon, so both directions are checked explicitly.
    """
    if not source_url:
        raise RuntimeError(
            "NEON_DATABASE_URL is not set. Add it to .env (the Neon endpoint to "
            "pull from). See .env.example."
        )
    source_host = _host_of(source_url)
    target_host = _host_of(target_url)

    if source_host in _LOCAL_HOSTS:
        raise RuntimeError(
            f"NEON_DATABASE_URL points at a local host ({source_host!r}); it must "
            "be the remote Neon endpoint."
        )
    if target_host not in _LOCAL_HOSTS:
        raise RuntimeError(
            f"DATABASE_URL points at a non-local host ({target_host!r}). This "
            "script truncates every warehouse table in DATABASE_URL — it will "
            "only do that to a local replica."
        )


def _copy_table(source_cur, target_cur, table: str) -> int:
    """Stream one table's rows from source to target via COPY, return row count."""
    with SpooledTemporaryFile(max_size=_SPILL_THRESHOLD_BYTES, mode="w+b") as buf:
        source_cur.copy_expert(f"COPY {table} TO STDOUT", buf)
        buf.seek(0)
        target_cur.copy_expert(f"COPY {table} FROM STDIN", buf)
    target_cur.execute(f"SELECT count(*) FROM {table}")
    return target_cur.fetchone()[0]


def sync_warehouse_from_neon() -> dict[str, int]:
    """Replace every local warehouse table with Neon's current contents.

    Returns {table: row_count}. Raises if the endpoint guard fails or if any
    table's post-load count does not match Neon's.
    """
    source_url = config.NEON_DATABASE_URL
    target_url = config.DATABASE_URL
    _guard(source_url, target_url)

    t0 = time.monotonic()
    logger.info(
        "Syncing %d warehouse tables: %s -> %s",
        len(WAREHOUSE_TABLES),
        _host_of(source_url),
        _host_of(target_url) or "localhost",
    )

    counts: dict[str, int] = {}
    source = psycopg2.connect(_to_libpq(source_url))
    target = psycopg2.connect(_to_libpq(target_url))
    try:
        source.set_session(readonly=True, autocommit=True)
        with source.cursor() as scur, target.cursor() as tcur:
            # One transaction on the target: either the whole warehouse swaps or
            # nothing does. FK triggers off — the snapshot is already consistent.
            tcur.execute("SET session_replication_role = replica")
            tcur.execute(
                "TRUNCATE {} RESTART IDENTITY".format(", ".join(WAREHOUSE_TABLES))
            )
            for table in WAREHOUSE_TABLES:
                t_table = time.monotonic()
                local_count = _copy_table(scur, tcur, table)
                scur.execute(f"SELECT count(*) FROM {table}")
                remote_count = scur.fetchone()[0]
                if local_count != remote_count:
                    raise RuntimeError(
                        f"{table}: loaded {local_count} rows but Neon has "
                        f"{remote_count} — aborting, transaction rolled back."
                    )
                counts[table] = local_count
                logger.info(
                    "  %-22s %8d rows  (%.1fs)",
                    table, local_count, time.monotonic() - t_table,
                )
            tcur.execute("SET session_replication_role = DEFAULT")
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()

    logger.info(
        "Warehouse sync complete: %d tables, %d total rows in %.1fs",
        len(counts), sum(counts.values()), time.monotonic() - t0,
    )
    return counts


def _needs_sync(local: dt.date | None, remote: dt.date | None) -> bool:
    """Decide whether the replica is behind Neon.

    Pure so the decision can be tested without a database: sync when the replica
    has never been loaded, or when Neon carries a newer ingestion_date. Never
    sync when Neon itself has no data.
    """
    if remote is None:
        return False
    return local is None or local < remote


def _max_ingestion_date(conn) -> dt.date | None:
    with conn.cursor() as cur:
        cur.execute("SELECT max(ingestion_date) FROM fact_movie_rating")
        return cur.fetchone()[0]


def sync_if_stale() -> bool:
    """Sync only when Neon's latest ingestion_date is newer than the replica's.

    Returns True if a sync ran. A Neon connectivity problem is logged and
    swallowed (returns False) — a laptop offline at breakfast should still be
    able to start the site on yesterday's replica.
    """
    source_url = config.NEON_DATABASE_URL
    target_url = config.DATABASE_URL
    try:
        _guard(source_url, target_url)
    except RuntimeError as exc:
        logger.warning("Replica freshness check skipped: %s", exc)
        return False

    try:
        source = psycopg2.connect(_to_libpq(source_url), connect_timeout=10)
    except psycopg2.OperationalError as exc:
        logger.warning("Neon unreachable — starting on the existing replica: %s", exc)
        return False
    try:
        source.set_session(readonly=True, autocommit=True)
        remote = _max_ingestion_date(source)
    finally:
        source.close()

    target = psycopg2.connect(_to_libpq(target_url))
    try:
        local = _max_ingestion_date(target)
    finally:
        target.close()

    if not _needs_sync(local, remote):
        logger.info("Local replica is current (ingestion_date=%s) — no sync needed.", local)
        return False

    logger.info("Neon is ahead (%s > %s) — refreshing the local replica.", remote, local)
    sync_warehouse_from_neon()
    return True


if __name__ == "__main__":
    import argparse

    from etl.logging_config import setup_logging

    setup_logging("sync_warehouse_from_neon")
    parser = argparse.ArgumentParser(
        description="Pull the warehouse from Neon into the local Postgres replica."
    )
    parser.add_argument(
        "--if-stale",
        action="store_true",
        help="Only sync when Neon's ingestion_date is newer than the replica's.",
    )
    args = parser.parse_args()
    if args.if_stale:
        sync_if_stale()
    else:
        sync_warehouse_from_neon()
