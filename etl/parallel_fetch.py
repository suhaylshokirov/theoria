"""Run a per-id Bronze fetch (one TMDB GET, then its S3 write) on a thread pool.

Every Bronze ingest that walks a list of ids used to do it one id at a time:
fetch from TMDB, write to S3, then start the next. Nothing in that loop is
rate-limited — the time is round-trip waiting (the runner is US-hosted, the
bucket is in eu-central-1), ~0.4-0.75 s per id, so the 2026-10-19 nightly spent
16 min on 1,250 films and 42 min on 6,000 people. Running
`config.TMDB_FETCH_WORKERS` ids at once overlaps that waiting.

The contract of the loops it replaces is kept: each id is written as soon as it
is fetched, so a mid-run failure never discards completed work; one id failing
is logged and skipped, never raised; and (succeeded, failed) come back in the
order the ids were passed, so a caller's priority order and a run's logs stay
deterministic whatever order the threads finish in.

Throughput stays well under TMDB's ~50 req/s per-IP ceiling (8 workers at
~0.5 s per id is ~16 req/s); a 429 is still retried, honouring Retry-After, by
`TMDBClient.get()`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

import config

logger = logging.getLogger(__name__)


def fetch_each(
    ids: Iterable[int],
    fetch_one: Callable[[int], object],
    *,
    label: str,
    max_workers: int | None = None,
) -> tuple[list[int], list[int]]:
    """Call `fetch_one(id)` for every id, `max_workers` at a time.

    `fetch_one` does the whole unit of work for one id (fetch + write) and
    raises on failure. `label` names the id in log lines ("movie_id=550").
    `max_workers` defaults to `config.TMDB_FETCH_WORKERS`; 1 runs inline.

    Returns (succeeded_ids, failed_ids), each in the order of `ids`.
    """
    ids = list(ids)
    workers = config.TMDB_FETCH_WORKERS if max_workers is None else max_workers

    def _run(item_id: int) -> bool:
        try:
            fetch_one(item_id)
        except Exception as exc:  # noqa: BLE001 — reported per id, not raised
            logger.error("%s=%d failed, skipping: %s", label, item_id, exc)
            return False
        logger.debug("%s=%d written to Bronze", label, item_id)
        return True

    if workers <= 1 or len(ids) <= 1:
        outcomes = [_run(item_id) for item_id in ids]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(ids))) as pool:
            outcomes = list(pool.map(_run, ids))

    succeeded = [item_id for item_id, ok in zip(ids, outcomes) if ok]
    failed = [item_id for item_id, ok in zip(ids, outcomes) if not ok]
    return succeeded, failed
