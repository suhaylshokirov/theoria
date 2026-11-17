"""Franchise (TMDB collection) pipeline stages, and a standalone runner (Task 153).

The collection stage is Bronze -> Silver -> warehouse load -> prune, for every
collection the date's film-detail partition references. It is three small
functions so each orchestrator can call them at the point its own sequencing
needs, rather than a block that has to sit in one place:

    fetch_collections(date)        Bronze  GET /collection/{id} x3 per collection
    build_collection_silver(date)  Silver  five Parquets
    load_collection_warehouse(date) warehouse  replace children, prune, slugs

``scripts/run_refresh.py`` (nightly) and ``scripts/run_pipeline.py`` (weekly
discovery) both call them. A stage wired into only one orchestrator is silently
missing from the other (the Task 65 gap), so both do.

**Each helper catches its own failure, logs it with a traceback and returns False.**
A franchise-stage problem must not stop the film/show refresh and the warehouse
load that surround it — the same posture as the person-translation backfill. The
warehouse load is one transaction, so a failure leaves yesterday's franchise rows
exactly as they were; the Silver DQ checks and warehouse checks that run later
still report the gap.

``python -m scripts.run_collections [--date D]`` runs only these stages (plus
their own DQ checks and the cache data-version publish), against whatever
``DATABASE_URL`` points at. It needs the date's Bronze film-detail partition and
the films already in the warehouse, so run it for a date a full run has covered.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import config
from data_quality.silver_checks import _COLLECTION_ENTITIES, run_silver_checks
from data_quality.warehouse_checks import run_collection_checks
from etl.bronze.ingest_collections import extract_collection_ids, ingest_collections
from etl.data_version import publish_data_version
from etl.silver.transform_collections import transform_collections
from etl.warehouse_loader.load_collections import load_collections

logger = logging.getLogger(__name__)


def fetch_collections(ingestion_date: dt.date) -> bool:
    """Bronze: every collection the date's films reference. False if the stage failed."""
    try:
        ids = extract_collection_ids(ingestion_date)
        succeeded, failed, gone = ingest_collections(ids, ingestion_date=ingestion_date)
        logger.info(
            "Bronze collections: %d fetched, %d failed, %d gone (404)",
            len(succeeded), len(failed), len(gone),
        )
        return True
    except Exception:
        logger.exception("Bronze collections failed; continuing without them")
        return False


def build_collection_silver(ingestion_date: dt.date) -> bool:
    """Silver: the five collection Parquets. False if the stage failed."""
    try:
        transform_collections(ingestion_date=ingestion_date)
        return True
    except Exception:
        logger.exception("Silver collections failed; continuing without them")
        return False


def load_collection_warehouse(ingestion_date: dt.date) -> bool:
    """Warehouse: replace children, prune orphans, reslug. False if the stage failed."""
    try:
        load_collections(ingestion_date=ingestion_date)
        return True
    except Exception:
        logger.exception("Collection warehouse load failed; yesterday's rows are untouched")
        return False


def run_collections(ingestion_date: dt.date | None = None) -> bool:
    """Run only the collection stages for one date. True when every stage and check passed."""
    config.require_etl()
    if ingestion_date is None:
        ingestion_date = dt.date.today()

    t0 = time.monotonic()
    logger.info("Starting collection run: ingestion_date=%s", ingestion_date)

    ok = (
        fetch_collections(ingestion_date)
        and build_collection_silver(ingestion_date)
    )
    if ok:
        silver_results = run_silver_checks(
            ingestion_date=ingestion_date, with_tv=False, only=_COLLECTION_ENTITIES
        )
        silver_failed = [r for r in silver_results if not r.passed]
        logger.info("Silver DQ checks (collections): %d failed", len(silver_failed))

        ok = load_collection_warehouse(ingestion_date)
        if ok:
            # The data is committed, so tell the site's cache. Never raises.
            publish_data_version(ingestion_date)
            warehouse_failed = [
                r for r in run_collection_checks(ingestion_date) if not r.passed
            ]
            logger.info("Warehouse checks (collections): %d failed", len(warehouse_failed))
            ok = not silver_failed and not warehouse_failed

    logger.info(
        "Collection run %s in %.2fs", "complete" if ok else "FINISHED WITH PROBLEMS",
        time.monotonic() - t0,
    )
    return ok


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run only the franchise (TMDB collection) stages for one date."
    )
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today. Must have a Bronze "
             "film-detail partition.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging

    setup_logging("run_collections")
    args = _parse_args()
    raise SystemExit(0 if run_collections(ingestion_date=args.date) else 1)
