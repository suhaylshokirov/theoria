"""Bronze ingestion: year-partitioned discover catalogue.

Fetches TMDB's `discover/movie` endpoint one release year at a time and writes
each page as a raw JSON file to the Bronze layer on S3.

Why this exists alongside `ingest_movies.py`: `movie/popular` can only ever
return what is popular *right now*, which is why the catalogue built from it is
overwhelmingly recent. `discover/movie` lets us define the population we want —
"the most-voted films of each year from START to END, with at least
MIN_VOTES votes" — which turns ingestion into a deliberate corpus-design step
rather than a snapshot. Partitioning the request by year also sidesteps TMDB's
pagination ceiling, which no single query can page past.

S3 layout:
    bronze/discover/ingestion_date=YYYY-MM-DD/year=YYYY/page_NNNN.json

Returns the deduplicated list of discovered movie_ids, in the same shape as
`ingest_movies()`, so the downstream detail/credits ingestion is unchanged.

Usage:
    python -m etl.bronze.ingest_discover
    python -m etl.bronze.ingest_discover --start-year 1980 --end-year 2026 --pages-per-year 2
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import config
from etl import s3_utils
from etl.tmdb_client import TMDBClient

logger = logging.getLogger(__name__)


def ingest_discover(
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
    start_year: int = config.DISCOVER_START_YEAR,
    end_year: int = config.DISCOVER_END_YEAR,
    pages_per_year: int = config.DISCOVER_PAGES_PER_YEAR,
    min_votes: int = config.DISCOVER_MIN_VOTES,
) -> list[int]:
    """Fetch `discover/movie` per year and write each page to Bronze S3.

    Returns every movie_id discovered, deduplicated while preserving order.
    Each page is flushed to S3 before the next is fetched, so a failure on one
    year or page never loses the pages already written — the same
    fail-and-continue contract as `ingest_movies()`.

    Idempotent: re-running for the same ingestion_date overwrites the same keys.
    Note that the *contents* can change between dates as TMDB vote counts move;
    Bronze stays immutable per partition, which is the point.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    t0 = time.monotonic()
    logger.info(
        "Starting discover ingestion: years %d-%d, %d page(s)/year, "
        "min_votes=%d, date=%s",
        start_year, end_year, pages_per_year, min_votes, ingestion_date,
    )

    seen: set[int] = set()
    movie_ids: list[int] = []
    pages_written = 0
    pages_failed = 0

    for year in range(start_year, end_year + 1):
        for page in range(1, pages_per_year + 1):
            try:
                payload = client.discover_movies(
                    page=page, release_year=year, min_votes=min_votes
                )
                results = payload.get("results", [])

                key = s3_utils.build_path(
                    "bronze",
                    "discover",
                    ingestion_date,
                    f"year={year}/page_{page:04d}.json",
                )
                s3_utils.write_json(config.S3_BUCKET, key, payload)
                pages_written += 1

                new_ids = 0
                for movie in results:
                    movie_id = movie.get("id")
                    if movie_id is not None and movie_id not in seen:
                        seen.add(movie_id)
                        movie_ids.append(movie_id)
                        new_ids += 1

                logger.info(
                    "year=%d page %d: %d result(s), %d new (running total: %d)",
                    year, page, len(results), new_ids, len(movie_ids),
                )

                # No more pages for this year — stop early rather than
                # requesting empty pages for sparse years.
                if not results or page >= payload.get("total_pages", page):
                    break

            except Exception as exc:
                pages_failed += 1
                logger.error("year=%d page %d failed, skipping: %s", year, page, exc)

    elapsed = time.monotonic() - t0
    logger.info(
        "Discover ingestion complete: %d page(s) written, %d failed, "
        "%d unique movie_ids collected in %.2fs",
        pages_written, pages_failed, len(movie_ids), elapsed,
    )
    return movie_ids


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest TMDB discover/movie results to Bronze S3, by year."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=config.DISCOVER_START_YEAR,
        help=f"First release year to fetch (default: {config.DISCOVER_START_YEAR}).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=config.DISCOVER_END_YEAR,
        help=f"Last release year to fetch (default: {config.DISCOVER_END_YEAR}).",
    )
    parser.add_argument(
        "--pages-per-year",
        type=int,
        default=config.DISCOVER_PAGES_PER_YEAR,
        help=f"Pages per year, 20 results each (default: {config.DISCOVER_PAGES_PER_YEAR}).",
    )
    parser.add_argument(
        "--min-votes",
        type=int,
        default=config.DISCOVER_MIN_VOTES,
        help=f"Minimum TMDB vote count (default: {config.DISCOVER_MIN_VOTES}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_discover")
    args = _parse_args()
    ingest_discover(
        ingestion_date=args.date,
        start_year=args.start_year,
        end_year=args.end_year,
        pages_per_year=args.pages_per_year,
        min_votes=args.min_votes,
    )
