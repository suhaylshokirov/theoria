"""Silver transform: seasons and episodes.

Two Parquets from two Bronze sources:

  * ``silver/seasons/seasons.parquet`` — grain ``(series_id, season_number)`` —
    built from the ``seasons[]`` stub that already rides every
    ``bronze/series_details`` payload (no season call needed to produce it).
  * ``silver/episodes/episodes.parquet`` — grain ``episode_id`` (TMDB's own
    global episode id) — built from the ``bronze/seasons/<series_id>/season_<n>.json``
    files that ``ingest_seasons`` writes.

``series_id`` for an episode row is taken from the **Bronze key path**
(``bronze/seasons/ingestion_date=.../<series_id>/season_<n>.json``), not from
the payload body — the path is the authoritative link and is always present.

Per-episode ``crew`` and ``guest_stars`` are in the season payload but are
deliberately **not** extracted (Task 82 step 5): a guest-star table is a
second new grain and a follow-up, not part of this task.

S3 sources:
    bronze/series_details/ingestion_date=YYYY-MM-DD/<series_id>.json
    bronze/seasons/ingestion_date=YYYY-MM-DD/<series_id>/season_<n>.json
S3 output:
    silver/seasons/ingestion_date=YYYY-MM-DD/seasons.parquet
    silver/episodes/ingestion_date=YYYY-MM-DD/episodes.parquet

seasons.parquet columns (grain: series_id, season_number):
    series_id, season_number, season_id, name, air_date, episode_count,
    overview, poster_path

episodes.parquet columns (grain: episode_id):
    episode_id, series_id, season_number, episode_number, name, air_date,
    runtime, overview, still_path, episode_type, production_code,
    vote_average, vote_count

Rows with a null grain key are dropped with a warning, never silently.

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_episodes
    python -m etl.silver.transform_episodes --date 2026-09-10
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

_SEASON_COLUMNS = [
    "series_id", "season_number", "season_id", "name", "air_date",
    "episode_count", "overview", "poster_path",
]
_EPISODE_COLUMNS = [
    "episode_id", "series_id", "season_number", "episode_number", "name",
    "air_date", "runtime", "overview", "still_path", "episode_type",
    "production_code", "vote_average", "vote_count",
]


def _list_keys(bucket: str, entity: str, ingestion_date: dt.date) -> list[str]:
    """Every .json key under the given Bronze partition (recursively)."""
    prefix = s3_utils.build_path("bronze", entity, ingestion_date, "")
    client = s3_utils.get_s3_client()
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def _series_id_from_key(key: str) -> int | None:
    """Pull <series_id> out of bronze/seasons/ingestion_date=.../<series_id>/season_<n>.json."""
    parts = key.split("/")
    if len(parts) < 5:
        return None
    try:
        return int(parts[3])
    except ValueError:
        return None


def _extract_season_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    series_id = raw.get("id")
    rows: list[dict[str, Any]] = []
    for season in raw.get("seasons") or []:
        rows.append({
            "series_id": series_id,
            "season_number": season.get("season_number"),
            "season_id": season.get("id"),
            "name": season.get("name"),
            "air_date": season.get("air_date") or None,
            "episode_count": season.get("episode_count"),
            "overview": season.get("overview") or None,
            "poster_path": season.get("poster_path") or None,
        })
    return rows


def _extract_episode_rows(raw: dict[str, Any], series_id: int | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ep in raw.get("episodes") or []:
        rows.append({
            "episode_id": ep.get("id"),
            "series_id": series_id,
            "season_number": ep.get("season_number"),
            "episode_number": ep.get("episode_number"),
            "name": ep.get("name"),
            "air_date": ep.get("air_date") or None,
            "runtime": ep.get("runtime"),
            "overview": ep.get("overview") or None,
            "still_path": ep.get("still_path") or None,
            "episode_type": ep.get("episode_type") or None,
            "production_code": ep.get("production_code") or None,
            "vote_average": ep.get("vote_average"),
            "vote_count": ep.get("vote_count"),
        })
    return rows


def _cast_seasons(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["series_id"] = pd.to_numeric(df["series_id"], errors="coerce").astype("Int64")
    df["season_number"] = pd.to_numeric(df["season_number"], errors="coerce").astype("Int64")
    df["season_id"] = pd.to_numeric(df["season_id"], errors="coerce").astype("Int64")
    df["episode_count"] = pd.to_numeric(df["episode_count"], errors="coerce").astype("Int64")
    df["air_date"] = pd.to_datetime(df["air_date"], errors="coerce").dt.date
    for col in ("name", "overview", "poster_path"):
        df[col] = df[col].astype("string")
    return df


def _cast_episodes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("episode_id", "series_id", "season_number", "episode_number", "runtime", "vote_count"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["vote_average"] = pd.to_numeric(df["vote_average"], errors="coerce")
    df["air_date"] = pd.to_datetime(df["air_date"], errors="coerce").dt.date
    for col in ("name", "overview", "still_path", "episode_type", "production_code"):
        df[col] = df[col].astype("string")
    return df


def _finalise(
    rows: list[dict[str, Any]], columns: list[str], cast_fn, grain: list[str],
    bucket: str, entity: str, filename: str, ingestion_date: dt.date,
) -> str:
    df = pd.DataFrame(rows, columns=columns)
    df = cast_fn(df)

    before = len(df)
    df = df.drop_duplicates(subset=grain, keep="last")
    if before - len(df):
        logger.info("[%s] Dropped %d duplicate row(s) on %s", entity, before - len(df), grain)

    n_null = df[grain].isna().any(axis=1).sum()
    if n_null:
        logger.warning("[%s] Dropping %d row(s) with a null grain key %s", entity, int(n_null), grain)
        df = df.dropna(subset=grain)

    df = df[columns]
    output_key = s3_utils.build_path("silver", entity, ingestion_date, filename)
    return s3_utils.write_parquet(bucket, output_key, df)


def transform_episodes(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> tuple[str, str]:
    """Read Bronze series-detail stubs + season files → write two Silver Parquets.

    Returns ``(seasons_uri, episodes_uri)``.

    Raises FileNotFoundError if no Bronze season files exist for the date —
    the seasons stub alone is not enough, and a run that produced no episodes
    is a real error worth surfacing, not an empty file.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver episodes transform for date=%s", ingestion_date)

    # --- seasons: from the series-detail stubs ---
    series_keys = _list_keys(bucket, "series_details", ingestion_date)
    if not series_keys:
        raise FileNotFoundError(
            f"No Bronze series-detail files for ingestion_date={ingestion_date}"
        )
    season_rows: list[dict[str, Any]] = []
    series_errors = 0
    for key, raw, err in s3_utils.read_json_objects(bucket, series_keys):
        if err is not None or not raw:
            series_errors += 1
            logger.error("Failed to read %s: %s", key, err)
            continue
        season_rows.extend(_extract_season_rows(raw))

    # --- episodes: from the season files ---
    season_keys = _list_keys(bucket, "seasons", ingestion_date)
    if not season_keys:
        raise FileNotFoundError(
            f"No Bronze season files for ingestion_date={ingestion_date}"
        )
    episode_rows: list[dict[str, Any]] = []
    season_file_errors = 0
    for key, raw, err in s3_utils.read_json_objects(bucket, season_keys):
        if err is not None or not raw:
            season_file_errors += 1
            logger.error("Failed to read %s: %s", key, err)
            continue
        episode_rows.extend(_extract_episode_rows(raw, _series_id_from_key(key)))

    if season_file_errors == len(season_keys):
        raise RuntimeError(
            f"Every Bronze season file failed to parse for ingestion_date={ingestion_date}"
        )

    seasons_uri = _finalise(
        season_rows, _SEASON_COLUMNS, _cast_seasons, ["series_id", "season_number"],
        bucket, "seasons", "seasons.parquet", ingestion_date,
    )
    episodes_uri = _finalise(
        episode_rows, _EPISODE_COLUMNS, _cast_episodes, ["episode_id"],
        bucket, "episodes", "episodes.parquet", ingestion_date,
    )

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver episodes transform complete: %d season row(s) from %d series file(s) "
        "(%d errors), %d episode row(s) from %d season file(s) (%d errors) in %.2fs",
        len(season_rows), len(series_keys), series_errors,
        len(episode_rows), len(season_keys), season_file_errors, elapsed,
    )
    return seasons_uri, episodes_uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze seasons/episodes JSON to Silver Parquet."
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
    setup_logging("transform_episodes")
    args = _parse_args()
    transform_episodes(ingestion_date=args.date)
