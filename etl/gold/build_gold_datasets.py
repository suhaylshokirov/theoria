"""Gold layer: pre-aggregated analytical datasets.

Reads the five Silver Parquet files for a given ingestion_date and produces
four Gold datasets, each answering a specific analytical question:

    1. genre_metrics     — avg rating, total revenue, movie count per genre
    2. decade_stats      — movie count, avg rating, total revenue per decade
    3. actor_filmography — number of films and avg rating per actor
    4. director_ratings  — avg rating, film count, and total revenue per director

All four datasets are written to the Gold layer in S3 as Parquet files. The
transform is idempotent: running twice for the same date overwrites the same
keys with the same content.

S3 sources (Silver layer for the given date):
    silver/movies/ingestion_date=.../movies.parquet
    silver/actors/ingestion_date=.../actors.parquet
    silver/directors/ingestion_date=.../directors.parquet
    silver/genres/ingestion_date=.../genres.parquet
    silver/credits_bridge/ingestion_date=.../credits_bridge.parquet

S3 outputs (Gold layer):
    gold/genre_metrics/ingestion_date=.../genre_metrics.parquet
    gold/decade_stats/ingestion_date=.../decade_stats.parquet
    gold/actor_filmography/ingestion_date=.../actor_filmography.parquet
    gold/director_ratings/ingestion_date=.../director_ratings.parquet

Usage:
    python -m etl.gold.build_gold_datasets
    python -m etl.gold.build_gold_datasets --date 2026-06-22
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import logging
import time

import pandas as pd

import config
from etl import s3_utils

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# S3 read helpers
# ---------------------------------------------------------------------------

def _read_silver_parquet(bucket: str, entity: str, ingestion_date: dt.date) -> pd.DataFrame:
    """Download and deserialise one Silver Parquet file into a DataFrame.

    Raises FileNotFoundError if the key does not exist in S3.
    """
    filename_map = {
        "movies": "movies.parquet",
        "actors": "actors.parquet",
        "directors": "directors.parquet",
        "genres": "genres.parquet",
        "credits_bridge": "credits_bridge.parquet",
    }
    filename = filename_map[entity]
    key = s3_utils.build_path("silver", entity, ingestion_date, filename)
    client = s3_utils.get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.NoSuchKey:
        raise FileNotFoundError(f"Silver file not found: s3://{bucket}/{key}")
    except Exception as exc:
        # Catch botocore ClientError for NoSuchKey (raised as generic exc in mocks)
        raise FileNotFoundError(f"Silver file not found: s3://{bucket}/{key}") from exc
    buf = io.BytesIO(response["Body"].read())
    df = pd.read_parquet(buf, engine="pyarrow")
    logger.info("Read Silver %s: %d rows from s3://%s/%s", entity, len(df), bucket, key)
    return df


# ---------------------------------------------------------------------------
# Aggregation functions — one per Gold dataset
# ---------------------------------------------------------------------------

def _build_genre_metrics(movies: pd.DataFrame, genres: pd.DataFrame) -> pd.DataFrame:
    """Compute avg rating, total revenue, and movie count per genre.

    movies.genre_ids is a list column; we explode it so each movie
    appears once per genre, then join genre names from the genres table.
    """
    exploded = movies[["movie_id", "vote_average", "revenue", "genre_ids"]].copy()
    exploded = exploded.explode("genre_ids").rename(columns={"genre_ids": "genre_id"})
    exploded = exploded.dropna(subset=["genre_id"])
    exploded["genre_id"] = pd.to_numeric(exploded["genre_id"], errors="coerce").astype("Int64")

    merged = exploded.merge(genres[["genre_id", "genre_name"]], on="genre_id", how="left")

    agg = (
        merged.groupby(["genre_id", "genre_name"], dropna=False)
        .agg(
            movie_count=("movie_id", "count"),
            avg_rating=("vote_average", "mean"),
            total_revenue=("revenue", "sum"),
        )
        .reset_index()
    )
    agg["avg_rating"] = agg["avg_rating"].round(3)
    agg["genre_id"] = agg["genre_id"].astype("Int64")
    agg["movie_count"] = agg["movie_count"].astype("Int64")
    agg["total_revenue"] = agg["total_revenue"].astype("Int64")
    return agg.sort_values("movie_count", ascending=False).reset_index(drop=True)


def _build_decade_stats(movies: pd.DataFrame) -> pd.DataFrame:
    """Compute movie count, avg rating, and total revenue grouped by release decade.

    Movies with no release_date are excluded (they cannot be placed in a decade).
    """
    df = movies[["movie_id", "release_date", "vote_average", "revenue"]].copy()
    df = df.dropna(subset=["release_date"])

    # release_date may be a Python date object or a string — normalise to year int.
    df["year"] = pd.to_datetime(df["release_date"], errors="coerce").dt.year
    df = df.dropna(subset=["year"])
    df["decade"] = (df["year"] // 10 * 10).astype(int)

    agg = (
        df.groupby("decade")
        .agg(
            movie_count=("movie_id", "count"),
            avg_rating=("vote_average", "mean"),
            total_revenue=("revenue", "sum"),
        )
        .reset_index()
    )
    agg["avg_rating"] = agg["avg_rating"].round(3)
    agg["movie_count"] = agg["movie_count"].astype("Int64")
    agg["total_revenue"] = agg["total_revenue"].astype("Int64")
    return agg.sort_values("decade").reset_index(drop=True)


def _build_actor_filmography(
    movies: pd.DataFrame,
    actors: pd.DataFrame,
    bridge: pd.DataFrame,
) -> pd.DataFrame:
    """Compute film count and avg rating per actor.

    Join: credits_bridge (cast rows only) → movies → actors.
    """
    cast = bridge[bridge["credit_type"] == "cast"][["movie_id", "person_id"]].copy()

    merged = cast.merge(
        movies[["movie_id", "vote_average"]], on="movie_id", how="left"
    ).merge(
        actors[["person_id", "name"]], on="person_id", how="left"
    )

    agg = (
        merged.groupby(["person_id", "name"], dropna=False)
        .agg(
            film_count=("movie_id", "count"),
            avg_rating=("vote_average", "mean"),
        )
        .reset_index()
    )
    agg["avg_rating"] = agg["avg_rating"].round(3)
    agg["person_id"] = agg["person_id"].astype("Int64")
    agg["film_count"] = agg["film_count"].astype("Int64")
    return agg.sort_values("film_count", ascending=False).reset_index(drop=True)


def _build_director_ratings(
    movies: pd.DataFrame,
    directors: pd.DataFrame,
    bridge: pd.DataFrame,
) -> pd.DataFrame:
    """Compute avg rating, film count, and total revenue per director.

    Join: credits_bridge (crew rows only) → movies → directors.
    """
    crew = bridge[bridge["credit_type"] == "crew"][["movie_id", "person_id"]].copy()

    merged = crew.merge(
        movies[["movie_id", "vote_average", "revenue"]], on="movie_id", how="left"
    ).merge(
        directors[["person_id", "name"]], on="person_id", how="left"
    )

    agg = (
        merged.groupby(["person_id", "name"], dropna=False)
        .agg(
            film_count=("movie_id", "count"),
            avg_rating=("vote_average", "mean"),
            total_revenue=("revenue", "sum"),
        )
        .reset_index()
    )
    agg["avg_rating"] = agg["avg_rating"].round(3)
    agg["person_id"] = agg["person_id"].astype("Int64")
    agg["film_count"] = agg["film_count"].astype("Int64")
    agg["total_revenue"] = agg["total_revenue"].astype("Int64")
    return agg.sort_values("avg_rating", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_gold_datasets(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> dict[str, str]:
    """Read all Silver files and write four Gold aggregation datasets.

    Returns a dict mapping dataset name → s3:// URI of the written file.
    Raises FileNotFoundError if any required Silver file is missing.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Gold build for date=%s", ingestion_date)

    movies = _read_silver_parquet(bucket, "movies", ingestion_date)
    actors = _read_silver_parquet(bucket, "actors", ingestion_date)
    directors = _read_silver_parquet(bucket, "directors", ingestion_date)
    genres = _read_silver_parquet(bucket, "genres", ingestion_date)
    bridge = _read_silver_parquet(bucket, "credits_bridge", ingestion_date)

    datasets = {
        "genre_metrics": _build_genre_metrics(movies, genres),
        "decade_stats": _build_decade_stats(movies),
        "actor_filmography": _build_actor_filmography(movies, actors, bridge),
        "director_ratings": _build_director_ratings(movies, directors, bridge),
    }

    uris: dict[str, str] = {}
    for name, df in datasets.items():
        key = s3_utils.build_path("gold", name, ingestion_date, f"{name}.parquet")
        uri = s3_utils.write_parquet(bucket, key, df)
        uris[name] = uri
        logger.info("Gold %s: %d rows → %s", name, len(df), uri)

    elapsed = time.monotonic() - t0
    logger.info(
        "Gold build complete in %.2fs — wrote %d datasets: %s",
        elapsed,
        len(uris),
        ", ".join(uris.keys()),
    )
    return uris


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from etl.logging_config import setup_logging

    setup_logging("build_gold_datasets")

    parser = argparse.ArgumentParser(description="Build Gold aggregation datasets from Silver.")
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=dt.date.today(),
        help="ingestion_date partition to process (YYYY-MM-DD, default: today)",
    )
    args = parser.parse_args()

    uris = build_gold_datasets(ingestion_date=args.date)
    for name, uri in uris.items():
        print(f"{name}: {uri}")
    sys.exit(0)
