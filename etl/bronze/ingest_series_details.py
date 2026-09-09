"""Bronze ingestion: full details per TV series.

The TV counterpart of `ingest_movie_details.py`. Fetches the TMDB tv-detail
endpoint for every supplied series_id and writes each response as a separate
raw JSON file to the Bronze layer on S3.

One TMDB call per series. `append_to_response=aggregate_credits,external_ids,
videos` folds three sub-resources into that one payload (verified live to
return all three): the series-level cast/crew roll-up, the `imdb_id` join key,
and the trailer/clip metadata.

`aggregate_credits` stays **inline** in this JSON — it is not split onto its
own Bronze entity. This follows the Task 73 `videos` precedent, not the movie
`credits` one: `ingest_credits.py` splits credits only to stay compatible with
a `bronze/credits` entity that predates `append_to_response`. There is no
pre-existing `bronze/tv_credits` entity here, so the nested block simply stays
where TMDB puts it and the Silver transform (Task 78) reads it straight out of
`bronze/series_details`.

Each series is written individually as it completes — a failure on one series
never loses details already written. Failures are logged with the specific
series_id so failed IDs can be retried without re-fetching the whole catalogue.

S3 layout:
    bronze/series_details/ingestion_date=YYYY-MM-DD/<series_id>.json

Usage:
    python -m etl.bronze.ingest_series_details --series-ids 1396 1399 60625
    python -m etl.bronze.ingest_series_details --date 2026-09-09 --series-ids 1396
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

# One call per series covers details + cast/crew + imdb_id + videos.
_APPEND_TO_RESPONSE = "aggregate_credits,external_ids,videos"


def ingest_series_details(
    series_ids: list[int],
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
) -> tuple[list[int], list[int]]:
    """Fetch full detail records for each series_id and write them to Bronze S3.

    Each series is written before the next is fetched so a mid-run failure
    never discards already-completed work. Failures are logged with the
    specific series_id so callers can retry only the failed subset.

    Returns (succeeded_ids, failed_ids).

    Idempotent: re-running with the same series_id and ingestion_date writes
    the same key with the same content.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    t0 = time.monotonic()
    total = len(series_ids)
    logger.info(
        "Starting series-details ingestion: %d series, date=%s", total, ingestion_date
    )

    succeeded: list[int] = []
    failed: list[int] = []

    for series_id in series_ids:
        try:
            payload = client.get_series_details(
                series_id, append_to_response=_APPEND_TO_RESPONSE
            )

            key = s3_utils.build_path(
                "bronze", "series_details", ingestion_date, f"{series_id}.json"
            )
            s3_utils.write_json(config.S3_BUCKET, key, payload)

            succeeded.append(series_id)
            logger.debug("series_id=%d written to Bronze", series_id)

        except Exception as exc:
            failed.append(series_id)
            logger.error("series_id=%d failed, skipping: %s", series_id, exc)

    elapsed = time.monotonic() - t0
    logger.info(
        "Series-details ingestion complete: %d written, %d failed out of %d in %.2fs",
        len(succeeded), len(failed), total, elapsed,
    )
    if failed:
        logger.warning("Failed series_ids: %s", failed)

    return succeeded, failed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest TMDB TV series details to Bronze S3."
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
        help="One or more TMDB series IDs to fetch.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("ingest_series_details")
    args = _parse_args()
    ingest_series_details(series_ids=args.series_ids, ingestion_date=args.date)
