"""Bronze ingestion: full details per production company.

Fetches the TMDB company-detail endpoint (`GET /company/{id}`) for every
supplied company_id and writes each response as a separate raw JSON file to
the Bronze layer on S3. Mirrors ingest_movie_details.py exactly: one file per
id, written as it completes so a mid-run failure never loses progress,
returns (succeeded_ids, failed_ids).

The `production_companies` stub inside a movie's detail payload — all that
`dim_company` is built from today — carries only id/name/logo_path/
origin_country. The richer fields (`description`, `headquarters`, `homepage`,
`parent_company`) live only on a company's own detail endpoint, which this
project has never called until now.

**Deliberate exception to the "fetch every entity fresh every partition"
norm** that ingest_movie_details.py follows: a studio's description /
headquarters / homepage / parent essentially never change, unlike a film's
vote_count. So `ingest_companies()` skips any company_id that already has a
JSON file under `bronze/company_details/` in *any* prior ingestion_date
partition, and only calls the API for the genuinely new ones. Bronze stays
append-only — an already-enriched company keeps its existing file in its
existing partition; it just isn't re-fetched into today's.

S3 layout:
    bronze/company_details/ingestion_date=YYYY-MM-DD/<company_id>.json

Usage:
    python -m etl.bronze.ingest_companies --company-ids 174 2 420
    python -m etl.bronze.ingest_companies --date 2026-06-22 --company-ids 174
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


def _already_enriched_ids(bucket: str) -> set[int]:
    """Return every company_id that already has a Bronze detail file, any date.

    Lists the whole `bronze/company_details/` prefix (across every
    ingestion_date partition) and pulls the `<company_id>.json` basename off
    each key. This is what makes the skip in ingest_companies() span all
    history rather than just today's partition.
    """
    client = s3_utils.get_s3_client()
    prefix = "bronze/company_details/"
    enriched: set[int] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            basename = key.rsplit("/", 1)[-1].removesuffix(".json")
            try:
                enriched.add(int(basename))
            except ValueError:
                logger.warning("Skipping non-numeric company_details key: %s", key)
    return enriched


def ingest_companies(
    company_ids: list[int],
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
    *,
    skip_existing: bool = True,
) -> tuple[list[int], list[int]]:
    """Fetch detail records for each new company_id and write them to Bronze S3.

    With `skip_existing=True` (the default), any company_id that already has a
    JSON file under `bronze/company_details/` in an earlier partition is left
    alone — see the module docstring for why studios are treated differently
    from films here.

    Each company is written before the next is fetched so a mid-run failure
    never discards already-completed work. Returns (succeeded_ids, failed_ids);
    company_ids skipped as already-enriched appear in neither list.

    Idempotent: re-running with the same company_id and ingestion_date writes
    the same key with the same content.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    requested = list(dict.fromkeys(company_ids))  # de-dup, keep order
    if skip_existing:
        enriched = _already_enriched_ids(config.S3_BUCKET)
        to_fetch = [cid for cid in requested if cid not in enriched]
        skipped = len(requested) - len(to_fetch)
    else:
        to_fetch = requested
        skipped = 0

    t0 = time.monotonic()
    logger.info(
        "Starting company-details ingestion: %d requested, %d already enriched, "
        "%d to fetch, date=%s",
        len(requested), skipped, len(to_fetch), ingestion_date,
    )

    succeeded: list[int] = []
    failed: list[int] = []

    for company_id in to_fetch:
        try:
            payload = client.get_company_details(company_id)

            key = s3_utils.build_path(
                "bronze", "company_details", ingestion_date, f"{company_id}.json"
            )
            s3_utils.write_json(config.S3_BUCKET, key, payload)

            succeeded.append(company_id)
            logger.debug("company_id=%d written to Bronze", company_id)

        except Exception as exc:
            failed.append(company_id)
            logger.error("company_id=%d failed, skipping: %s", company_id, exc)

    elapsed = time.monotonic() - t0
    logger.info(
        "Company-details ingestion complete: %d written, %d failed, %d skipped "
        "(already enriched) in %.2fs",
        len(succeeded), len(failed), skipped, elapsed,
    )
    if failed:
        logger.warning("Failed company_ids: %s", failed)

    return succeeded, failed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest TMDB company details to Bronze S3."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--company-ids",
        type=int,
        nargs="+",
        required=True,
        metavar="ID",
        help="One or more TMDB company IDs to fetch.",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Re-fetch every company_id even if a prior partition already has it.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_companies")
    args = _parse_args()
    ingest_companies(
        company_ids=args.company_ids,
        ingestion_date=args.date,
        skip_existing=not args.no_skip_existing,
    )
