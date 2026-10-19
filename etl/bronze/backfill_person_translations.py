"""Bronze backfill: re-fetch people enriched before translations were stored.

Task 93 started asking TMDB for `append_to_response=translations` on every *new*
person; Task 134 added the Russian name to what is read from that block. Anyone
enriched before 2026-09-24 has a `bronze/person_details/` file with no
`translations` key, and `ingest_people()` never looks at them again (it skips
every id already enriched). Bronze is immutable, so the only way to give those
people a Russian name and biography is to fetch them again into a newer
partition — the cumulative Silver sweep (`transform_people_translations`) takes
the latest file per person.

**Finding the people without opening a single file.** A person written on or
after the cutover carries the translations block by construction, so "needs a
backfill" is "the newest partition holding this person predates the cutover" —
answerable from the S3 listing alone. A person re-fetched by this module moves
to a post-cutover partition, drops off the list, and a rerun picks up where the
last one stopped; running it twice in a row fetches nothing the second time.

**Order.** Most popular first, so a capped run spends its calls on the people a
reader is most likely to open. Popularity is read from `dim_person`; a person
the warehouse does not know sorts last, by id.

**Cost.** One TMDB call per person (6,000 took 42 min one at a time; with
`config.TMDB_FETCH_WORKERS` in parallel, a few minutes),
no new dependency and no schema change. `--max-new` bounds a run the same way
`ingest_people()` bounds the nightly. The next nightly load turns the new Bronze
files into rows (`transform_people_translations` → `load_person_translation`).

**Runs nightly.** `scripts/run_refresh.py` calls this after `ingest_people()`
with `config.PERSON_TRANSLATION_BACKFILL_MAX_NEW` (6000; 0 disables), so the
~89k backlog (measured 2026-10-13) fills in about 15 nights with no manual step, and the call becomes a
cheap S3 listing once nobody is left. The same batch is in the same night's
Silver and load, so a re-fetched person's Russian name is live that morning.

Usage:
    python -m etl.bronze.backfill_person_translations --dry-run
    python -m etl.bronze.backfill_person_translations --max-new 5000
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging

from sqlalchemy import text

import config
from etl.bronze.ingest_people import ingest_people
from etl.person_detail_files import latest_person_detail_files

logger = logging.getLogger(__name__)

# First day on which every fetched person carries a translations block. Task 93
# landed on 2026-09-24; the day after is the first partition that cannot have
# been written by the old code. Re-fetching someone already done is harmless.
DEFAULT_CUTOVER = dt.date(2026, 9, 25)

_DEFAULT_MAX_NEW = 5000


def stale_person_ids(bucket: str, before: dt.date = DEFAULT_CUTOVER) -> set[int]:
    """Person ids whose newest Bronze file predates `before` (no translations)."""
    return {
        person_id
        for person_id, (day, _key) in latest_person_detail_files(bucket).items()
        if day < before
    }


def _by_popularity(person_ids: set[int]) -> list[int]:
    """`person_ids` most popular first; unknown people last, then by id."""
    from warehouse.db import get_session

    with get_session() as session:
        rows = session.execute(
            text("SELECT person_id, popularity FROM dim_person WHERE person_id = ANY(:ids)"),
            {"ids": list(person_ids)},
        ).all()
    popularity = {pid: float(pop) for pid, pop in rows if pop is not None}
    return sorted(person_ids, key=lambda pid: (-popularity.get(pid, -1.0), pid))


def backfill_person_translations(
    before: dt.date = DEFAULT_CUTOVER,
    ingestion_date: dt.date | None = None,
    *,
    max_new: int = _DEFAULT_MAX_NEW,
    dry_run: bool = False,
    client=None,
) -> tuple[list[int], list[int]]:
    """Re-fetch up to `max_new` people enriched before `before`.

    Returns (succeeded_ids, failed_ids) like `ingest_people()`; a dry run
    returns two empty lists after logging how many people are owed a fetch.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()

    stale = stale_person_ids(config.S3_BUCKET, before)
    logger.info(
        "Person-translation backfill: %d people have no translations block "
        "(newest file before %s); this run fetches at most %d",
        len(stale), before, max_new,
    )
    if dry_run or not stale:
        return [], []

    ordered = _by_popularity(stale)
    return ingest_people(
        ordered,
        ingestion_date=ingestion_date,
        client=client,
        max_new=max_new,
        skip_existing=False,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-fetch people whose Bronze file has no translations block."
    )
    parser.add_argument("--date", type=dt.date.fromisoformat, default=None,
                        help="Ingestion date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--before", type=dt.date.fromisoformat, default=DEFAULT_CUTOVER,
                        help=f"Re-fetch people whose newest file predates this date "
                             f"(default: {DEFAULT_CUTOVER}).")
    parser.add_argument("--max-new", type=int, default=_DEFAULT_MAX_NEW,
                        help=f"Cap on people fetched this run (default: {_DEFAULT_MAX_NEW}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count the people owed a fetch and stop.")
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("backfill_person_translations")
    args = _parse_args()
    backfill_person_translations(
        before=args.before,
        ingestion_date=args.date,
        max_new=args.max_new,
        dry_run=args.dry_run,
    )
