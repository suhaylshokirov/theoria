"""Publish the warehouse's "data version" so the site's cache rolls over with it.

Every cache key the Django site builds embeds the value stored at
`theoria:data_version` (django_app/core/datacache.py). The pipeline writes a new
value after each committed warehouse load, which makes every key built from the
old data unreachable -- nothing is ever deleted by hand.

The value is `<ingestion_date>.<unix seconds>`, not the date alone: the nightly
refresh (03:12) and the Monday discovery run (04:20) stamp the same
ingestion_date and both change the warehouse, so a date-only version would not
roll over after the second load.

Two contracts with the site, both pinned by tests/test_data_version.py:
  * the key is exactly `theoria:data_version` (core.cache_backend.make_cache_key);
  * the value is a pickle, because Django's Redis cache unpickles everything but
    plain integers. A raw string would read as garbage there, and the site would
    quietly stay in short-TTL mode. This module deliberately does not import
    Django -- the pipeline never needs it -- so it pickles the value itself.

Publishing never fails a run: the cache is an optimisation, and a Redis problem
must not turn a successful 40-minute load red.

Usage:
    python -m etl.data_version           # show the published version
    python -m etl.data_version --bump    # publish a new one (today's date)
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import pickle
import time

import config

logger = logging.getLogger(__name__)

VERSION_KEY = "theoria:data_version"
# Connect and read timeouts. The runner is not in Redis's region, so this is
# generous next to the site's 0.3 s -- but a hung Redis must still not stall
# the end of a run.
SOCKET_TIMEOUT = 5


def make_version(ingestion_date: dt.date, now: float | None = None) -> str:
    """`2026-10-07.1760000000` -- the date plus the publish time in unix seconds."""
    return f"{ingestion_date.isoformat()}.{int(time.time() if now is None else now)}"


def _client():
    import redis

    return redis.Redis.from_url(
        config.REDIS_URL,
        socket_connect_timeout=SOCKET_TIMEOUT,
        socket_timeout=SOCKET_TIMEOUT,
    )


def publish_data_version(ingestion_date: dt.date) -> str | None:
    """Write a fresh data version. Returns it, or None if nothing was published."""
    if not config.REDIS_URL:
        logger.info("Data version not published (no REDIS_URL)")
        return None

    version = make_version(ingestion_date)
    try:
        # No expiry: a stale marker only costs freshness, and a missing one
        # would drop every key to the 15-minute fallback.
        _client().set(VERSION_KEY, pickle.dumps(version, pickle.HIGHEST_PROTOCOL))
    except Exception as exc:  # noqa: BLE001 - nothing here may fail a run
        logger.warning(
            "Data version %s not published: %s: %s", version, type(exc).__name__, exc
        )
        return None

    logger.info("Published data version %s to %s", version, VERSION_KEY)
    return version


def read_data_version() -> str | None:
    """The currently published version, or None (unset, no REDIS_URL, or unreachable)."""
    if not config.REDIS_URL:
        return None
    try:
        raw = _client().get(VERSION_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read the data version: %s: %s", type(exc).__name__, exc)
        return None
    return pickle.loads(raw) if raw else None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show, or with --bump publish, the cache data version."
    )
    parser.add_argument(
        "--bump",
        action="store_true",
        help="Publish a new version dated today (after a hand-edit to the "
        "warehouse, or to restore the marker after a Redis flush).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging

    setup_logging("data_version")
    args = _parse_args()
    if args.bump:
        publish_data_version(dt.date.today())
    else:
        logger.info("Published data version: %s", read_data_version())
