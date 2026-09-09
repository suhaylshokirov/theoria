"""Bronze ingestion: year-partitioned discover catalogue for TV series.

The exact TV counterpart of `ingest_discover.py`. Fetches TMDB's `discover/tv`
endpoint one first-air year at a time and writes each page as a raw JSON file
to the Bronze layer on S3.

Why a separate module rather than a flag on `ingest_discover.py`: the two hit
different endpoints, partition under different Bronze prefixes, and feed
different downstream detail ingests (`ingest_series_details` vs
`ingest_movie_details`). The shape is deliberately identical so the pattern is
recognisable, but there is no shared behaviour to factor out.

S3 layout:
    bronze/discover_tv/ingestion_date=YYYY-MM-DD/year=YYYY/page_NNNN.json

Returns the deduplicated list of discovered series_ids, in discovery order, the
same shape `ingest_discover()` returns movie_ids — so the downstream
series-detail ingestion is a plain hand-off.

Usage:
    python -m etl.bronze.ingest_discover_tv
    python -m etl.bronze.ingest_discover_tv --start-year 1990 --end-year 2026 --pages-per-year 2
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


def ingest_discover_tv(
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
    start_year: int = config.TV_DISCOVER_START_YEAR,
    end_year: int = config.TV_DISCOVER_END_YEAR,
    pages_per_year: int = config.TV_DISCOVER_PAGES_PER_YEAR,
    min_votes: int = config.TV_DISCOVER_MIN_VOTES,
) -> list[int]:
    """Fetch `discover/tv` per first-air year and write each page to Bronze S3.

    Returns every series_id discovered, deduplicated while preserving order.
    Each page is flushed to S3 before the next is fetched, so a failure on one
    year or page never loses the pages already written — the same
    fail-and-continue contract as `ingest_discover()`.

    Idempotent: re-running for the same ingestion_date overwrites the same
    keys. The *contents* can shift between dates as TMDB vote counts move;
    Bronze stays immutable per partition, which is the point.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    t0 = time.monotonic()
    logger.info(
        "Starting discover-tv ingestion: years %d-%d, %d page(s)/year, "
        "min_votes=%d, date=%s",
        start_year, end_year, pages_per_year, min_votes, ingestion_date,
    )

    seen: set[int] = set()
    series_ids: list[int] = []
    pages_written = 0
    pages_failed = 0

    for year in range(start_year, end_year + 1):
        for page in range(1, pages_per_year + 1):
            try:
                payload = client.discover_tv(
                    page=page, first_air_year=year, min_votes=min_votes
                )
                results = payload.get("results", [])

                key = s3_utils.build_path(
                    "bronze",
                    "discover_tv",
                    ingestion_date,
                    f"year={year}/page_{page:04d}.json",
                )
                s3_utils.write_json(config.S3_BUCKET, key, payload)
                pages_written += 1

                new_ids = 0
                for series in results:
                    series_id = series.get("id")
                    if series_id is not None and series_id not in seen:
                        seen.add(series_id)
                        series_ids.append(series_id)
                        new_ids += 1

                logger.info(
                    "year=%d page %d: %d result(s), %d new (running total: %d)",
                    year, page, len(results), new_ids, len(series_ids),
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
        "Discover-tv ingestion complete: %d page(s) written, %d failed, "
        "%d unique series_ids collected in %.2fs",
        pages_written, pages_failed, len(series_ids), elapsed,
    )
    return series_ids


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest TMDB discover/tv results to Bronze S3, by first-air year."
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
        default=config.TV_DISCOVER_START_YEAR,
        help=f"First air year to fetch (default: {config.TV_DISCOVER_START_YEAR}).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=config.TV_DISCOVER_END_YEAR,
        help=f"Last air year to fetch (default: {config.TV_DISCOVER_END_YEAR}).",
    )
    parser.add_argument(
        "--pages-per-year",
        type=int,
        default=config.TV_DISCOVER_PAGES_PER_YEAR,
        help=f"Pages per year, 20 results each (default: {config.TV_DISCOVER_PAGES_PER_YEAR}).",
    )
    parser.add_argument(
        "--min-votes",
        type=int,
        default=config.TV_DISCOVER_MIN_VOTES,
        help=f"Minimum TMDB vote count (default: {config.TV_DISCOVER_MIN_VOTES}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_discover_tv")
    args = _parse_args()
    ingest_discover_tv(
        ingestion_date=args.date,
        start_year=args.start_year,
        end_year=args.end_year,
        pages_per_year=args.pages_per_year,
        min_votes=args.min_votes,
    )
