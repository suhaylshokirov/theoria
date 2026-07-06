"""End-to-end pipeline runner: Bronze -> Silver -> Gold -> Warehouse, one date.

Sequences the existing, independently-tested stage functions in-process for a
single ingestion_date. Calling them as plain Python functions (rather than
shelling out to each script's CLI) lets movie_ids flow directly from
ingest_movies() into ingest_movie_details()/ingest_credits() as a local
variable — those two scripts require --movie-ids on the CLI, and nothing
persists that list to disk between separate process invocations.

Every stage here is independently idempotent (see each module's docstring),
so re-running this script for the same ingestion_date is safe.

Usage:
    python -m scripts.run_pipeline
    python -m scripts.run_pipeline --date 2026-07-06 --max-pages 5
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import config
from data_quality.silver_checks import run_silver_checks
from data_quality.warehouse_checks import run_warehouse_checks
from etl.bronze.ingest_credits import ingest_credits
from etl.bronze.ingest_genres import ingest_genres
from etl.bronze.ingest_movie_details import ingest_movie_details
from etl.bronze.ingest_movies import ingest_movies
from etl.gold.build_gold_datasets import build_gold_datasets
from etl.silver.transform_credits_bridge import transform_credits_bridge
from etl.silver.transform_genres import transform_genres
from etl.silver.transform_movies import transform_movies
from etl.silver.transform_people import transform_people
from etl.warehouse_loader.load_dimensions import load_dimensions
from etl.warehouse_loader.load_facts import load_facts

logger = logging.getLogger(__name__)


def run_pipeline(
    ingestion_date: dt.date | None = None,
    max_pages: int | None = None,
) -> None:
    """Run every ETL stage in order for a single ingestion_date.

    Bronze ingestion runs first and its movie_ids feed both movie_details and
    credits. Silver transforms depend on Bronze, Gold and the dimension load
    depend on Silver, and the fact load depends on dimensions already being
    loaded (it resolves foreign keys against them). Both DQ check suites run
    at the end and report failures without aborting, mirroring how they're
    used standalone elsewhere in the project.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if max_pages is None:
        max_pages = config.MAX_PAGES

    t0 = time.monotonic()
    logger.info(
        "Starting full pipeline run: ingestion_date=%s, max_pages=%d",
        ingestion_date, max_pages,
    )

    ingest_genres(ingestion_date=ingestion_date)
    movie_ids = ingest_movies(ingestion_date=ingestion_date, max_pages=max_pages)
    logger.info("Bronze movies: %d movie_id(s) discovered", len(movie_ids))

    succeeded_details, failed_details = ingest_movie_details(
        movie_ids, ingestion_date=ingestion_date
    )
    succeeded_credits, failed_credits = ingest_credits(
        movie_ids, ingestion_date=ingestion_date
    )
    logger.info(
        "Bronze details/credits: %d/%d details succeeded, %d/%d credits succeeded",
        len(succeeded_details), len(movie_ids),
        len(succeeded_credits), len(movie_ids),
    )

    transform_movies(ingestion_date=ingestion_date)
    transform_people(ingestion_date=ingestion_date)
    transform_genres(ingestion_date=ingestion_date)
    transform_credits_bridge(ingestion_date=ingestion_date)

    silver_results = run_silver_checks(ingestion_date=ingestion_date)
    silver_failed = [r for r in silver_results if not r.passed]
    if silver_failed:
        logger.warning("Silver DQ checks: %d check(s) failed", len(silver_failed))
    else:
        logger.info("Silver DQ checks: all passed")

    build_gold_datasets(ingestion_date=ingestion_date)

    load_dimensions(ingestion_date=ingestion_date)
    load_facts(ingestion_date=ingestion_date)

    warehouse_results = run_warehouse_checks(ingestion_date=ingestion_date)
    warehouse_failed = [r for r in warehouse_results if not r.passed]
    if warehouse_failed:
        logger.warning("Warehouse checks: %d check(s) failed", len(warehouse_failed))
    else:
        logger.info("Warehouse checks: all passed")

    elapsed = time.monotonic() - t0
    logger.info(
        "Pipeline run complete in %.2fs: %d movie(s), "
        "%d Silver DQ failure(s), %d warehouse check failure(s)",
        elapsed, len(movie_ids), len(silver_failed), len(warehouse_failed),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full Theoria pipeline (Bronze -> Silver -> Gold -> Warehouse) for one date."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=f"Number of Bronze movie-listing pages to fetch (default: config.MAX_PAGES={config.MAX_PAGES}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging

    setup_logging("run_pipeline")
    args = _parse_args()
    run_pipeline(ingestion_date=args.date, max_pages=args.max_pages)
