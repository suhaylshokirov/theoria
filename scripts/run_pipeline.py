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
from etl.bronze.ingest_discover_tv import ingest_discover_tv
from etl.bronze.ingest_genres import ingest_genres
from etl.bronze.ingest_imdb_episodes import ingest_imdb_episodes
from etl.bronze.ingest_imdb_ratings import ingest_imdb_ratings
from etl.bronze.ingest_movie_details import ingest_movie_details
from etl.bronze.ingest_movies import ingest_movies
from etl.bronze.ingest_people import ingest_people
from etl.bronze.ingest_seasons import ingest_seasons
from etl.bronze.ingest_series_details import ingest_series_details
from etl.gold.build_gold_datasets import build_gold_datasets
from etl.silver.transform_companies import transform_companies
from etl.silver.transform_credits_bridge import transform_credits_bridge
from etl.silver.transform_genres import transform_genres
from etl.silver.transform_imdb_ratings import transform_imdb_ratings
from etl.silver.transform_movie_links import transform_movie_links
from etl.silver.transform_movie_videos import transform_movie_videos
from etl.silver.transform_movies import transform_movies
from etl.silver.transform_people import transform_people
from etl.silver.transform_people_details import transform_people_details
from etl.silver.transform_episodes import transform_episodes
from etl.silver.transform_series import transform_series
from etl.silver.transform_series_credits import transform_series_credits
from etl.silver.transform_series_links import transform_series_links
from etl.silver.transform_series_videos import transform_series_videos
from etl.warehouse_loader.load_dimensions import load_dimensions
from etl.warehouse_loader.load_facts import load_facts

logger = logging.getLogger(__name__)


def _extract_company_ids(
    movie_ids: list[int],
    ingestion_date: dt.date,
    bucket: str,
    series_ids: list[int] | None = None,
) -> list[int]:
    """Re-read the Bronze detail files just written and return the deduplicated
    set of production_companies[].id across all of them.

    TMDB has no "list all companies" endpoint, and the company ids aren't in
    the discovery listing — they live inside each movie's (and each series')
    detail payload. This is the company-id equivalent of the movie_ids that
    ingest_movies() threads into ingest_movie_details(): the input to
    ingest_companies().

    `series_ids` (Task 79): when given, `bronze/series_details/<sid>.json` is
    swept too, so a studio that only ever made TV shows still gets its
    `GET /company/{id}` enrichment. TMDB has one company namespace across film
    and TV, so these merge into the same set with no disambiguation.
    """
    keys = [
        s3_utils.build_path("bronze", "movie_details", ingestion_date, f"{mid}.json")
        for mid in movie_ids
    ]
    keys += [
        s3_utils.build_path("bronze", "series_details", ingestion_date, f"{sid}.json")
        for sid in (series_ids or [])
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
    movie_ids: list[int],
    ingestion_date: dt.date,
    bucket: str,
    series_ids: list[int] | None = None,
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

    `series_ids` (Task 80): when given, `bronze/series_details/<sid>.json` is
    swept too, so a person who only ever worked on a TV show still gets their
    `GET /person/{id}` bio enrichment. The series payload nests its people
    under `aggregate_credits` and gives each a `roles[]` / `jobs[]` array
    rather than a flat `job` — handled below. TMDB has one person namespace
    across film and TV, so these merge into the same sets with no
    disambiguation.
    """
    keys = [
        s3_utils.build_path("bronze", "credits", ingestion_date, f"{mid}.json")
        for mid in movie_ids
    ]
    keys += [
        s3_utils.build_path("bronze", "series_details", ingestion_date, f"{sid}.json")
        for sid in (series_ids or [])
    ]
    lead: set[int] = set()
    rest: set[int] = set()
    _LEAD_JOBS = {"Director", "Writer", "Screenplay", "Story"}
    for key, raw, err in s3_utils.read_json_objects(bucket, keys):
        if err is not None or not raw:
            logger.warning("Could not read %s for person-id extraction: %s", key, err)
            continue
        # A series payload keeps cast/crew under aggregate_credits; a movie
        # credits payload has them at the root.
        agg = raw.get("aggregate_credits")
        cast = agg.get("cast") if agg else raw.get("cast")
        crew = agg.get("crew") if agg else raw.get("crew")
        for member in cast or []:
            pid = member.get("id")
            if pid is None or not member.get("profile_path"):
                continue
            order = member.get("order")
            (lead if order is not None and order < 10 else rest).add(pid)
        for member in crew or []:
            pid = member.get("id")
            if pid is None or not member.get("profile_path"):
                continue
            jobs = member.get("jobs")
            member_jobs = {j.get("job") for j in jobs} if jobs else {member.get("job")}
            (lead if member_jobs & _LEAD_JOBS else rest).add(pid)
    rest -= lead
    return sorted(lead) + sorted(rest)


def _warehouse_episode_counts() -> dict[int, int]:
    """{series_id: number_of_episodes} from dim_series, for ingest_seasons' change signal.

    Degrades to {} if dim_series does not exist yet (a partition replayed
    before 19_series.sql is applied) — so every series reads as newly seen.
    """
    try:
        from sqlalchemy import text
        from warehouse.db import get_session
        with get_session() as session:
            rows = session.execute(
                text("SELECT series_id, number_of_episodes FROM dim_series "
                     "WHERE number_of_episodes IS NOT NULL")
            ).all()
        return {int(sid): int(n) for sid, n in rows}
    except Exception as exc:
        logger.info("No dim_series episode counts available (%s) — all series treated as new", exc)
        return {}


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

    The TV series path runs unconditionally (Task 85 removed the `--with-tv`
    gate that Tasks 77–84 hid it behind): Bronze (discover_tv + series_details
    + seasons + the TV genre list), Silver (transform_series,
    transform_series_links, transform_series_credits, transform_episodes,
    transform_series_videos (Task 90), the TV half of transform_genres /
    transform_imdb_ratings / run_silver_checks, and transform_people folding
    in TV-only people), and the warehouse (load_dimensions / load_facts
    self-load dim_series, dim_network, dim_season, dim_episode,
    dim_series_video, the series bridges and the series/episode facts). The
    warehouse loaders and warehouse_checks still self-degrade when a partition
    has no series Silver files, so a pre-Task-85 partition replays unchanged.
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

    ingest_genres(ingestion_date=ingestion_date, with_tv=True)
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
    # IMDb's episode-mapping bulk file (Task 83) — same class of source, one
    # daily file, no per-entity cost. Feeds the episode-rating join in
    # transform_imdb_ratings(with_tv=True) below.
    ingest_imdb_episodes(ingestion_date=ingestion_date)

    # TV series (Tasks 77–84, turned on unconditionally in Task 85). Runs here,
    # before the company-id extraction, so a TV-only studio is picked up by
    # _extract_company_ids() and enriched too (Task 79). The Silver transforms
    # run below with the movie ones; the warehouse loaders self-degrade when
    # the series Silver files are absent, so an old partition replays unchanged.
    series_ids = ingest_discover_tv(ingestion_date=ingestion_date)
    logger.info("Bronze discover_tv: %d series_id(s) discovered", len(series_ids))
    succeeded_series, failed_series = ingest_series_details(
        series_ids, ingestion_date=ingestion_date
    )
    logger.info(
        "Bronze series details: %d/%d succeeded",
        len(succeeded_series), len(series_ids),
    )
    # Seasons/episodes (Task 82): bounded per run by TV_SEASONS_MAX_NEW.
    # dim_series' episode counts (last night's) decide which already-known
    # series get re-fetched. series_ids are already in discovery order.
    seasons_ok, seasons_failed = ingest_seasons(
        series_ids,
        ingestion_date=ingestion_date,
        known_episode_counts=_warehouse_episode_counts(),
    )
    logger.info(
        "Bronze seasons: %d series written, %d failed",
        len(seasons_ok), len(seasons_failed),
    )

    # Company ids only exist inside the movie- (and series-) detail payloads
    # just written. ingest_companies() then skips any already enriched in a
    # prior partition, so this is cheap on every run after the first (Task 65).
    company_ids = _extract_company_ids(
        movie_ids, ingestion_date, config.S3_BUCKET, series_ids=series_ids
    )
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
    person_ids = _extract_person_ids(
        movie_ids, ingestion_date, config.S3_BUCKET, series_ids=series_ids
    )
    succeeded_people, failed_people = ingest_people(
        person_ids, ingestion_date=ingestion_date
    )
    logger.info(
        "Bronze person details: %d new people fetched (of %d photo-having candidates)",
        len(succeeded_people), len(person_ids),
    )

    transform_movies(ingestion_date=ingestion_date)
    # TV Silver runs before transform_people so its series_people hand-off
    # exists when transform_people(with_tv=True) folds TV-only people into the
    # person dedupe (Task 80). transform_series[_links] have no movie-Silver
    # dependency, so their position here is free.
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
        ingestion_date=args.date,
        max_pages=args.max_pages,
        source=args.source,
    )
