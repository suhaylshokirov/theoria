"""Bronze ingestion: TMDB country names in each translated language.

`dim_country.name` is filled from the movie payload's
`production_countries[].name`, which is always English. TMDB's
`configuration/countries?language=xx` returns the same ~250 ISO countries with
`native_name` in that language — measured live 2026-09-24 the names are genuinely
translated for both Russian ("Соединенные Штаты") and Uzbek ("Qoʻshma Shtatlar"),
unlike genre names, which TMDB leaves null for Uzbek.

Two calls per run (one per language), one file each. The list is ~250 entries
and rarely changes, but it is fetched every run like the genre list so the
partition is self-contained and Silver never has to look back through history.

S3 layout:
    bronze/countries/ingestion_date=YYYY-MM-DD/countries_<lang>.json

The payload is a bare JSON list, written verbatim.

Usage:
    python -m etl.bronze.ingest_countries
    python -m etl.bronze.ingest_countries --date 2026-09-24
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

# Languages the site ships translated country names for (Task 93).
COUNTRY_LANGUAGES = ("ru", "uz")


def ingest_countries(
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
) -> list[str]:
    """Fetch the country list in each of COUNTRY_LANGUAGES and write it to Bronze S3.

    Returns the s3:// URIs written, in COUNTRY_LANGUAGES order.

    Idempotent: re-running with the same `ingestion_date` writes the same keys.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    t0 = time.monotonic()
    logger.info("Starting country ingestion for date=%s", ingestion_date)

    uris: list[str] = []
    for lang in COUNTRY_LANGUAGES:
        payload = client.get_countries(language=lang)
        key = s3_utils.build_path(
            "bronze", "countries", ingestion_date, f"countries_{lang}.json"
        )
        uris.append(s3_utils.write_json(config.S3_BUCKET, key, payload))
        logger.info("Fetched %d countries in %s", len(payload), lang)

    logger.info(
        "Country ingestion complete: %d file(s) written in %.2fs",
        len(uris), time.monotonic() - t0,
    )
    return uris


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest TMDB country names to Bronze S3.")
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_countries")
    args = _parse_args()
    ingest_countries(ingestion_date=args.date)
