"""Silver transform: Genres.

Reads the Bronze genre list JSON for a given ingestion_date, flattens the
payload into one row per genre, casts types, deduplicates on genre_id, and
writes a single Parquet file to the Silver layer.

S3 source:  bronze/genres/ingestion_date=YYYY-MM-DD/genres.json
            bronze/genres/ingestion_date=YYYY-MM-DD/genres_tv.json   (with_tv)
S3 output:  silver/genres/ingestion_date=YYYY-MM-DD/genres.parquet

`with_tv=True` merges the `genre/tv/list` response into the same output. The
two lists share 8 ids; measured live (2026-09-08) those 8 carry **identical
names**, so they collapse into one `dim_genre` with no `medium` column and no
conflict. That equality is *asserted* here, not assumed — if TMDB ever
renames a shared genre on one endpoint only, the run fails loud rather than
silently keeping whichever row sorted last.

Idempotent: running twice for the same date overwrites the same key with the
same content.

`transform_genre_translations()` (Task 93) is a second entry point in this
module: it reads the `genres_<lang>.json` / `genres_tv_<lang>.json` files
`ingest_genres(with_translations=True)` writes and produces
`silver/genre_translations/`. It lives here, beside the genre list it
translates, because both read the same Bronze layout and share the same
movie/TV shared-id reasoning. `genre_name` is the *only* column that differs by
language, so it never touches `genres.parquet` — `dim_genre.genre_name` stays the
English name and is never overwritten by a translation.

Usage:
    python -m etl.silver.transform_genres
    python -m etl.silver.transform_genres --date 2026-06-22
    python -m etl.silver.transform_genres --with-tv
    python -m etl.silver.transform_genres --with-tv --with-translations
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import time
from typing import Any

import pandas as pd

import config
from etl import s3_utils
from etl.bronze.ingest_genres import TRANSLATED_GENRE_LANGUAGES

logger = logging.getLogger(__name__)


def _read_bronze_genres(
    bucket: str, ingestion_date: dt.date, filename: str = "genres.json"
) -> dict[str, Any]:
    """Download and parse a Bronze genre JSON file for this date."""
    key = s3_utils.build_path("bronze", "genres", ingestion_date, filename)
    client = s3_utils.get_s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except client.exceptions.NoSuchKey:
        raise FileNotFoundError(
            f"No Bronze genre file found for ingestion_date={ingestion_date} (key={key})"
        )
    return json.loads(response["Body"].read())


def _assert_shared_ids_agree(
    movie_rows: list[dict[str, Any]], tv_rows: list[dict[str, Any]]
) -> int:
    """Raise if a genre id present in both lists carries different names.

    Returns the number of shared ids checked. TMDB's movie and TV genre
    vocabularies overlap; the whole reason the two lists can merge into one
    `dim_genre` is that the overlap agrees on names. Guard it, don't trust it.
    """
    movie_by_id = {r["genre_id"]: r["genre_name"] for r in movie_rows}
    tv_by_id = {r["genre_id"]: r["genre_name"] for r in tv_rows}
    shared = set(movie_by_id) & set(tv_by_id)
    mismatches = {
        gid: (movie_by_id[gid], tv_by_id[gid])
        for gid in shared
        if movie_by_id[gid] != tv_by_id[gid]
    }
    if mismatches:
        raise ValueError(
            "Movie and TV genre lists disagree on shared id(s): "
            + ", ".join(f"{gid}: {m!r} vs {t!r}" for gid, (m, t) in sorted(mismatches.items()))
        )
    return len(shared)


def _extract_genres(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one row per genre from a TMDB genre-list payload."""
    return [
        {"genre_id": g.get("id"), "genre_name": g.get("name")}
        for g in payload.get("genres", [])
    ]


def _cast_genre_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast columns to intended types; bad values become NaN, not crashes."""
    df = df.copy()
    df["genre_id"] = pd.to_numeric(df["genre_id"], errors="coerce").astype("Int64")
    df["genre_name"] = df["genre_name"].astype("string")
    return df


def transform_genres(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    with_tv: bool = False,
) -> str:
    """Read Bronze genre JSON → clean → deduplicate → write Silver Parquet.

    Reads bronze/genres/ingestion_date=.../genres.json, flattens each entry
    into a (genre_id, genre_name) row, casts types, deduplicates on genre_id,
    and writes silver/genres/ingestion_date=.../genres.parquet.

    With `with_tv`, also reads genres_tv.json from the same partition, asserts
    the two lists agree on every shared id, and merges them before dedup.

    Returns the s3:// URI of the written Parquet file.

    Raises FileNotFoundError if no Bronze genre file exists for the given date.
    Raises ValueError if the genres list inside the payload is empty, or if the
    movie and TV lists disagree on a shared id.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver genre transform for date=%s (with_tv=%s)", ingestion_date, with_tv)

    payload = _read_bronze_genres(bucket, ingestion_date)
    rows = _extract_genres(payload)

    if not rows:
        raise ValueError(
            f"Bronze genre file for ingestion_date={ingestion_date} contains no genres."
        )
    logger.info("Extracted %d movie genre row(s) from Bronze", len(rows))

    if with_tv:
        tv_payload = _read_bronze_genres(bucket, ingestion_date, "genres_tv.json")
        tv_rows = _extract_genres(tv_payload)
        if not tv_rows:
            raise ValueError(
                f"Bronze TV genre file for ingestion_date={ingestion_date} contains no genres."
            )
        n_shared = _assert_shared_ids_agree(rows, tv_rows)
        tv_only = len({r["genre_id"] for r in tv_rows} - {r["genre_id"] for r in rows})
        logger.info(
            "Merged %d TV genre row(s): %d shared ids agree, %d TV-only ids added",
            len(tv_rows), n_shared, tv_only,
        )
        rows = rows + tv_rows

    df = pd.DataFrame(rows)
    df = _cast_genre_types(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["genre_id"], keep="last")
    dupes_dropped = before_dedup - len(df)
    if dupes_dropped:
        logger.info("Dropped %d duplicate genre_id row(s)", dupes_dropped)

    null_ids = df["genre_id"].isna().sum()
    if null_ids:
        logger.warning("Dropping %d row(s) with null genre_id", null_ids)
        df = df.dropna(subset=["genre_id"])

    output_key = s3_utils.build_path("silver", "genres", ingestion_date, "genres.parquet")
    uri = s3_utils.write_parquet(bucket, output_key, df)

    elapsed = time.monotonic() - t0
    logger.info(
        "Silver genre transform complete: %d rows written in %.2fs",
        len(df), elapsed,
    )
    return uri


def transform_genre_translations(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    with_tv: bool = False,
) -> str:
    """Read the Bronze per-language genre lists -> write silver/genre_translations.

    One row per (genre_id, lang, genre_name). An entry whose `name` is null or
    blank is skipped: TMDB returns null for languages it has no genre vocabulary
    in (Uzbek — those names are seeded by `28_seed_uz_genres.sql`), and a
    translation table row with no translation is worse than no row, since the
    site would show an empty chip instead of falling back to English.

    With `with_tv`, the TV lists are merged in; a shared movie/TV id that TMDB
    names differently in one language keeps the movie list's name (the list is
    read first and `keep="first"`), logged as a warning rather than failing —
    unlike the English lists, a translation disagreement is cosmetic.

    Returns the s3:// URI of the written Parquet file.

    Raises FileNotFoundError if a language's Bronze file is missing, and
    ValueError if a language yields no named genres at all.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info(
        "Starting Silver genre-translations transform for date=%s (with_tv=%s)",
        ingestion_date, with_tv,
    )

    rows: list[dict[str, Any]] = []
    for lang in TRANSLATED_GENRE_LANGUAGES:
        files = [f"genres_{lang}.json"] + ([f"genres_tv_{lang}.json"] if with_tv else [])
        lang_rows: list[dict[str, Any]] = []
        for filename in files:
            payload = _read_bronze_genres(bucket, ingestion_date, filename)
            for genre in _extract_genres(payload):
                name = genre["genre_name"]
                name = name.strip() if isinstance(name, str) else None
                if genre["genre_id"] is None or not name:
                    continue
                lang_rows.append(
                    {"genre_id": genre["genre_id"], "lang": lang, "genre_name": name}
                )
        if not lang_rows:
            raise ValueError(
                f"Bronze genre files for lang={lang}, ingestion_date={ingestion_date} "
                "contain no named genres."
            )
        rows.extend(lang_rows)

    df = pd.DataFrame(rows, columns=["genre_id", "lang", "genre_name"])
    df["genre_id"] = pd.to_numeric(df["genre_id"], errors="coerce").astype("Int64")
    df["lang"] = df["lang"].astype("string")
    df["genre_name"] = df["genre_name"].astype("string")

    # Movie list first, so keep="first" lets it win a movie/TV name disagreement.
    disagreements = df.groupby(["genre_id", "lang"])["genre_name"].nunique()
    n_disagree = int((disagreements > 1).sum())
    if n_disagree:
        logger.warning(
            "%d genre id(s) have different translated names in the movie and TV "
            "lists; keeping the movie list's name", n_disagree,
        )
    before = len(df)
    df = df.drop_duplicates(subset=["genre_id", "lang"], keep="first")
    if before - len(df):
        logger.info("Dropped %d duplicate (genre_id, lang) row(s)", before - len(df))

    output_key = s3_utils.build_path(
        "silver", "genre_translations", ingestion_date, "genre_translations.parquet"
    )
    uri = s3_utils.write_parquet(bucket, output_key, df)
    logger.info(
        "Silver genre-translations transform complete: %d row(s) in %.2fs",
        len(df), time.monotonic() - t0,
    )
    return uri


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze genre JSON to Silver Parquet."
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
        help="Also merge genres_tv.json into the output.",
    )
    parser.add_argument(
        "--with-translations",
        action="store_true",
        help="Also write silver/genre_translations from the per-language Bronze files.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging
    setup_logging("transform_genres")
    args = _parse_args()
    transform_genres(ingestion_date=args.date, with_tv=args.with_tv)
    if args.with_translations:
        transform_genre_translations(ingestion_date=args.date, with_tv=args.with_tv)
