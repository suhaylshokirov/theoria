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
    bronze/imdb_episodes/ingestion_date=YYYY-MM-DD/title.episode.tsv.gz  (only with_tv=True — Task 83)
    silver/movies/ingestion_date=YYYY-MM-DD/movies.parquet
    silver/series/ingestion_date=YYYY-MM-DD/series.parquet    (only with_tv=True — Task 81)
    silver/episodes/ingestion_date=YYYY-MM-DD/episodes.parquet  (only with_tv=True — Task 83)
S3 output:
    silver/imdb_ratings/ingestion_date=YYYY-MM-DD/imdb_ratings.parquet
    silver/series_ratings/ingestion_date=YYYY-MM-DD/series_ratings.parquet   (only with_tv=True)
    silver/episode_ratings/ingestion_date=YYYY-MM-DD/episode_ratings.parquet  (only with_tv=True)
    silver/episodes/ingestion_date=YYYY-MM-DD/episodes.parquet  — rewritten with an
        `imdb_id` column backfilled onto it (only with_tv=True — Task 83)

Output columns (imdb_ratings.parquet):
    movie_id    Int64   — Theoria/TMDB movie id
    imdb_id     string  — kept for traceability, not just used as a join key
    rating      float   — IMDb's averageRating, 1.0-10.0
    vote_count  Int64   — IMDb's numVotes

Output columns (series_ratings.parquet — the same shape, keyed by series_id):
    series_id   Int64
    imdb_id     string
    rating      float
    vote_count  Int64

Output columns (episode_ratings.parquet):
    episode_id  Int64   — TMDB's global episode id
    source      string  — 'imdb' or 'tmdb'
    rating      float
    vote_count  Int64

**Task 81** made this the project's first *three-input* Silver transform: the
single IMDb snapshot is joined against both this partition's movies and its
series. **Task 83** adds a *second* IMDb bulk file — `title.episode.tsv.gz`,
which maps `(parentTconst, seasonNumber, episodeNumber)` -> the episode's own
`tconst` — and resolves every catalogued episode to its IMDb id and rating in
two hops: `series imdb_id (== parentTconst)` + season/episode number -> the
episode's `tconst`, then `tconst` -> `title.ratings`. The 9.87M-row episode
file is filtered to this catalogue's parent-series `tconst`s *before* any
per-episode join (~75k rows survive).

Unlike `imdb_ratings.parquet` / `series_ratings.parquet` — which carry only the
IMDb join, with the loader synthesising the `source='tmdb'` rows from
`movies`/`series` at load time — `episode_ratings.parquet` carries **both
sources with a `source` column**. The transform already has to read
`silver/episodes` (for the natural-key -> `episode_id` mapping and the
`imdb_id` backfill), so its TMDB `vote_average` / `vote_count` are already in
hand; emitting both here keeps Task 84's `load_fact_episode_rating()` a plain
read-and-upsert and reads `episodes.parquet` once, not twice.

The `imdb_id` backfill onto `silver/episodes/episodes.parquet` covers every
episode that matched a `tconst`, rated or not — it is a genuine stable
identifier for the episode, carried onto `dim_episode` in Task 84. This
creates an ordering coupling on a `--with-tv` run: `transform_episodes` must
run before this transform (it does in `run_pipeline`), and the `episodes`
Silver DQ config expects the `imdb_id` column this transform adds.

The series and episode joins run only when `with_tv=True`; a movie-only run
(and the nightly refresh, until Task 85) writes none of the TV outputs.

Idempotent: running twice for the same date overwrites the same key with
the same content (modulo IMDb's own snapshot changing between runs, which is
a property of the upstream source, not of this transform).

Usage:
    python -m etl.silver.transform_imdb_ratings
    python -m etl.silver.transform_imdb_ratings --date 2026-06-22
    python -m etl.silver.transform_imdb_ratings --with-tv
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


def _read_silver_series(bucket: str, ingestion_date: dt.date) -> pd.DataFrame:
    """Download this partition's already-written silver/series/series.parquet."""
    key = s3_utils.build_path("silver", "series", ingestion_date, "series.parquet")
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return pd.read_parquet(io.BytesIO(response["Body"].read()))


def _read_silver_episodes(bucket: str, ingestion_date: dt.date) -> pd.DataFrame:
    """Download this partition's already-written silver/episodes/episodes.parquet."""
    key = s3_utils.build_path("silver", "episodes", ingestion_date, "episodes.parquet")
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return pd.read_parquet(io.BytesIO(response["Body"].read()))


def _read_bronze_episodes_bytes(bucket: str, ingestion_date: dt.date) -> bytes:
    """Download the raw gzipped title.episode file from Bronze (Task 83)."""
    key = s3_utils.build_path(
        "bronze", "imdb_episodes", ingestion_date, "title.episode.tsv.gz"
    )
    client = s3_utils.get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


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


def _parse_episode_tsv(raw_bytes: bytes) -> pd.DataFrame:
    """Parse IMDb's tab-separated, gzip-compressed title.episode export (Task 83).

    Four columns: `tconst` (the episode's IMDb id), `parentTconst` (its
    series' IMDb id), `seasonNumber`, `episodeNumber`. `\\N` is IMDb's null
    marker and genuinely appears here — some episodes carry no season or
    episode number. Read with an explicit `usecols` subset: the file is
    ~9.87M rows, and naming the columns also pins the schema if IMDb ever
    adds one. Numbers stay as strings at parse time; the caller coerces them
    after scoping the frame down to this catalogue.
    """
    df = pd.read_csv(
        io.BytesIO(raw_bytes),
        sep="\t",
        compression="gzip",
        usecols=["tconst", "parentTconst", "seasonNumber", "episodeNumber"],
        dtype={
            "tconst": "string", "parentTconst": "string",
            "seasonNumber": "string", "episodeNumber": "string",
        },
        na_values=["\\N"],
    )
    return df[["tconst", "parentTconst", "seasonNumber", "episodeNumber"]]


def _resolve_entity_ratings(
    ratings_df: pd.DataFrame,
    entity_df: pd.DataFrame,
    id_col: str,
    noun: str,
) -> pd.DataFrame:
    """Inner-join the IMDb snapshot onto one entity table on `imdb_id`.

    Shared by the movie and (Task 81) series joins — the only differences are
    the id column name and the log noun. Non-matches are logged in two
    buckets, both real sparsity and neither an error: no `imdb_id` at all, or
    an `imdb_id` with no row in IMDb's file (below its publication vote floor).

    Returns a frame with columns [id_col, imdb_id, rating, vote_count].
    """
    with_imdb_id = entity_df[[id_col, "imdb_id"]].dropna(subset=["imdb_id"])
    n_no_imdb_id = len(entity_df) - len(with_imdb_id)
    if n_no_imdb_id:
        logger.info(
            "%d %s in this partition have no imdb_id — real sparsity, excluded",
            n_no_imdb_id, noun,
        )

    merged = with_imdb_id.merge(ratings_df, on="imdb_id", how="inner")
    n_unmatched = len(with_imdb_id) - len(merged)
    if n_unmatched:
        logger.info(
            "%d %s with an imdb_id have no matching IMDb rating row (below "
            "IMDb's publication vote floor) — real sparsity, excluded",
            n_unmatched, noun,
        )

    merged[id_col] = pd.to_numeric(merged[id_col], errors="coerce").astype("Int64")
    merged["imdb_id"] = merged["imdb_id"].astype("string")
    merged["rating"] = pd.to_numeric(merged["rating"], errors="coerce")
    merged["vote_count"] = pd.to_numeric(merged["vote_count"], errors="coerce").astype("Int64")

    total_with_imdb_id = len(with_imdb_id)
    match_rate = (len(merged) / total_with_imdb_id * 100) if total_with_imdb_id else 0.0
    logger.info(
        "IMDb match rate for %s: %d of %d with an imdb_id (%.1f%%)",
        noun, len(merged), total_with_imdb_id, match_rate,
    )
    return merged[[id_col, "imdb_id", "rating", "vote_count"]]


def _transform_series_ratings(
    ratings_df: pd.DataFrame, bucket: str, ingestion_date: dt.date,
) -> str:
    """Join the already-parsed IMDb snapshot onto this partition's Silver series.

    Called only when transform_imdb_ratings(with_tv=True). Writes
    silver/series_ratings/series_ratings.parquet, the show counterpart of
    imdb_ratings.parquet. Returns its s3:// URI.
    """
    series_df = _read_silver_series(bucket, ingestion_date)
    resolved = _resolve_entity_ratings(ratings_df, series_df, "series_id", "show(s)")
    output_key = s3_utils.build_path(
        "silver", "series_ratings", ingestion_date, "series_ratings.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, resolved)
    logger.info("Wrote %d Silver series_ratings row(s) to %s", len(resolved), uri)
    return uri


def _transform_episode_ratings(
    ratings_df: pd.DataFrame, bucket: str, ingestion_date: dt.date,
) -> str:
    """Resolve every catalogued episode to its IMDb id and rating (Task 83).

    Called only when transform_imdb_ratings(with_tv=True). Two hops:

      1. IMDb's title.episode.tsv.gz maps (parentTconst, seasonNumber,
         episodeNumber) -> the episode's own tconst. Filter it to this
         catalogue's parent-series tconst set *first* (9.87M rows -> ~75k),
         then join onto silver/series (imdb_id == parentTconst) for series_id
         and onto silver/episodes on the natural key for episode_id.
      2. tconst -> the already-parsed title.ratings snapshot for the rating.

    Two side effects, both logged:
      * writes silver/episode_ratings/episode_ratings.parquet at
        (episode_id, source, rating, vote_count) — 'imdb' rows from the join,
        'tmdb' rows from the episodes Parquet's own vote_average / vote_count
        (the dual-source story of Phase 15 and Task 81);
      * rewrites silver/episodes/episodes.parquet with an `imdb_id` column
        backfilled onto it — populated for every episode that matched a
        tconst, rated or not, since the id is a genuine episode identifier
        worth carrying onto dim_episode (Task 84).

    Returns the episode_ratings.parquet s3:// URI.
    """
    series_df = _read_silver_series(bucket, ingestion_date)
    episodes_df = _read_silver_episodes(bucket, ingestion_date)
    episode_map = _parse_episode_tsv(_read_bronze_episodes_bytes(bucket, ingestion_date))
    logger.info(
        "Parsed %d row(s) from the IMDb episode-mapping snapshot", len(episode_map)
    )

    # --- hop 1: (parentTconst, season, episode) -> episode_id, tconst ---
    series_imdb = series_df[["series_id", "imdb_id"]].dropna(subset=["imdb_id"])
    catalogue_parents = set(series_imdb["imdb_id"])
    scoped = episode_map[episode_map["parentTconst"].isin(catalogue_parents)].copy()
    logger.info(
        "IMDb episode rows in scope: %d of %d (parent series in this catalogue)",
        len(scoped), len(episode_map),
    )
    scoped["season_number"] = pd.to_numeric(scoped["seasonNumber"], errors="coerce").astype("Int64")
    scoped["episode_number"] = pd.to_numeric(scoped["episodeNumber"], errors="coerce").astype("Int64")
    scoped = scoped.merge(
        series_imdb, left_on="parentTconst", right_on="imdb_id", how="inner"
    ).drop(columns=["imdb_id"])

    ep_keys = episodes_df[
        ["episode_id", "series_id", "season_number", "episode_number"]
    ].copy()
    linked = ep_keys.merge(
        scoped[["series_id", "season_number", "episode_number", "tconst"]],
        on=["series_id", "season_number", "episode_number"],
        how="inner",
    ).rename(columns={"tconst": "imdb_id"})
    linked = linked.drop_duplicates(subset=["episode_id"])
    linked["imdb_id"] = linked["imdb_id"].astype("string")
    n_total_eps = len(episodes_df)
    n_with_tconst = int(linked["imdb_id"].notna().sum())
    logger.info(
        "Episode -> IMDb id: %d of %d catalogued episode(s) matched a tconst (%.1f%%)",
        n_with_tconst, n_total_eps,
        (n_with_tconst / n_total_eps * 100) if n_total_eps else 0.0,
    )

    # --- backfill imdb_id onto silver/episodes (every match, rated or not) ---
    id_by_episode = linked.set_index("episode_id")["imdb_id"]
    episodes_out = episodes_df.copy()
    episodes_out["imdb_id"] = (
        episodes_out["episode_id"].map(id_by_episode).astype("string")
    )
    episodes_key = s3_utils.build_path(
        "silver", "episodes", ingestion_date, "episodes.parquet"
    )
    s3_utils.write_parquet(bucket, episodes_key, episodes_out)
    logger.info(
        "Backfilled imdb_id onto %d of %d episode row(s) in %s",
        int(episodes_out["imdb_id"].notna().sum()), len(episodes_out), episodes_key,
    )

    # --- hop 2: tconst -> IMDb rating ---
    matched = linked[["episode_id", "imdb_id"]].dropna(subset=["imdb_id"])
    imdb_join = matched.merge(ratings_df, on="imdb_id", how="inner")
    n_rated = len(imdb_join)
    logger.info(
        "Episode -> IMDb rating: %d of %d episode(s) with a tconst have a rating "
        "row (%.1f%%)",
        n_rated, n_with_tconst,
        (n_rated / n_with_tconst * 100) if n_with_tconst else 0.0,
    )
    imdb_join = imdb_join.reset_index(drop=True)
    imdb_rows = pd.DataFrame({
        "episode_id": pd.to_numeric(imdb_join["episode_id"], errors="coerce").astype("Int64"),
        "source": "imdb",
        "rating": pd.to_numeric(imdb_join["rating"], errors="coerce"),
        "vote_count": pd.to_numeric(imdb_join["vote_count"], errors="coerce").astype("Int64"),
    })

    # --- 'tmdb' rows straight off the episodes Parquet's own figures ---
    tmdb_src = episodes_df[["episode_id", "vote_average", "vote_count"]].copy()
    tmdb_src["rating"] = pd.to_numeric(tmdb_src["vote_average"], errors="coerce")
    n_no_tmdb = int(tmdb_src["rating"].isna().sum())
    if n_no_tmdb:
        logger.info(
            "%d episode(s) have no TMDB vote_average — no source='tmdb' row for them",
            n_no_tmdb,
        )
    tmdb_src = tmdb_src.dropna(subset=["rating"]).reset_index(drop=True)
    tmdb_rows = pd.DataFrame({
        "episode_id": pd.to_numeric(tmdb_src["episode_id"], errors="coerce").astype("Int64"),
        "source": "tmdb",
        "rating": tmdb_src["rating"],
        "vote_count": pd.to_numeric(tmdb_src["vote_count"], errors="coerce").astype("Int64"),
    })

    out = pd.concat([imdb_rows, tmdb_rows], ignore_index=True)
    out = out[["episode_id", "source", "rating", "vote_count"]]
    output_key = s3_utils.build_path(
        "silver", "episode_ratings", ingestion_date, "episode_ratings.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, out)
    logger.info(
        "Wrote %d Silver episode_ratings row(s) to %s (%d imdb, %d tmdb)",
        len(out), uri, len(imdb_rows), len(tmdb_rows),
    )
    return uri


def transform_imdb_ratings(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    with_tv: bool = False,
) -> str:
    """Read Bronze IMDb ratings + this partition's Silver movies -> resolve -> write Silver.

    With `with_tv=True` the same snapshot is also joined onto this partition's
    `silver/series/series.parquet` (Task 81), writing
    `silver/series_ratings/series_ratings.parquet`, and a second IMDb bulk file
    (`bronze/imdb_episodes`) resolves every catalogued episode to its IMDb id
    and rating (Task 83), writing `silver/episode_ratings/episode_ratings.parquet`
    and backfilling an `imdb_id` column onto `silver/episodes/episodes.parquet`.
    The return value is always the movie Parquet's s3:// URI; the TV files are
    logged side effects.
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

    if with_tv:
        _transform_series_ratings(ratings_df, bucket, ingestion_date)
        _transform_episode_ratings(ratings_df, bucket, ingestion_date)

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
    parser.add_argument(
        "--with-tv",
        action="store_true",
        help="Also join the snapshot onto this partition's Silver series "
             "and write silver/series_ratings (Task 81).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("transform_imdb_ratings")
    args = _parse_args()
    transform_imdb_ratings(ingestion_date=args.date, with_tv=args.with_tv)
