"""Silver transform: trailers and clips carried inside the series-detail payload.

The TV counterpart of `transform_movie_videos.py`. `GET /tv/{id}` has carried a
nested `"videos": {"id", "results": [...]}` block since Task 77 first wrote
this entity — `append_to_response=aggregate_credits,external_ids,videos`, all
three requested from day one. So unlike the movie side, there is no
"partition written before the feature existed" gap to degrade around: every
`bronze/series_details` payload has a `videos` key. The defensive path below
is kept anyway, at zero extra cost, for the same reason `transform_movie_links`
guards a missing nested array rather than assuming TMDB always sends one.

Modelled directly on `transform_movie_videos.py`: one pass over
`bronze/series_details` for the date, extracting the nested array into its
own denormalised long table at the same 11-plus-parent-id shape.

S3 source:  bronze/series_details/ingestion_date=YYYY-MM-DD/<series_id>.json
S3 output:  silver/series_videos/ingestion_date=YYYY-MM-DD/series_videos.parquet

series_videos columns — identical to movie_videos, series_id in place of
movie_id:
    series_id     Int64    — TMDB series ID
    video_id      string   — TMDB's own 24-char hex id for the video row
    name          string
    key           string   — the site-specific id (YouTube/Vimeo watch id)
    site          string   — "YouTube" or "Vimeo"
    type          string   — Trailer / Teaser / Clip / Featurette /
                             Behind the Scenes / Bloopers
    official      boolean   — whether TMDB flags it as an official upload
    size          Int64    — video resolution (2160/1080/720/480/360), not a
                             duration — TMDB publishes no duration
    iso_639_1     string   — language code, nullable
    iso_3166_1    string   — country code, nullable
    published_at  string   — ISO-8601 timestamp, kept verbatim; the warehouse
                             DDL casts it to TIMESTAMPTZ (Task 90)

Dedup key: (series_id, video_id) — its true grain, per Task 40 not widened
"to be safe".

Rows with a null series_id or video_id are dropped with a warning, never
silently — same convention as every other Silver transform.

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_series_videos
    python -m etl.silver.transform_series_videos --date 2026-09-09
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from typing import Any

import pandas as pd

import config
from etl import s3_utils

logger = logging.getLogger(__name__)

_COLUMNS = [
    "series_id", "video_id", "name", "key", "site", "type", "official",
    "size", "iso_639_1", "iso_3166_1", "published_at",
]


def _list_bronze_keys(bucket: str, ingestion_date: dt.date) -> list[str]:
    """Return every .json key under the bronze/series_details partition for this date."""
    prefix = s3_utils.build_path("bronze", "series_details", ingestion_date, "")
    client = s3_utils.get_s3_client()
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def _extract_video_rows(raw: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract one row per video from a TMDB series-detail payload.

    Returns ``None`` when the payload has no ``videos`` key at all. Unobserved
    in practice — every `bronze/series_details` partition has carried one
    since Task 77 — but kept distinct from a genuinely video-less show
    (``videos.results`` present and empty, which returns ``[]``) for the same
    reason `transform_movie_videos._extract_video_rows()` draws that line.
    """
    videos = raw.get("videos")
    if not isinstance(videos, dict):
        return None

    series_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for video in videos.get("results") or []:
        rows.append({
            "series_id": series_id,
            "video_id": video.get("id"),
            "name": video.get("name"),
            "key": video.get("key"),
            "site": video.get("site"),
            "type": video.get("type"),
            "official": video.get("official"),
            "size": video.get("size"),
            "iso_639_1": video.get("iso_639_1"),
            "iso_3166_1": video.get("iso_3166_1"),
            "published_at": video.get("published_at"),
        })
    return rows


def _cast_video_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    df["video_id"] = df["video_id"].astype("string")
    df["name"] = df["name"].astype("string")
    df["key"] = df["key"].astype("string")
    df["site"] = df["site"].astype("string")
    df["type"] = df["type"].astype("string")
    df["official"] = df["official"].astype("boolean")
    df["size"] = pd.to_numeric(df["size"], errors="coerce").astype("Int64")
    df["iso_639_1"] = df["iso_639_1"].astype("string")
    df["iso_3166_1"] = df["iso_3166_1"].astype("string")
    df["published_at"] = df["published_at"].astype("string")
    return df


def transform_series_videos(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Bronze series-detail JSON -> extract the videos array -> write Silver.

    Returns the s3:// URI of the series_videos Parquet.

    Raises FileNotFoundError if no Bronze series-detail files exist for the date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver series-videos transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze series-detail files found for ingestion_date={ingestion_date}"
        )
    logger.info("Found %d Bronze JSON file(s) to process", len(keys))

    video_rows: list[dict[str, Any]] = []
    missing_videos_key = 0
    errors = 0

    for key, raw, read_err in s3_utils.read_json_objects(bucket, keys):
        try:
            if read_err is not None:
                raise read_err
            rows = _extract_video_rows(raw)
            if rows is None:
                missing_videos_key += 1
            else:
                video_rows.extend(rows)
        except Exception as exc:
            errors += 1
            logger.error("Failed to read/extract %s: %s", key, exc)

    if not video_rows and errors == len(keys):
        raise RuntimeError(
            f"Every Bronze file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    if missing_videos_key:
        logger.warning(
            "%d of %d Bronze payload(s) had no `videos` key — unexpected for a "
            "post-Task-77 partition; those shows contribute no video rows this "
            "partition.",
            missing_videos_key, len(keys),
        )

    # columns= keeps an all-empty partition (every show videoless) producing a
    # well-formed empty Parquet rather than a column-less frame the cast
    # would trip over.
    df = pd.DataFrame(video_rows, columns=_COLUMNS)
    df = _cast_video_types(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["series_id", "video_id"], keep="last")
    dupes = before_dedup - len(df)
    if dupes:
        logger.info("[series_videos] Dropped %d duplicate row(s) on (series_id, video_id)", dupes)

    for col in ("series_id", "video_id"):
        n_null = int(df[col].isna().sum())
        if n_null:
            logger.warning("[series_videos] Dropping %d row(s) with null %s", n_null, col)
    df = df.dropna(subset=["series_id", "video_id"])

    output_key = s3_utils.build_path(
        "silver", "series_videos", ingestion_date, "series_videos.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver series-videos transform complete: %d video row(s) across %d show(s), "
        "%d payload(s) without a videos key, %d parse error(s) in %.2fs",
        len(df), df["series_id"].nunique(), missing_videos_key, errors, elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze series-detail JSON to Silver series-videos Parquet."
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
    setup_logging("transform_series_videos")
    args = _parse_args()
    transform_series_videos(ingestion_date=args.date)
