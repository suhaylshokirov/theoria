"""Refresh-mode pipeline runner: update films already in the warehouse.

``run_pipeline.py`` *discovers* films — it asks TMDB what is popular (or what
cleared a vote floor) and ingests whatever comes back. It has no way to say
"the 1,215 films I already have are stale, refetch them". Conflating the two
is what produced that gap, so this is a separate orchestrator, not a flag on
the other one.

Stage sequence mirrors ``run_pipeline.py`` exactly from Silver onward. The
only differences are at the head and tail:

  * the movie corpus comes from ``refresh_movies()`` (ids from ``dim_movie``)
    and the TV corpus from ``refresh_series()`` (ids from ``dim_series``) —
    both write the same Bronze partitions the discovery/ingest path writes, so
    every transform and loader below is unchanged;
  * ``build_metrics_snapshot()`` runs after Gold, appending today's volatile
    metrics to ``gold/metrics_snapshot/`` before the warehouse load upserts
    ``fact_movie_metrics`` in place and the previous values are lost.

TV runs unconditionally here as of Task 85 (it removed the ``--with-tv`` gate):
``refresh_series`` re-fetches every known show, ``ingest_seasons`` picks up
newly-aired episodes bounded by ``TV_SEASONS_MAX_NEW``, and the series/episode
Silver transforms + warehouse loaders run beside the movie ones. The warehouse
loaders self-degrade when a partition has no series Silver files, so a
pre-Task-85 partition still replays cleanly.

Genres and the two IMDb bulk files are ingested fresh every run:
``transform_genres`` needs a Bronze genres file for the partition (movie + TV
lists); IMDb ratings / vote counts are the fields that actually drift (the
whole reason this job exists); and the IMDb episode-mapping file (Task 83)
feeds the episode-rating join.

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

import config
from data_quality.silver_checks import run_silver_checks
from data_quality.warehouse_checks import run_warehouse_checks
from etl.bronze.ingest_companies import ingest_companies
from etl.bronze.ingest_genres import ingest_genres
from etl.bronze.ingest_imdb_episodes import ingest_imdb_episodes
from etl.bronze.ingest_imdb_ratings import ingest_imdb_ratings
from etl.bronze.ingest_people import ingest_people
from etl.bronze.ingest_seasons import ingest_seasons
from etl.bronze.refresh_movies import refresh_movies
from etl.bronze.refresh_series import refresh_series
from etl.gold.build_gold_datasets import build_gold_datasets
from etl.gold.build_metrics_snapshot import build_metrics_snapshot
from etl.silver.transform_companies import transform_companies
from etl.silver.transform_credits_bridge import transform_credits_bridge
from etl.silver.transform_episodes import transform_episodes
from etl.silver.transform_genres import transform_genres
from etl.silver.transform_imdb_ratings import transform_imdb_ratings
from etl.silver.transform_movie_links import transform_movie_links
from etl.silver.transform_movie_videos import transform_movie_videos
from etl.silver.transform_movies import transform_movies
from etl.silver.transform_people import transform_people
from etl.silver.transform_people_details import transform_people_details
from etl.silver.transform_series import transform_series
from etl.silver.transform_series_credits import transform_series_credits
from etl.silver.transform_series_links import transform_series_links
from etl.silver.transform_series_videos import transform_series_videos
from etl.warehouse_loader.load_dimensions import load_dimensions
from etl.warehouse_loader.load_facts import load_facts
from scripts.run_pipeline import (
    _extract_company_ids,
    _extract_person_ids,
    _warehouse_episode_counts,
)

logger = logging.getLogger(__name__)


def run_refresh(ingestion_date: dt.date | None = None) -> None:
    """Refresh every film and series in the warehouse for a single ingestion_date."""
    # Fail on a missing TMDB/AWS secret now, not 4 minutes into ingestion.
    config.require_etl()

    if ingestion_date is None:
        ingestion_date = dt.date.today()

    t0 = time.monotonic()
    logger.info("Starting refresh run: ingestion_date=%s", ingestion_date)

    ingest_genres(ingestion_date=ingestion_date, with_tv=True)
    succeeded, failed = refresh_movies(ingestion_date=ingestion_date)
    logger.info(
        "Bronze refresh: %d film(s) refreshed, %d failed", len(succeeded), len(failed)
    )

    # TV: re-fetch every show already in dim_series (Task 85). refresh_series
    # writes the same bronze/series_details partition the discovery path writes,
    # so ingest_seasons and every TV Silver transform below run unchanged.
    series_ok, series_failed = refresh_series(ingestion_date=ingestion_date)
    logger.info(
        "Bronze series refresh: %d show(s) refreshed, %d failed",
        len(series_ok), len(series_failed),
    )
    # Seasons/episodes, bounded per run by TV_SEASONS_MAX_NEW. A show already in
    # dim_series is re-fetched only when its episode count moved (a running show
    # the night it airs); a finished show is fetched once, ever.
    seasons_ok, seasons_failed = ingest_seasons(
        series_ok,
        ingestion_date=ingestion_date,
        known_episode_counts=_warehouse_episode_counts(),
    )
    logger.info(
        "Bronze seasons: %d series written, %d failed",
        len(seasons_ok), len(seasons_failed),
    )

    ingest_imdb_ratings(ingestion_date=ingestion_date)
    # Second IMDb bulk file (Task 83): the episode -> tconst mapping, feeding
    # the episode-rating join in transform_imdb_ratings(with_tv=True) below.
    ingest_imdb_episodes(ingestion_date=ingestion_date)

    # A refreshed film or show can gain a production company that isn't
    # enriched yet. ingest_companies() skips every already-enriched id, so on a
    # steady-state nightly run this fetches nothing; transform_companies() below
    # still has to run so the loader has a company_details Silver file for this
    # partition and the cumulative enrichment survives the Neon write (Task 65).
    company_ids = _extract_company_ids(
        succeeded, ingestion_date, config.S3_BUCKET, series_ids=series_ok
    )
    ingest_companies(company_ids, ingestion_date=ingestion_date)

    # Same as companies: a refreshed film or show can gain a new billed cast
    # member. ingest_people() skips everyone already enriched and caps new
    # fetches per run, and transform_people_details() below still has to run so
    # the loader has a person_details Silver file for this partition and the
    # cumulative enrichment survives the Neon write (Task 65 / Task 72).
    person_ids = _extract_person_ids(
        succeeded, ingestion_date, config.S3_BUCKET, series_ids=series_ok
    )
    ingest_people(person_ids, ingestion_date=ingestion_date)

    transform_movies(ingestion_date=ingestion_date)
    # TV Silver runs before transform_people so its series_people hand-off
    # exists when transform_people(with_tv=True) folds TV-only people into the
    # person dedupe (Task 80).
    transform_series(ingestion_date=ingestion_date)
    transform_series_links(ingestion_date=ingestion_date)
    transform_series_credits(ingestion_date=ingestion_date)
    transform_episodes(ingestion_date=ingestion_date)
    transform_series_videos(ingestion_date=ingestion_date)
    transform_people(ingestion_date=ingestion_date, with_tv=True)
    transform_people_details(ingestion_date=ingestion_date)
    transform_genres(ingestion_date=ingestion_date, with_tv=True)
    transform_credits_bridge(ingestion_date=ingestion_date)
    transform_movie_links(ingestion_date=ingestion_date)
    transform_movie_videos(ingestion_date=ingestion_date)
    transform_companies(ingestion_date=ingestion_date)
    transform_imdb_ratings(ingestion_date=ingestion_date, with_tv=True)

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

    warehouse_results = run_warehouse_checks(ingestion_date=ingestion_date)
    warehouse_failed = [r for r in warehouse_results if not r.passed]
    if warehouse_failed:
        logger.warning("Warehouse checks: %d check(s) failed", len(warehouse_failed))
    else:
        logger.info("Warehouse checks: all passed")

    elapsed = time.monotonic() - t0
    logger.info(
        "Refresh run complete in %.2fs: %d film(s) refreshed (%d failed), "
        "%d show(s) refreshed (%d failed), %d Silver DQ failure(s), "
        "%d warehouse check failure(s)",
        elapsed, len(succeeded), len(failed), len(series_ok), len(series_failed),
        len(silver_failed), len(warehouse_failed),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh every film and TV series already in the warehouse for one date."
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
