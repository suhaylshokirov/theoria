"""Bronze ingestion: paginated popular-movies catalogue.

Fetches the TMDB "popular movies" endpoint page by page and writes each page
as a separate raw JSON file to the Bronze layer on S3. Pages are written as
they complete — a failure on page N never loses pages 1..N-1 already written.

S3 layout:
    bronze/movies/ingestion_date=YYYY-MM-DD/page_NNNN.json

Returns the full list of discovered movie_ids so downstream scripts
(ingest_movie_details, ingest_credits) know what to fetch next.

Usage:
    python -m etl.bronze.ingest_movies
    python -m etl.bronze.ingest_movies --date 2026-06-22 --max-pages 10
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


def ingest_movies(
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
    max_pages: int = config.MAX_PAGES,
) -> list[int]:
    """Fetch popular movies page by page and write each page to Bronze S3.

    Returns all movie_ids discovered across every successfully written page.
    Pages already written to S3 are never lost on partial failure — each page
    is flushed to S3 before the next one is fetched.

    Idempotent: re-running on the same date overwrites the same page keys
    with the same content (same popularity ranking → same pages for that date).
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    t0 = time.monotonic()
    logger.info(
        "Starting movie ingestion: up to %d page(s), date=%s", max_pages, ingestion_date
    )

    movie_ids: list[int] = []
    pages_written = 0
    pages_failed = 0

    for page in range(1, max_pages + 1):
        try:
            payload = client.get_popular_movies(page=page)
            results = payload.get("results", [])

            key = s3_utils.build_path(
                "bronze", "movies", ingestion_date, f"page_{page:04d}.json"
            )
            s3_utils.write_json(config.S3_BUCKET, key, payload)

            ids = [movie["id"] for movie in results if "id" in movie]
            movie_ids.extend(ids)
            pages_written += 1
            logger.info(
                "Page %d/%d: %d movies written (running total: %d)",
                page, max_pages, len(results), len(movie_ids),
            )

        except Exception as exc:
            pages_failed += 1
            logger.error("Page %d/%d failed, skipping: %s", page, max_pages, exc)

    elapsed = time.monotonic() - t0
    logger.info(
        "Movie ingestion complete: %d page(s) written, %d failed, "
        "%d total movie_ids collected in %.2fs",
        pages_written, pages_failed, len(movie_ids), elapsed,
    )
    return movie_ids


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest TMDB popular movies to Bronze S3.")
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=config.MAX_PAGES,
        help=f"Number of pages to fetch (default: {config.MAX_PAGES}).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_args()
    ingest_movies(ingestion_date=args.date, max_pages=args.max_pages)
