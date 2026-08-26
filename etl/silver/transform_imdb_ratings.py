"""Silver transform: IMDb bulk ratings, resolved to Theoria movie_ids.

Reads the gzipped `title.ratings.tsv.gz` Bronze snapshot for a given
ingestion_date, plus that same partition's `silver/movies/movies.parquet`
(already written by transform_movies.py), and writes one row per film in
this partition that IMDb has published a rating for.

**This is the first Silver transform that joins two Silver inputs** rather
than one Bronze source. It deliberately does *not* join against `dim_movie`
in the warehouse — Silver reading the warehouse would be a layer inversion,
and it would also make this transform's output depend on load order rather
than only on immutable upstream data. Resolving `imdb_id -> movie_id` here,
not in the loader, keeps `load_fact_movie_rating()`'s job identical to every
other loader: resolve already-integer FKs against the live dimensions, then
upsert.

The raw file is a *global* daily snapshot (~1.6M titles, overwhelmingly TV
episodes rather than films) with no partition of its own — every Theoria
ingestion_date sees the same upstream content (whichever Bronze copy that
date wrote). Filtering it down to the ~1,200 films in *this* partition's own
movies.parquet is what turns a 99.9%-irrelevant global file into a small,
partition-scoped table; shipping the whole file to Silver untouched would be
almost entirely waste.

A film drops out of the join, and is never an error, when:
    - it has no `imdb_id` at all (2 of 1,215 films, per Task 55), or
    - its `imdb_id` has no row in IMDb's file — IMDb only publishes a title
      once it clears a minimum vote threshold, so a very low-vote film is
      real sparsity, not a data quality problem.
Both counts are logged, never silently swallowed.

S3 sources:
    bronze/imdb_ratings/ingestion_date=YYYY-MM-DD/title.ratings.tsv.gz
    silver/movies/ingestion_date=YYYY-MM-DD/movies.parquet
S3 output:
    silver/imdb_ratings/ingestion_date=YYYY-MM-DD/imdb_ratings.parquet

Output columns:
    movie_id    Int64   — Theoria/TMDB movie id
    imdb_id     string  — kept for traceability, not just used as a join key
    rating      float   — IMDb's averageRating, 1.0-10.0
    vote_count  Int64   — IMDb's numVotes

Idempotent: running twice for the same date overwrites the same key with
the same content (modulo IMDb's own snapshot changing between runs, which is
a property of the upstream source, not of this transform).

Usage:
    python -m etl.silver.transform_imdb_ratings
    python -m etl.silver.transform_imdb_ratings --date 2026-06-22
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


def _read_bronze_ratings_bytes(bucket: str, ingestion_date: dt.date) -> bytes:
    """Download the raw gzipped ratings file from Bronze."""
    key = s3_utils.build_path(
        "bronze", "imdb_ratings", ingestion_date, "title.ratings.tsv.gz"
    )
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def _read_silver_movies(bucket: str, ingestion_date: dt.date) -> pd.DataFrame:
    """Download this partition's already-written silver/movies/movies.parquet."""
    key = s3_utils.build_path("silver", "movies", ingestion_date, "movies.parquet")
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return pd.read_parquet(io.BytesIO(response["Body"].read()))


def _parse_ratings_tsv(raw_bytes: bytes) -> pd.DataFrame:
    """Parse IMDb's tab-separated, gzip-compressed ratings export.

    IMDb documents `\\N` as its null marker; passed defensively even though
    all three columns are 100% populated in practice (measured live).
    """
    df = pd.read_csv(
        io.BytesIO(raw_bytes),
        sep="\t",
        compression="gzip",
        na_values=["\\N"],
    )
    df = df.rename(columns={
        "tconst": "imdb_id",
        "averageRating": "rating",
        "numVotes": "vote_count",
    })
    return df[["imdb_id", "rating", "vote_count"]]


def transform_imdb_ratings(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> str:
    """Read Bronze IMDb ratings + this partition's Silver movies -> resolve -> write Silver.

    Returns the s3:// URI of the written Parquet file.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver IMDb-ratings transform for date=%s", ingestion_date)

    raw_bytes = _read_bronze_ratings_bytes(bucket, ingestion_date)
    ratings_df = _parse_ratings_tsv(raw_bytes)
    logger.info("Parsed %d row(s) from the IMDb ratings snapshot", len(ratings_df))

    movies_df = _read_silver_movies(bucket, ingestion_date)
    movies_with_imdb_id = movies_df[["movie_id", "imdb_id"]].dropna(subset=["imdb_id"])
    n_no_imdb_id = len(movies_df) - len(movies_with_imdb_id)
    if n_no_imdb_id:
        logger.info(
            "%d film(s) in this partition have no imdb_id — real sparsity, excluded",
            n_no_imdb_id,
        )

    merged = movies_with_imdb_id.merge(ratings_df, on="imdb_id", how="inner")
    n_unmatched = len(movies_with_imdb_id) - len(merged)
    if n_unmatched:
        logger.info(
            "%d film(s) with an imdb_id have no matching IMDb rating row (below "
            "IMDb's publication vote floor) — real sparsity, excluded",
            n_unmatched,
        )

    merged["movie_id"] = pd.to_numeric(merged["movie_id"], errors="coerce").astype("Int64")
    merged["imdb_id"] = merged["imdb_id"].astype("string")
    merged["rating"] = pd.to_numeric(merged["rating"], errors="coerce")
    merged["vote_count"] = pd.to_numeric(merged["vote_count"], errors="coerce").astype("Int64")
    merged = merged[["movie_id", "imdb_id", "rating", "vote_count"]]

    output_key = s3_utils.build_path(
        "silver", "imdb_ratings", ingestion_date, "imdb_ratings.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, merged)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver IMDb-ratings transform complete: %d of %d film(s) with an imdb_id "
        "matched in %.2fs",
        len(merged), len(movies_with_imdb_id), elapsed,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze IMDb ratings to Silver Parquet, resolved against this partition's movies."
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
    setup_logging("transform_imdb_ratings")
    args = _parse_args()
    transform_imdb_ratings(ingestion_date=args.date)
