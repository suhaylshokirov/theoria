"""Gold layer: pre-aggregated analytical datasets.

Reads the five Silver Parquet files for a given ingestion_date and produces
five Gold datasets, each answering a specific analytical question:

    1. genre_metrics       — avg rating, total revenue, movie count per genre
    2. decade_stats        — movie count, avg rating, total revenue per decade
    3. actor_filmography   — number of films and avg rating per actor
    4. director_ratings    — avg rating, film count, and total revenue per director
    5. collaboration_edges — how often each pair of key collaborators worked together

`collaboration_edges` is the first Gold dataset that is loaded back into the
warehouse (see etl/warehouse_loader/load_gold.py); the other four are still
recomputed live by the Django views and the analytics SQL.

All five datasets are written to the Gold layer in S3 as Parquet files. The
transform is idempotent: running twice for the same date overwrites the same
keys with the same content.

S3 sources (Silver layer for the given date):
    silver/movies/ingestion_date=.../movies.parquet
    silver/people/ingestion_date=.../people.parquet
    silver/genres/ingestion_date=.../genres.parquet
    silver/credits_bridge/ingestion_date=.../credits_bridge.parquet

S3 outputs (Gold layer):
    gold/genre_metrics/ingestion_date=.../genre_metrics.parquet
    gold/decade_stats/ingestion_date=.../decade_stats.parquet
    gold/actor_filmography/ingestion_date=.../actor_filmography.parquet
    gold/director_ratings/ingestion_date=.../director_ratings.parquet
    gold/collaboration_edges/ingestion_date=.../collaboration_edges.parquet

Usage:
    python -m etl.gold.build_gold_datasets
    python -m etl.gold.build_gold_datasets --date 2026-06-22
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import itertools
import logging
import time

import pandas as pd

import config
from etl import s3_utils

logger = logging.getLogger(__name__)

# What counts as a collaboration.
#
# These two values are a definition, not a tuning knob — the same distinction
# config.DISCOVER_* carries for the corpus itself. Widening them doesn't make
# the build slower so much as make `fact_collaboration` describe a different
# relationship. See _build_collaboration_edges() for the measured cost of not
# bounding it at all.
TOP_BILLED_CUTOFF = 10
KEY_CREW_JOBS = frozenset({
    "Director",
    "Screenplay",
    "Writer",
    "Story",
    "Original Music Composer",
    "Director of Photography",
    "Editor",
    "Production Design",
    "Producer",
})


def _key_credits(bridge: pd.DataFrame) -> pd.DataFrame:
    """Return the (movie_id, person_id) credits that count as a collaboration."""
    cast = bridge[
        (bridge["credit_type"] == "cast")
        & bridge["ordering"].notna()
        & (bridge["ordering"] < TOP_BILLED_CUTOFF)
    ]
    crew = bridge[
        (bridge["credit_type"] == "crew") & bridge["role"].isin(KEY_CREW_JOBS)
    ]
    return (
        pd.concat([cast[["movie_id", "person_id"]], crew[["movie_id", "person_id"]]])
        .dropna()
        .drop_duplicates()
    )


# ---------------------------------------------------------------------------
# S3 read helpers
# ---------------------------------------------------------------------------

def _read_silver_parquet(bucket: str, entity: str, ingestion_date: dt.date) -> pd.DataFrame:
    """Download and deserialise one Silver Parquet file into a DataFrame.

    Raises FileNotFoundError if the key does not exist in S3.
    """
    filename_map = {
        "movies": "movies.parquet",
        "people": "people.parquet",
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

    Join: credits_bridge (director credits only) → movies → directors.

    Filters on `role == "Director"` as well as `credit_type == "crew"` to match
    `load_facts._build_crew_rows()`. Without the role filter this counts every
    crew credit (writers, producers, editors), which both inflates film_count
    and produces null-name rows for non-directors after the left join — i.e.
    Gold and the warehouse would disagree on what a director credit is.
    """
    crew = bridge[
        (bridge["credit_type"] == "crew") & (bridge["role"] == "Director")
    ][["movie_id", "person_id"]].copy()

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


def _build_collaboration_edges(movies: pd.DataFrame, bridge: pd.DataFrame) -> pd.DataFrame:
    """Count how often each pair of key collaborators has worked together.

    One row per unordered pair, with `person_a_id < person_b_id` so a pair is
    counted once rather than twice in mirror image.

    **Why only key credits.** Pairing every person credited on a film is
    unusable and, worse, untrue: on this corpus it produces **33.1 million**
    edges and asserts that a caterer and a stunt double "collaborated". Scoping
    to top-billed cast plus the principal craft roles gives **~181,500** edges —
    a 180x reduction that comes from deciding what a collaboration *is*, not
    from a LIMIT. Compare Task 42, where two dashboard queries had no bound at
    all and returned whatever the corpus happened to contain.

    This is the one dataset that genuinely belongs in Gold: expensive to compute
    (a quadratic expansion over every film), cheap to serve, and shaped for a
    read pattern the star schema can't answer without a self-join per query.
    """
    key_credits = _key_credits(bridge)

    years = (
        movies[["movie_id", "release_date"]]
        .assign(year=lambda d: pd.to_datetime(d["release_date"], errors="coerce").dt.year)
        .set_index("movie_id")["year"]
        .to_dict()
    )

    pair_films: dict[tuple[int, int], list[int]] = {}
    for movie_id, group in key_credits.groupby("movie_id"):
        # sorted() is what makes the pair canonical: combinations() over an
        # ascending list can only ever emit (smaller, larger).
        people = sorted({int(p) for p in group["person_id"]})
        year = years.get(movie_id)
        for pair in itertools.combinations(people, 2):
            pair_films.setdefault(pair, []).append(year)

    rows = []
    for (a, b), pair_years in pair_films.items():
        known = [y for y in pair_years if y is not None and not pd.isna(y)]
        rows.append({
            "person_a_id": a,
            "person_b_id": b,
            "films_together": len(pair_years),
            "first_year": min(known) if known else None,
            "last_year": max(known) if known else None,
        })

    df = pd.DataFrame(rows, columns=[
        "person_a_id", "person_b_id", "films_together", "first_year", "last_year",
    ])
    for col in ("person_a_id", "person_b_id", "films_together", "first_year", "last_year"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    logger.info(
        "collaboration_edges: %d pair(s) from %d key credit(s) across %d film(s)",
        len(df), len(key_credits), key_credits["movie_id"].nunique(),
    )
    return df.sort_values(
        ["films_together", "person_a_id"], ascending=[False, True]
    ).reset_index(drop=True)


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
    people = _read_silver_parquet(bucket, "people", ingestion_date)
    genres = _read_silver_parquet(bucket, "genres", ingestion_date)
    bridge = _read_silver_parquet(bucket, "credits_bridge", ingestion_date)

    datasets = {
        "genre_metrics": _build_genre_metrics(movies, genres),
        "decade_stats": _build_decade_stats(movies),
        "actor_filmography": _build_actor_filmography(movies, people, bridge),
        "director_ratings": _build_director_ratings(movies, people, bridge),
        "collaboration_edges": _build_collaboration_edges(movies, bridge),
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
