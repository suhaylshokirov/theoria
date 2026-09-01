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
    python -m scripts.run_pipeline --source discover
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import config
from data_quality.silver_checks import run_silver_checks
from data_quality.warehouse_checks import run_warehouse_checks
from etl import s3_utils
from etl.bronze.ingest_companies import ingest_companies
from etl.bronze.ingest_credits import ingest_credits
from etl.bronze.ingest_discover import ingest_discover
from etl.bronze.ingest_genres import ingest_genres
from etl.bronze.ingest_imdb_ratings import ingest_imdb_ratings
from etl.bronze.ingest_movie_details import ingest_movie_details
from etl.bronze.ingest_movies import ingest_movies
from etl.bronze.ingest_people import ingest_people
from etl.gold.build_gold_datasets import build_gold_datasets
from etl.silver.transform_companies import transform_companies
from etl.silver.transform_credits_bridge import transform_credits_bridge
from etl.silver.transform_genres import transform_genres
from etl.silver.transform_imdb_ratings import transform_imdb_ratings
from etl.silver.transform_movie_links import transform_movie_links
from etl.silver.transform_movies import transform_movies
from etl.silver.transform_people import transform_people
from etl.silver.transform_people_details import transform_people_details
from etl.warehouse_loader.load_dimensions import load_dimensions
from etl.warehouse_loader.load_facts import load_facts
from etl.warehouse_loader.load_gold import load_gold

logger = logging.getLogger(__name__)


def _extract_company_ids(
    movie_ids: list[int], ingestion_date: dt.date, bucket: str
) -> list[int]:
    """Re-read the Bronze movie-detail files just written and return the
    deduplicated set of production_companies[].id across all of them.

    TMDB has no "list all companies" endpoint, and the company ids aren't in
    the discovery listing — they live inside each movie's detail payload. This
    is the company-id equivalent of the movie_ids that ingest_movies() threads
    into ingest_movie_details(): the input to ingest_companies().
    """
    keys = [
        s3_utils.build_path("bronze", "movie_details", ingestion_date, f"{mid}.json")
        for mid in movie_ids
    ]
    company_ids: set[int] = set()
    for key, raw, err in s3_utils.read_json_objects(bucket, keys):
        if err is not None or not raw:
            logger.warning("Could not read %s for company-id extraction: %s", key, err)
            continue
        for company in raw.get("production_companies") or []:
            cid = company.get("id")
            if cid is not None:
                company_ids.add(cid)
    return sorted(company_ids)


def _extract_person_ids(
    movie_ids: list[int], ingestion_date: dt.date, bucket: str
) -> list[int]:
    """Re-read the Bronze credits files just written and return the person ids
    worth enriching, most-reachable first.

    Only people with a `profile_path` are kept — `profile_path` predicts a bio
    sharply (62% vs 7%), so a photo is the cheap free filter that spends the
    per-run cap on people who actually have something to fetch (Task 72).

    Ordering: billed cast (`order < 10`) and directors/writers lead, then
    everyone else with a photo. ingest_people()'s max_new cap truncates the
    tail, so the people a reader reaches first are always fetched first, and
    the long tail fills in over subsequent nightly runs. Within a priority
    band ids are sorted so two runs process in the same order.
    """
    keys = [
        s3_utils.build_path("bronze", "credits", ingestion_date, f"{mid}.json")
        for mid in movie_ids
    ]
    lead: set[int] = set()
    rest: set[int] = set()
    _LEAD_JOBS = {"Director", "Writer", "Screenplay", "Story"}
    for key, raw, err in s3_utils.read_json_objects(bucket, keys):
        if err is not None or not raw:
            logger.warning("Could not read %s for person-id extraction: %s", key, err)
            continue
        for member in raw.get("cast") or []:
            pid = member.get("id")
            if pid is None or not member.get("profile_path"):
                continue
            order = member.get("order")
            (lead if order is not None and order < 10 else rest).add(pid)
        for member in raw.get("crew") or []:
            pid = member.get("id")
            if pid is None or not member.get("profile_path"):
                continue
            (lead if member.get("job") in _LEAD_JOBS else rest).add(pid)
    rest -= lead
    return sorted(lead) + sorted(rest)


def run_pipeline(
    ingestion_date: dt.date | None = None,
    max_pages: int | None = None,
    source: str = "popular",
) -> None:
    """Run every ETL stage in order for a single ingestion_date.

    Bronze ingestion runs first and its movie_ids feed both movie_details and
    credits. Silver transforms depend on Bronze, Gold and the dimension load
    depend on Silver, and the fact load depends on dimensions already being
    loaded (it resolves foreign keys against them). Both DQ check suites run
    at the end and report failures without aborting, mirroring how they're
    used standalone elsewhere in the project.

    `source` selects which Bronze catalogue defines the corpus: "popular"
    (whatever TMDB is featuring today) or "discover" (the most-voted films of
    each year in a configured range). Everything downstream is identical —
    both return a plain list of movie_ids.
    """
    # Fail on a missing TMDB/AWS secret now, not 4 minutes into ingestion.
    config.require_etl()

    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if max_pages is None:
        max_pages = config.MAX_PAGES

    t0 = time.monotonic()
    logger.info(
        "Starting full pipeline run: ingestion_date=%s, source=%s, max_pages=%d",
        ingestion_date, source, max_pages,
    )

    ingest_genres(ingestion_date=ingestion_date)
    if source == "discover":
        movie_ids = ingest_discover(ingestion_date=ingestion_date)
    else:
        movie_ids = ingest_movies(ingestion_date=ingestion_date, max_pages=max_pages)
    logger.info("Bronze %s: %d movie_id(s) discovered", source, len(movie_ids))

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
    # IMDb's ratings snapshot is a single daily file, not per-movie, so it has
    # no movie_ids dependency — but its Silver transform reads
    # transform_movies()'s output below, so it must run after that.
    ingest_imdb_ratings(ingestion_date=ingestion_date)

    # Company ids only exist inside the movie-detail payloads just written.
    # ingest_companies() then skips any already enriched in a prior partition,
    # so this is cheap on every run after the first (Task 65).
    company_ids = _extract_company_ids(movie_ids, ingestion_date, config.S3_BUCKET)
    succeeded_companies, failed_companies = ingest_companies(
        company_ids, ingestion_date=ingestion_date
    )
    logger.info(
        "Bronze company details: %d/%d new companies fetched",
        len(succeeded_companies), len(company_ids),
    )

    # Person ids likewise only exist inside the credits payloads just written.
    # ingest_people() skips anyone already enriched in a prior partition and
    # caps how many new people it fetches per run (Task 72), so on a
    # steady-state run this is cheap.
    person_ids = _extract_person_ids(movie_ids, ingestion_date, config.S3_BUCKET)
    succeeded_people, failed_people = ingest_people(
        person_ids, ingestion_date=ingestion_date
    )
    logger.info(
        "Bronze person details: %d new people fetched (of %d photo-having candidates)",
        len(succeeded_people), len(person_ids),
    )

    transform_movies(ingestion_date=ingestion_date)
    transform_people(ingestion_date=ingestion_date)
    transform_people_details(ingestion_date=ingestion_date)
    transform_genres(ingestion_date=ingestion_date)
    transform_credits_bridge(ingestion_date=ingestion_date)
    transform_movie_links(ingestion_date=ingestion_date)
    transform_companies(ingestion_date=ingestion_date)
    transform_imdb_ratings(ingestion_date=ingestion_date)

    silver_results = run_silver_checks(ingestion_date=ingestion_date)
    silver_failed = [r for r in silver_results if not r.passed]
    if silver_failed:
        logger.warning("Silver DQ checks: %d check(s) failed", len(silver_failed))
    else:
        logger.info("Silver DQ checks: all passed")

    build_gold_datasets(ingestion_date=ingestion_date)

    load_dimensions(ingestion_date=ingestion_date)
    load_facts(ingestion_date=ingestion_date)
    # Gold last: fact_collaboration's FKs point at dim_person, so the dimension
    # load has to have committed first.
    load_gold(ingestion_date=ingestion_date)

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
    parser.add_argument(
        "--source",
        choices=["popular", "discover"],
        default="popular",
        help=(
            "Which Bronze catalogue defines the corpus: 'popular' (what TMDB "
            "features today) or 'discover' (most-voted films per year over the "
            "configured DISCOVER_* range). Default: popular."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging

    setup_logging("run_pipeline")
    args = _parse_args()
    run_pipeline(
        ingestion_date=args.date, max_pages=args.max_pages, source=args.source
    )
