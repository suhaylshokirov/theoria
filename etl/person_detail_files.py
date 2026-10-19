"""The newest Bronze person-detail file per person, and one shared read of them.

`bronze/person_details/` is spread across every ingestion_date: `ingest_people()`
writes a person into whichever partition first discovered them, and
`etl.bronze.backfill_person_translations` re-fetches a person into a *newer*
partition (Bronze is immutable, so the old file stays). The Silver sweeps only
ever want the newest file per person, and the S3 listing alone says which one
that is — the date is in the key — so the superseded copies are never
downloaded. Without this, every re-fetch added one more file to read every
night, forever (95k files on 2026-10-11, 138k a week into the backfill).

`read_latest_person_details()` downloads that set once. The nightly passes the
result to both `transform_people_details` and `transform_people_translations`,
which used to download the same files separately.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

from etl import s3_utils

logger = logging.getLogger(__name__)

PREFIX = "bronze/person_details/"

_KEY_RE = re.compile(
    r"bronze/person_details/ingestion_date=(\d{4}-\d{2}-\d{2})/(\d+)\.json$"
)


def latest_person_detail_files(bucket: str) -> dict[int, tuple[dt.date, str]]:
    """{person_id: (ingestion_date, key)} of each person's newest detail file."""
    client = s3_utils.get_s3_client()
    latest: dict[int, tuple[dt.date, str]] = {}
    listed = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            match = _KEY_RE.search(obj["Key"])
            if match is None:
                continue
            listed += 1
            day = dt.date.fromisoformat(match.group(1))
            person_id = int(match.group(2))
            if person_id not in latest or day > latest[person_id][0]:
                latest[person_id] = (day, obj["Key"])
    logger.info(
        "Listed %d Bronze person-detail file(s): %d people, %d superseded file(s) skipped",
        listed, len(latest), listed - len(latest),
    )
    return latest


def read_latest_person_details(bucket: str) -> list[tuple[str, Any, Exception | None]]:
    """Download and parse each person's newest detail file.

    Same `(key, parsed_or_None, error_or_None)` tuples as
    `s3_utils.read_json_objects`, in key order (oldest partition first).
    """
    keys = sorted(key for _, key in latest_person_detail_files(bucket).values())
    return s3_utils.read_json_objects(bucket, keys)
