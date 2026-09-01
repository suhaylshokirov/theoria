"""Bronze ingestion: full details per person.

Fetches the TMDB person-detail endpoint (`GET /person/{id}`) for every
supplied person_id and writes each response as a separate raw JSON file to
the Bronze layer on S3. Mirrors ingest_companies.py: one file per id, written
as it completes so a mid-run failure never loses progress, returns
(succeeded_ids, failed_ids).

The cast/crew member objects inside a movie's credits payload — all that
`dim_person` is built from today — carry only id/name/gender/popularity/
profile_path/known_for_department. The biographical fields (`biography`,
`birthday`, `deathday`, `place_of_birth`, `homepage`, `imdb_id`,
`also_known_as`) live only on a person's own detail endpoint, which this
project has never called until now.

**Deliberate exception to the "fetch every entity fresh every partition"
norm** that ingest_movie_details.py follows — the same exception
ingest_companies.py makes: a person's birthday / place of birth / bio
essentially never change, unlike a film's vote_count. So `ingest_people()`
skips any person_id that already has a JSON file under `bronze/person_details/`
in *any* prior ingestion_date partition, and only calls the API for the
genuinely new ones. Bronze stays append-only.

**New here (companies had no cap): `max_new`.** `dim_person` holds ~35,782
people with a photo — far more than one nightly job's ~90-minute budget can
fetch at TMDB's ~4.35 req/s. Callers pass person_ids *already in priority
order* (billed cast + directors/writers first — see run_pipeline's
_extract_person_ids); `max_new` truncates the still-to-fetch list so a large
backfill self-completes over several nights instead of blowing the budget in
one. The initial backfill is run by hand with a high cap.

S3 layout:
    bronze/person_details/ingestion_date=YYYY-MM-DD/<person_id>.json

Usage:
    python -m etl.bronze.ingest_people --person-ids 31 287 1245
    python -m etl.bronze.ingest_people --date 2026-06-22 --person-ids 31 --max-new 100
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

# Default per-call ceiling on how many *new* people to fetch. High enough that
# the hand-run initial backfill (~35.8k) needs an explicit override, low enough
# that a nightly run can't overrun its budget once steady state is reached
# (where it fetches ~0 anyway).
_DEFAULT_MAX_NEW = 5000


def _already_enriched_ids(bucket: str) -> set[int]:
    """Return every person_id that already has a Bronze detail file, any date.

    Lists the whole `bronze/person_details/` prefix (across every
    ingestion_date partition) and pulls the `<person_id>.json` basename off
    each key — so the skip in ingest_people() spans all history rather than
    just today's partition.
    """
    client = s3_utils.get_s3_client()
    prefix = "bronze/person_details/"
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
                logger.warning("Skipping non-numeric person_details key: %s", key)
    return enriched


def ingest_people(
    person_ids: list[int],
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
    *,
    max_new: int = _DEFAULT_MAX_NEW,
    skip_existing: bool = True,
) -> tuple[list[int], list[int]]:
    """Fetch detail records for each new person_id and write them to Bronze S3.

    With `skip_existing=True` (the default), any person_id that already has a
    JSON file under `bronze/person_details/` in an earlier partition is left
    alone. The remaining new ids — kept in the order they were passed, which
    the caller has already sorted by fetch priority — are truncated to
    `max_new` before any API call is made.

    Each person is written before the next is fetched so a mid-run failure
    never discards already-completed work. Returns (succeeded_ids, failed_ids);
    person_ids skipped as already-enriched or trimmed past `max_new` appear in
    neither list.

    Idempotent: re-running with the same person_id and ingestion_date writes
    the same key with the same content.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    requested = list(dict.fromkeys(person_ids))  # de-dup, keep caller's order
    if skip_existing:
        enriched = _already_enriched_ids(config.S3_BUCKET)
        new_ids = [pid for pid in requested if pid not in enriched]
    else:
        enriched = set()
        new_ids = list(requested)

    already = len(requested) - len(new_ids)
    to_fetch = new_ids[:max_new]
    deferred = len(new_ids) - len(to_fetch)

    t0 = time.monotonic()
    logger.info(
        "Starting person-details ingestion: %d requested, %d already enriched, "
        "%d to fetch (cap=%d), %d deferred to a later run, date=%s",
        len(requested), already, len(to_fetch), max_new, deferred, ingestion_date,
    )

    succeeded: list[int] = []
    failed: list[int] = []

    for person_id in to_fetch:
        try:
            payload = client.get_person_details(person_id)

            key = s3_utils.build_path(
                "bronze", "person_details", ingestion_date, f"{person_id}.json"
            )
            s3_utils.write_json(config.S3_BUCKET, key, payload)

            succeeded.append(person_id)
            logger.debug("person_id=%d written to Bronze", person_id)

        except Exception as exc:
            failed.append(person_id)
            logger.error("person_id=%d failed, skipping: %s", person_id, exc)

    elapsed = time.monotonic() - t0
    logger.info(
        "Person-details ingestion complete: %d written, %d failed, %d skipped "
        "(already enriched), %d deferred in %.2fs",
        len(succeeded), len(failed), already, deferred, elapsed,
    )
    if failed:
        logger.warning("Failed person_ids: %s", failed)

    return succeeded, failed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest TMDB person details to Bronze S3."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--person-ids",
        type=int,
        nargs="+",
        required=True,
        metavar="ID",
        help="One or more TMDB person IDs to fetch, in fetch-priority order.",
    )
    parser.add_argument(
        "--max-new",
        type=int,
        default=_DEFAULT_MAX_NEW,
        help=f"Cap on how many new people to fetch this run (default: {_DEFAULT_MAX_NEW}).",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Re-fetch every person_id even if a prior partition already has it.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_people")
    args = _parse_args()
    ingest_people(
        person_ids=args.person_ids,
        ingestion_date=args.date,
        max_new=args.max_new,
        skip_existing=not args.no_skip_existing,
    )
