"""Silver transform: trailers and clips carried inside the movie-detail payload.

`GET /movie/{id}` gains a nested `"videos": {"id", "results": [...]}` block when
called with `append_to_response=videos` (Task 73) — the same shape as
`production_companies` / `spoken_languages`, an array riding along inside a
payload this project already fetches. So this module is modelled on
`transform_movie_links.py`, not on the credits split: one pass over
`bronze/movie_details` for the date, extracting the nested array into its own
denormalised long table.

`videos` does **not** become its own Bronze entity. `credits` is split out by
`refresh_movies._split_payload()` only because a standalone `bronze/credits/`
entity already existed and both paths must produce the same layout. Videos has
no prior entity, so it stays inline in the movie-detail JSON and is read
straight out of it here, exactly as `transform_movie_links` reads the other
nested arrays.

**Backfill is not possible.** Bronze is immutable, and every `movie_details`
partition written before Task 73 has no `videos` key. Such a payload yields
zero rows plus one aggregate warning, never an exception. A partition where no
file carries the key still produces a valid empty Parquet with the right
columns, so the loader downstream always has something well-formed to read. The
feature lights up on the first partition written after Task 73 lands.

S3 source:  bronze/movie_details/ingestion_date=YYYY-MM-DD/<movie_id>.json
S3 output:  silver/movie_videos/ingestion_date=YYYY-MM-DD/movie_videos.parquet

movie_videos columns:
    movie_id      Int64    — TMDB movie ID
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
                             DDL casts it to TIMESTAMPTZ (Task 74)

Dedup key: (movie_id, video_id) — its true grain, per Task 40 not widened "to
be safe". `key` is unique only within a site (a YouTube id and a Vimeo id share
no namespace); `video_id` is TMDB's stable row identifier.

Rows with a null movie_id or video_id are dropped with a warning, never
silently — same convention as every other Silver transform.

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_movie_videos
    python -m etl.silver.transform_movie_videos --date 2026-09-06
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
    "movie_id", "video_id", "name", "key", "site", "type", "official",
    "size", "iso_639_1", "iso_3166_1", "published_at",
]


def _list_bronze_keys(bucket: str, ingestion_date: dt.date) -> list[str]:
    """Return every .json key under the bronze/movie_details partition for this date."""
    prefix = s3_utils.build_path("bronze", "movie_details", ingestion_date, "")
    client = s3_utils.get_s3_client()
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def _extract_video_rows(raw: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract one row per video from a TMDB movie-detail payload.

    Returns ``None`` when the payload has no ``videos`` key at all — a partition
    written before Task 73 appended it. That is distinct from a payload whose
    ``videos.results`` is an empty list (a film that genuinely has no videos,
    e.g. TMDB id 664413), which returns ``[]``.
    """
    videos = raw.get("videos")
    if not isinstance(videos, dict):
        return None

    movie_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for video in videos.get("results") or []:
        rows.append({
            "movie_id": movie_id,
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
    df["movie_id"] = pd.to_numeric(df["movie_id"], errors="coerce").astype("Int64")
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


def transform_movie_videos(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Bronze movie-detail JSON -> extract the videos array -> write Silver.

    Returns the s3:// URI of the movie_videos Parquet.

    Raises FileNotFoundError if no Bronze movie-detail files exist for the date.
    Raises RuntimeError if every file fails to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver movie-videos transform for date=%s", ingestion_date)

    keys = _list_bronze_keys(bucket, ingestion_date)
    if not keys:
        raise FileNotFoundError(
            f"No Bronze movie-detail files found for ingestion_date={ingestion_date}"
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
            "%d of %d Bronze payload(s) had no `videos` key — written before Task 73 "
            "appended `append_to_response=videos`; those films contribute no video "
            "rows this partition.",
            missing_videos_key, len(keys),
        )

    # columns= keeps an all-empty partition (every payload pre-Task-73, or every
    # film videoless) producing a well-formed empty Parquet rather than a
    # column-less frame the cast would trip over.
    df = pd.DataFrame(video_rows, columns=_COLUMNS)
    df = _cast_video_types(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["movie_id", "video_id"], keep="last")
    dupes = before_dedup - len(df)
    if dupes:
        logger.info("[movie_videos] Dropped %d duplicate row(s) on (movie_id, video_id)", dupes)

    for col in ("movie_id", "video_id"):
        n_null = int(df[col].isna().sum())
        if n_null:
            logger.warning("[movie_videos] Dropping %d row(s) with null %s", n_null, col)
    df = df.dropna(subset=["movie_id", "video_id"])

    output_key = s3_utils.build_path(
        "silver", "movie_videos", ingestion_date, "movie_videos.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver movie-videos transform complete: %d video row(s) across %d film(s), "
        "%d payload(s) without a videos key, %d parse error(s) in %.2fs",
        len(df), df["movie_id"].nunique(), missing_videos_key, errors, elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze movie-detail JSON to Silver movie-videos Parquet."
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
    setup_logging("transform_movie_videos")
    args = _parse_args()
    transform_movie_videos(ingestion_date=args.date)
