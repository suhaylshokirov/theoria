"""Bronze ingestion: every episode of every season, per series.

Reads the `bronze/series_details/<series_id>.json` files written earlier this
run, pulls the `seasons[]` stub off each (which lists every season number at
**no extra call**), and fetches `GET /tv/{id}/season/{n}` — one call returns
the whole season, every episode with its own `runtime`, `air_date`,
`vote_average`, `vote_count`, `still_path`, `episode_type` and
`production_code`.

**Bounded cost — the Task 72 `max_new` pattern.** Episodes are the expensive
half of TV: 733 series x ~4.8 seasons is ~3,500 season calls for a full sweep.
So:

  * a **newly seen** series (no `bronze/seasons/<id>/` in any prior partition
    and not in the warehouse's `dim_series`) counts against `max_new`; the
    overflow is deferred and fills over subsequent nights, cast in
    fetch-priority order by the caller;
  * a series **already ingested** is re-fetched only when its
    `number_of_episodes` differs from `known_episode_counts[series_id]` (the
    warehouse value the caller passes in) — so a finished show is fetched
    once, ever, and a running show refreshes the night it actually airs
    something. These re-fetches do **not** count against `max_new`: they are
    already in the corpus and keeping them current is cheap and bounded by how
    often TV actually airs.

`season_number == 0` ("Specials") is skipped unless
`include_specials=True` — specials distort every per-season chart and are not
what a reader means by "season 1".

Per-episode `crew` and `guest_stars` ride in the payload but are **not**
extracted here or downstream in this task — a guest-star table is a second new
grain and this feature is already large. Follow-up, not silent scope creep.

Bronze stays append-only and immutable: each season is written to its own key
as it completes, so a mid-run failure never discards finished work.

S3 layout:
    bronze/seasons/ingestion_date=YYYY-MM-DD/<series_id>/season_<n>.json

Usage:
    python -m etl.bronze.ingest_seasons --series-ids 1396 1399
    python -m etl.bronze.ingest_seasons --date 2026-09-10 --series-ids 1396 --max-new 50
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

_DEFAULT_MAX_NEW = config.TV_SEASONS_MAX_NEW


def _already_ingested_series(bucket: str) -> set[int]:
    """Every series_id that already has at least one season file, any partition.

    Lists the whole `bronze/seasons/` prefix across every ingestion_date and
    pulls the `<series_id>` path segment off each key — so the "newly seen"
    test spans all history, not just today.
    """
    client = s3_utils.get_s3_client()
    prefix = "bronze/seasons/"
    seen: set[int] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            # bronze/seasons/ingestion_date=YYYY-MM-DD/<series_id>/season_<n>.json
            parts = key.split("/")
            if len(parts) < 5:
                continue
            try:
                seen.add(int(parts[3]))
            except ValueError:
                logger.warning("Skipping non-numeric seasons key: %s", key)
    return seen


def _read_series_stubs(
    series_ids: list[int], ingestion_date: dt.date, bucket: str
) -> dict[int, dict]:
    """Return {series_id: {"number_of_episodes": int|None, "season_numbers": [int]}}.

    Read straight from the `bronze/series_details` files just written this run —
    the `seasons[]` stub lists every season number, and `number_of_episodes` is
    the change signal for a re-fetch. A series whose file is unreadable is
    dropped with a warning.
    """
    keys = [
        s3_utils.build_path("bronze", "series_details", ingestion_date, f"{sid}.json")
        for sid in series_ids
    ]
    stubs: dict[int, dict] = {}
    for key, raw, err in s3_utils.read_json_objects(bucket, keys):
        if err is not None or not raw:
            logger.warning("Could not read %s for season discovery: %s", key, err)
            continue
        sid = raw.get("id")
        if sid is None:
            continue
        season_numbers = sorted(
            {
                s.get("season_number")
                for s in raw.get("seasons") or []
                if s.get("season_number") is not None
            }
        )
        stubs[int(sid)] = {
            "number_of_episodes": raw.get("number_of_episodes"),
            "season_numbers": season_numbers,
        }
    return stubs


def ingest_seasons(
    series_ids: list[int],
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
    *,
    known_episode_counts: dict[int, int] | None = None,
    max_new: int = _DEFAULT_MAX_NEW,
    include_specials: bool | None = None,
    skip_existing: bool = True,
) -> tuple[list[int], list[int]]:
    """Fetch every season of the series that need it and write them to Bronze.

    `series_ids` are in fetch-priority order (the caller sorts them). A series
    is fetched if it is newly seen (subject to `max_new`) or its episode count
    has moved from `known_episode_counts`. Returns (succeeded_series_ids,
    failed_series_ids) — a series with even one failed season call is "failed";
    series skipped as unchanged or deferred past `max_new` appear in neither.

    Idempotent: re-running for the same date writes the same keys.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()
    if include_specials is None:
        include_specials = config.TV_SEASONS_INCLUDE_SPECIALS
    known_episode_counts = known_episode_counts or {}

    bucket = config.S3_BUCKET
    requested = list(dict.fromkeys(series_ids))  # de-dup, keep caller's order
    stubs = _read_series_stubs(requested, ingestion_date, bucket)
    already = _already_ingested_series(bucket) if skip_existing else set()

    to_fetch: list[int] = []
    n_skipped_unchanged = 0
    n_new_seen = 0
    for sid in requested:
        stub = stubs.get(sid)
        if stub is None:
            continue  # unreadable series file, already warned
        is_known = sid in already or sid in known_episode_counts
        if is_known:
            current = stub["number_of_episodes"]
            prior = known_episode_counts.get(sid)
            if current is not None and prior is not None and current == prior:
                n_skipped_unchanged += 1
                continue
            to_fetch.append(sid)  # count moved or unknown — refresh, no cap
        else:
            n_new_seen += 1
            to_fetch.append(sid)

    # Cap only the newly seen series; keep the refreshes.
    new_in_order = [s for s in to_fetch if not (s in already or s in known_episode_counts)]
    capped_new = set(new_in_order[:max_new])
    deferred = [s for s in new_in_order[max_new:]]
    to_fetch = [
        s for s in to_fetch
        if (s in already or s in known_episode_counts) or s in capped_new
    ]

    t0 = time.monotonic()
    logger.info(
        "Starting seasons ingestion: %d series requested, %d newly seen "
        "(cap=%d, %d deferred), %d skipped unchanged, %d to fetch, date=%s",
        len(requested), n_new_seen, max_new, len(deferred),
        n_skipped_unchanged, len(to_fetch), ingestion_date,
    )

    succeeded: list[int] = []
    failed: list[int] = []
    n_calls = 0

    for sid in to_fetch:
        season_numbers = [
            n for n in stubs[sid]["season_numbers"]
            if include_specials or n != 0
        ]
        series_ok = True
        for season_number in season_numbers:
            try:
                payload = client.get_season_details(sid, season_number)
                n_calls += 1
                key = s3_utils.build_path(
                    "bronze", "seasons", ingestion_date,
                    f"{sid}/season_{season_number}.json",
                )
                s3_utils.write_json(bucket, key, payload)
            except Exception as exc:
                series_ok = False
                logger.error(
                    "series_id=%d season=%d failed, skipping: %s",
                    sid, season_number, exc,
                )
        (succeeded if series_ok else failed).append(sid)

    elapsed = time.monotonic() - t0
    logger.info(
        "Seasons ingestion complete: %d series written (%d season calls), "
        "%d series failed, %d skipped unchanged, %d deferred by cap in %.2fs",
        len(succeeded), n_calls, len(failed), n_skipped_unchanged,
        len(deferred), elapsed,
    )
    if failed:
        logger.warning("Failed series_ids: %s", failed)

    return succeeded, failed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest TMDB season/episode details to Bronze S3."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--series-ids",
        type=int,
        nargs="+",
        required=True,
        metavar="ID",
        help="One or more TMDB series IDs to fetch, in fetch-priority order.",
    )
    parser.add_argument(
        "--max-new",
        type=int,
        default=_DEFAULT_MAX_NEW,
        help=f"Cap on how many newly seen series to fetch this run (default: {_DEFAULT_MAX_NEW}).",
    )
    parser.add_argument(
        "--include-specials",
        action="store_true",
        help="Also fetch season 0 (Specials); off by default.",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Treat every series as newly seen even if a prior partition has it.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_seasons")
    args = _parse_args()
    ingest_seasons(
        series_ids=args.series_ids,
        ingestion_date=args.date,
        max_new=args.max_new,
        include_specials=args.include_specials or None,
        skip_existing=not args.no_skip_existing,
    )
