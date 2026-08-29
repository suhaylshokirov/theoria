"""Refresh-mode pipeline runner: update films already in the warehouse.

``run_pipeline.py`` *discovers* films — it asks TMDB what is popular (or what
cleared a vote floor) and ingests whatever comes back. It has no way to say
"the 1,215 films I already have are stale, refetch them". Conflating the two
is what produced that gap, so this is a separate orchestrator, not a flag on
the other one.

Stage sequence mirrors ``run_pipeline.py`` exactly from Silver onward. The
only differences are at the head and tail:

  * the corpus comes from ``refresh_movies()`` (ids from ``dim_movie``), which
    writes the same Bronze ``movie_details`` + ``credits`` partitions the
    ingest path writes — so every transform and loader below is unchanged;
  * ``build_metrics_snapshot()`` runs after Gold, appending today's volatile
    metrics to ``gold/metrics_snapshot/`` before the warehouse load upserts
    ``fact_movie_metrics`` in place and the previous values are lost.

Genres and IMDb ratings are still ingested fresh: ``transform_genres`` needs a
Bronze genres file for the partition, and IMDb ratings / vote counts are the
fields that actually drift (the whole reason this job exists).

Every stage is idempotent per ``ingestion_date``, so re-running for the same
date is safe — which is what makes unattended nightly scheduling sound.

Usage:
    python -m scripts.run_refresh
    python -m scripts.run_refresh --date 2026-07-29
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

from data_quality.silver_checks import run_silver_checks
from data_quality.warehouse_checks import run_warehouse_checks
from etl.bronze.ingest_genres import ingest_genres
from etl.bronze.ingest_imdb_ratings import ingest_imdb_ratings
from etl.bronze.refresh_movies import refresh_movies
from etl.gold.build_gold_datasets import build_gold_datasets
from etl.gold.build_metrics_snapshot import build_metrics_snapshot
from etl.silver.transform_credits_bridge import transform_credits_bridge
from etl.silver.transform_genres import transform_genres
from etl.silver.transform_imdb_ratings import transform_imdb_ratings
from etl.silver.transform_movie_links import transform_movie_links
from etl.silver.transform_movies import transform_movies
from etl.silver.transform_people import transform_people
from etl.warehouse_loader.load_dimensions import load_dimensions
from etl.warehouse_loader.load_facts import load_facts
from etl.warehouse_loader.load_gold import load_gold

logger = logging.getLogger(__name__)


def run_refresh(ingestion_date: dt.date | None = None) -> None:
    """Refresh every film in the warehouse for a single ingestion_date."""
    if ingestion_date is None:
        ingestion_date = dt.date.today()

    t0 = time.monotonic()
    logger.info("Starting refresh run: ingestion_date=%s", ingestion_date)

    ingest_genres(ingestion_date=ingestion_date)
    succeeded, failed = refresh_movies(ingestion_date=ingestion_date)
    logger.info(
        "Bronze refresh: %d film(s) refreshed, %d failed", len(succeeded), len(failed)
    )
    ingest_imdb_ratings(ingestion_date=ingestion_date)

    transform_movies(ingestion_date=ingestion_date)
    transform_people(ingestion_date=ingestion_date)
    transform_genres(ingestion_date=ingestion_date)
    transform_credits_bridge(ingestion_date=ingestion_date)
    transform_movie_links(ingestion_date=ingestion_date)
    transform_imdb_ratings(ingestion_date=ingestion_date)

    silver_results = run_silver_checks(ingestion_date=ingestion_date)
    silver_failed = [r for r in silver_results if not r.passed]
    if silver_failed:
        logger.warning("Silver DQ checks: %d check(s) failed", len(silver_failed))
    else:
        logger.info("Silver DQ checks: all passed")

    build_gold_datasets(ingestion_date=ingestion_date)
    # Snapshot the volatile metrics to the lake *before* the warehouse load
    # upserts fact_movie_metrics in place and the previous run's values vanish.
    build_metrics_snapshot(ingestion_date=ingestion_date)

    load_dimensions(ingestion_date=ingestion_date)
    load_facts(ingestion_date=ingestion_date)
    load_gold(ingestion_date=ingestion_date)

    warehouse_results = run_warehouse_checks(ingestion_date=ingestion_date)
    warehouse_failed = [r for r in warehouse_results if not r.passed]
    if warehouse_failed:
        logger.warning("Warehouse checks: %d check(s) failed", len(warehouse_failed))
    else:
        logger.info("Warehouse checks: all passed")

    elapsed = time.monotonic() - t0
    logger.info(
        "Refresh run complete in %.2fs: %d film(s) refreshed, %d failed, "
        "%d Silver DQ failure(s), %d warehouse check failure(s)",
        elapsed, len(succeeded), len(failed), len(silver_failed), len(warehouse_failed),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh every film already in the warehouse for one date."
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

    setup_logging("run_refresh")
    args = _parse_args()
    run_refresh(ingestion_date=args.date)
