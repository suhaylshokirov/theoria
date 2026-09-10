"""Bronze ingestion: IMDb's public bulk episode-mapping dataset.

The project's **second bulk-file Bronze source**, after
``ingest_imdb_ratings`` (Task 66) — and, like it, a single daily snapshot
covering IMDb's entire catalogue rather than one file per entity, so one
file per partition is the faithful Bronze representation, not a shortcut.

``https://datasets.imdbws.com/title.episode.tsv.gz`` is four tab-separated
columns — ``tconst`` (the episode's own IMDb id), ``parentTconst`` (its
series' IMDb id), ``seasonNumber``, ``episodeNumber``. 53 MB gzipped,
9,871,461 rows (measured 2026-09-08), refreshed daily, no auth / key /
quota.

**Why the bulk file, and not TMDB.** TMDB exposes an episode's IMDb id only
through ``GET /tv/{id}/season/{n}/episode/{m}/external_ids`` — one call per
episode, ~75,000 calls to cover the catalogue at full corpus. This one HTTP
GET replaces every one of them: the join happens downstream in Silver
(``transform_imdb_ratings``), which resolves
``series imdb_id -> parentTconst`` + season/episode number -> the episode's
own ``tconst``, then ``tconst`` -> ``title.ratings`` for its IMDb rating.
Zero per-episode API calls, no new secret, no new dependency — the same
posture Task 66 established for film and series ratings. Recorded here so
the comparison is never re-researched.

Uses ``requests`` directly rather than ``TMDBClient`` (no API key to
inject, no TMDB base URL). ``_fetch_with_retry`` is imported from
``ingest_imdb_ratings`` — the retry posture (429 + 5xx, exponential
backoff, honouring Retry-After) is byte-identical and not worth a second
copy that can drift.

The gzip bytes are written to Bronze **verbatim** via
``s3_utils.write_bytes`` — never decompressed or re-encoded here — so the
stored artefact is byte-identical to what IMDb served.

S3 layout:
    bronze/imdb_episodes/ingestion_date=YYYY-MM-DD/title.episode.tsv.gz

Idempotent: re-running for the same date re-downloads and overwrites the
same key. IMDb's snapshot changes daily, so a same-day re-run may not be
byte-identical to an earlier run that day — a property of the upstream
source, not a bug in this module.

Usage:
    python -m etl.bronze.ingest_imdb_episodes
    python -m etl.bronze.ingest_imdb_episodes --date 2026-06-22
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import config
from etl import s3_utils
from etl.bronze.ingest_imdb_ratings import _fetch_with_retry

logger = logging.getLogger(__name__)


def ingest_imdb_episodes(ingestion_date: dt.date | None = None) -> str:
    """Download IMDb's public episode-mapping snapshot and write it verbatim to Bronze.

    Returns the s3:// URI written.

    Idempotent: re-running with the same ingestion_date re-fetches and
    overwrites the same key.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()

    t0 = time.monotonic()
    logger.info(
        "Starting IMDb episodes ingestion: date=%s, url=%s",
        ingestion_date, config.IMDB_EPISODES_URL,
    )

    data = _fetch_with_retry(config.IMDB_EPISODES_URL)

    key = s3_utils.build_path(
        "bronze", "imdb_episodes", ingestion_date, "title.episode.tsv.gz"
    )
    uri = s3_utils.write_bytes(config.S3_BUCKET, key, data)

    elapsed = time.monotonic() - t0
    logger.info(
        "IMDb episodes ingestion complete: %d bytes written to %s in %.2fs",
        len(data), uri, elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest IMDb's public bulk episode-mapping dataset to Bronze S3."
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_imdb_episodes")
    args = _parse_args()
    ingest_imdb_episodes(ingestion_date=args.date)
