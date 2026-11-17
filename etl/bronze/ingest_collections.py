"""Bronze ingestion: full details per film collection (franchise), Task 153.

A movie's `belongs_to_collection` stub carries only id/name/poster_path/
backdrop_path. `GET /collection/{id}` is the rest: the original name and
language, an overview, **every part** TMDB lists (a part is a film, in or out of
the catalogue), and — with `append_to_response=translations,images` — the
per-language text and the poster/backdrop gallery.

Three raw responses are written per collection:

    bronze/collection_details/ingestion_date=D/<id>.json
        append_to_response=translations,images. At write time
        etl.translations.trim_translations() cuts the translations block (~23 KB
        untrimmed, ~26 languages) to ru, uz **and en**: the English homepage, the
        only one TMDB publishes for a collection, lives only in the `en` entry.
        The images are kept whole, since the site shows them.
    bronze/collection_localized/ingestion_date=D/ru/<id>.json   (language=ru-RU)
    bronze/collection_localized/ingestion_date=D/uz/<id>.json   (language=uz-UZ)
        The *localised* parts[] — the only source of Russian titles and overviews
        for parts that are not in the catalogue (movie_translation covers
        catalogue films only).

**No skip-existing — a deliberate break from ingest_companies().** A studio's
details are static; a franchise gains a part whenever a sequel is announced, so
every referenced collection is fetched every run. The cost is bounded by the
number of distinct collections (~370 today, 3 calls each), not by catalogue size.

A collection is **all-or-nothing**: the three responses are fetched first and
written together, so a failure never leaves a details file with no localised
sibling. A collection that fails contributes no Bronze files today, and the
loader leaves yesterday's warehouse rows untouched (see load_collections.py).

A **404 means TMDB deleted the collection** (24 of the 392 ids in the
warehouse on 2026-11-16, e.g. "Constantine Collection"). It is logged and
counted as `gone`, never raised, and does not fail the run.

Usage:
    python -m etl.bronze.ingest_collections --collection-ids 10 119
    python -m etl.bronze.ingest_collections --date 2026-11-16     # every id in that day's film partition
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import config
from etl import s3_utils
from etl.parallel_fetch import fetch_each
from etl.tmdb_client import TMDBAPIError, TMDBClient
from etl.translations import TRANSLATION_LANGUAGES, trim_translations

logger = logging.getLogger(__name__)

DETAILS_ENTITY = "collection_details"
LOCALIZED_ENTITY = "collection_localized"
_DETAILS_APPEND = "translations,images"


def _tmdb_locale(lang: str) -> str:
    """`ru` -> `ru-RU`: the `language=` value TMDB expects for a shipped language."""
    return f"{lang}-{lang.upper()}"


def _list_keys(bucket: str, prefix: str) -> list[str]:
    client = s3_utils.get_s3_client()
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".json"))
    return keys


def extract_collection_ids(
    ingestion_date: dt.date, bucket: str | None = None
) -> list[int]:
    """Every distinct `belongs_to_collection.id` in the date's film-detail partition.

    TMDB has no "list collections" endpoint; the ids only exist inside the film
    payloads Bronze already holds. Reading the whole partition (rather than the
    ids a caller just fetched) keeps this correct however the films got there —
    the nightly refresh, the weekly discovery run, or a replay.
    """
    if bucket is None:
        bucket = config.S3_BUCKET
    prefix = s3_utils.build_path("bronze", "movie_details", ingestion_date, "")
    keys = _list_keys(bucket, prefix)
    ids: set[int] = set()
    unreadable = 0
    for key, raw, err in s3_utils.read_json_objects(bucket, keys):
        if err is not None or not raw:
            unreadable += 1
            logger.warning("Could not read %s for collection-id extraction: %s", key, err)
            continue
        cid = (raw.get("belongs_to_collection") or {}).get("id")
        if cid is not None:
            ids.add(int(cid))
    logger.info(
        "Found %d distinct collection id(s) across %d film file(s) (%d unreadable) for %s",
        len(ids), len(keys), unreadable, ingestion_date,
    )
    return sorted(ids)


def ingest_collections(
    collection_ids: list[int],
    ingestion_date: dt.date | None = None,
    client: TMDBClient | None = None,
) -> tuple[list[int], list[int], list[int]]:
    """Fetch details + ru + uz for every collection id and write them to Bronze S3.

    Returns ``(succeeded_ids, failed_ids, gone_ids)``. `gone_ids` are the 404s
    (TMDB deleted the collection): nothing is written for them and they are not
    failures. Every id is written as soon as it is fetched, several at once
    (see etl.parallel_fetch), so a mid-run failure never discards finished work.

    Idempotent: re-running for the same date rewrites the same keys.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if client is None:
        client = TMDBClient()

    requested = list(dict.fromkeys(collection_ids))  # de-dup, keep order
    t0 = time.monotonic()
    logger.info(
        "Starting collection-details ingestion: %d collection(s), date=%s",
        len(requested), ingestion_date,
    )

    gone: list[int] = []  # list.append is atomic, so the worker threads can share it

    def _fetch_one(collection_id: int) -> None:
        try:
            details = client.get_collection_details(
                collection_id, append_to_response=_DETAILS_APPEND
            )
        except TMDBAPIError as exc:
            if exc.status_code == 404:
                logger.warning(
                    "collection_id=%d is gone from TMDB (404); skipping", collection_id
                )
                gone.append(collection_id)
                return
            raise
        localized = {
            lang: client.get_collection_details(
                collection_id, language=_tmdb_locale(lang)
            )
            for lang in TRANSLATION_LANGUAGES
        }

        # Fetched in full first, written together: all three files or none.
        s3_utils.write_json(
            config.S3_BUCKET,
            s3_utils.build_path(
                "bronze", DETAILS_ENTITY, ingestion_date, f"{collection_id}.json"
            ),
            trim_translations(details, keep=(*TRANSLATION_LANGUAGES, "en")),
        )
        for lang, payload in localized.items():
            s3_utils.write_json(
                config.S3_BUCKET,
                s3_utils.build_path(
                    "bronze", LOCALIZED_ENTITY, ingestion_date, f"{lang}/{collection_id}.json"
                ),
                payload,
            )

    ok, failed = fetch_each(requested, _fetch_one, label="collection_id")
    gone_set = set(gone)
    succeeded = [cid for cid in ok if cid not in gone_set]
    gone_ids = [cid for cid in requested if cid in gone_set]

    logger.info(
        "Collection-details ingestion complete: %d written, %d failed, %d gone (404) "
        "in %.2fs",
        len(succeeded), len(failed), len(gone_ids), time.monotonic() - t0,
    )
    if failed:
        logger.warning("Failed collection_ids: %s", failed)
    return succeeded, failed, gone_ids


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest TMDB collection details (+ ru/uz localisations) to Bronze S3."
    )
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--collection-ids", type=int, nargs="+", default=None, metavar="ID",
        help="TMDB collection ids to fetch. Default: every collection referenced by "
             "the date's Bronze film-detail partition.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging

    setup_logging("ingest_collections")
    args = _parse_args()
    date = args.date or dt.date.today()
    ids = args.collection_ids or extract_collection_ids(date)
    ingest_collections(ids, ingestion_date=date)
