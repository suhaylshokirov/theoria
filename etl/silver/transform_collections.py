"""Silver transform: film collections (franchises), Task 153.

Reads the three Bronze prefixes `etl/bronze/ingest_collections.py` writes for one
ingestion_date and writes **five** dated Parquets:

    silver/collections/…/collections.parquet
        collection_id, name, original_name, original_language, overview,
        poster_path, backdrop_path, homepage. "" becomes None. `homepage` is the
        `en` translation entry's — the only English homepage TMDB publishes.
    silver/collection_parts/…/collection_parts.parquet
        One row per film TMDB lists in a collection: every `parts[]` key except
        `media_type` (always "movie" — it is the endpoint) and `softcore` (always
        false, undocumented), plus `position`, the index in TMDB's array. TMDB's
        array order is not release order (it is in only 277 of 368 collections),
        so the site sorts by date; `position` is kept as a stable tiebreak.
        An empty `release_date` becomes NaT. `genre_ids` stays a list.
    silver/collection_images/…/collection_images.parquet
        One row per poster and backdrop, with `kind`. Deduplicated on
        (collection_id, file_path) keeping the first (7 repeats on 2026-11-16).
    silver/collection_translations/…/collection_translations.parquet
        name / overview / homepage per shipped language, via
        etl.translations.select_translations (which already drops Uzbek names,
        the Task 110 rule, and the extra `en` entry Bronze keeps for the homepage).
    silver/collection_part_translations/…/collection_part_translations.parquet
        The localised `parts[]` title and overview, from the `language=ru-RU` /
        `uz-UZ` Bronze files — the only Russian text for the parts that are not
        in the catalogue. TMDB falls back to English for a field it has no
        translation for, so a localised value is kept **only when it differs
        from the English one**. A `uz` title is never kept (NAME_LANGUAGES).
        A row where every field ended up empty is not written.

A collection with no details file for the date (a failed fetch, or TMDB 404)
contributes no rows to any of the five. That is not an error here and not a
deletion downstream: the loader replaces rows only for the ids present in
today's Silver collections, so yesterday's rows for a failed id stay put.

S3 sources:
    bronze/collection_details/ingestion_date=D/<id>.json
    bronze/collection_localized/ingestion_date=D/{ru,uz}/<id>.json

Idempotent: running twice for the same date overwrites the same keys.

Usage:
    python -m etl.silver.transform_collections
    python -m etl.silver.transform_collections --date 2026-11-16
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
from etl.bronze.ingest_collections import DETAILS_ENTITY, LOCALIZED_ENTITY
from etl.translations import NAME_LANGUAGES, TRANSLATION_LANGUAGES, select_translations

logger = logging.getLogger(__name__)

COLLECTIONS_COLUMNS = [
    "collection_id", "name", "original_name", "original_language", "overview",
    "poster_path", "backdrop_path", "homepage",
]
PART_COLUMNS = [
    "collection_id", "movie_id", "position", "title", "original_title",
    "original_language", "release_date", "overview", "poster_path",
    "backdrop_path", "genre_ids", "popularity", "vote_average", "vote_count",
    "adult", "video",
]
IMAGE_COLUMNS = [
    "collection_id", "file_path", "kind", "iso_639_1", "iso_3166_1", "width",
    "height", "aspect_ratio", "vote_average", "vote_count",
]
TRANSLATION_COLUMNS = ["collection_id", "lang", "name", "overview", "homepage"]
PART_TRANSLATION_COLUMNS = ["collection_id", "movie_id", "lang", "title", "overview"]

# Silver entity -> its columns. One place that names all five outputs.
OUTPUTS: dict[str, list[str]] = {
    "collections": COLLECTIONS_COLUMNS,
    "collection_parts": PART_COLUMNS,
    "collection_images": IMAGE_COLUMNS,
    "collection_translations": TRANSLATION_COLUMNS,
    "collection_part_translations": PART_TRANSLATION_COLUMNS,
}

_IMAGE_KINDS = (("posters", "poster"), ("backdrops", "backdrop"))


def _clean(value: Any) -> str | None:
    """Strip a text field; "" / whitespace / null / a non-string all become None."""
    return value.strip() or None if isinstance(value, str) else None


def _english_homepage(raw: dict[str, Any]) -> str | None:
    """The `en` translation entry's homepage; the US entry wins over a regional one."""
    block = raw.get("translations")
    entries = block.get("translations") if isinstance(block, dict) else None
    best: tuple[int, str] | None = None
    for entry in entries or []:
        if entry.get("iso_639_1") != "en":
            continue
        homepage = _clean((entry.get("data") or {}).get("homepage"))
        if homepage is None:
            continue
        rank = 0 if (entry.get("iso_3166_1") or "").upper() == "US" else 1
        if best is None or rank < best[0]:
            best = (rank, homepage)
    return best[1] if best else None


def _extract_collection(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "collection_id": raw.get("id"),
        "name": _clean(raw.get("name")),
        "original_name": _clean(raw.get("original_name")),
        "original_language": _clean(raw.get("original_language")),
        "overview": _clean(raw.get("overview")),
        "poster_path": _clean(raw.get("poster_path")),
        "backdrop_path": _clean(raw.get("backdrop_path")),
        "homepage": _english_homepage(raw),
    }


def _extract_parts(raw: dict[str, Any]) -> list[dict[str, Any]]:
    cid = raw.get("id")
    rows = []
    for position, part in enumerate(raw.get("parts") or []):
        rows.append({
            "collection_id": cid,
            "movie_id": part.get("id"),
            "position": position,
            "title": _clean(part.get("title")),
            "original_title": _clean(part.get("original_title")),
            "original_language": _clean(part.get("original_language")),
            "release_date": part.get("release_date") or None,
            "overview": _clean(part.get("overview")),
            "poster_path": _clean(part.get("poster_path")),
            "backdrop_path": _clean(part.get("backdrop_path")),
            "genre_ids": [int(g) for g in part.get("genre_ids") or []],
            "popularity": part.get("popularity"),
            "vote_average": part.get("vote_average"),
            "vote_count": part.get("vote_count"),
            "adult": part.get("adult"),
            "video": part.get("video"),
        })
    return rows


def _extract_images(raw: dict[str, Any]) -> list[dict[str, Any]]:
    cid = raw.get("id")
    images = raw.get("images") or {}
    rows = []
    for key, kind in _IMAGE_KINDS:
        for image in images.get(key) or []:
            rows.append({
                "collection_id": cid,
                "file_path": _clean(image.get("file_path")),
                "kind": kind,
                "iso_639_1": _clean(image.get("iso_639_1")),
                "iso_3166_1": _clean(image.get("iso_3166_1")),
                "width": image.get("width"),
                "height": image.get("height"),
                "aspect_ratio": image.get("aspect_ratio"),
                "vote_average": image.get("vote_average"),
                "vote_count": image.get("vote_count"),
            })
    return rows


def _extract_translations(raw: dict[str, Any]) -> list[dict[str, Any]]:
    picked = select_translations(raw, ("title", "overview", "homepage")) or {}
    return [
        {
            "collection_id": raw.get("id"), "lang": lang,
            "name": values["title"], "overview": values["overview"],
            "homepage": values["homepage"],
        }
        for lang, values in picked.items()
    ]


def _extract_part_translations(
    english: dict[str, Any], localized: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Localised part title/overview, kept only where it differs from the English part.

    `english` is the details payload; `localized` maps lang -> that language's
    `language=xx-XX` payload for the same collection. A part the English payload
    does not list is ignored (it would have no `dim_collection_part` row to hang
    off).
    """
    en_parts = {p.get("id"): p for p in english.get("parts") or []}
    rows = []
    for lang, payload in localized.items():
        for part in payload.get("parts") or []:
            base = en_parts.get(part.get("id"))
            if base is None:
                continue
            title = _clean(part.get("title"))
            if lang not in NAME_LANGUAGES or title == _clean(base.get("title")):
                title = None
            overview = _clean(part.get("overview"))
            if overview == _clean(base.get("overview")):
                overview = None
            if title is None and overview is None:
                continue
            rows.append({
                "collection_id": english.get("id"), "movie_id": part.get("id"),
                "lang": lang, "title": title, "overview": overview,
            })
    return rows


def extract_frames(
    details: list[dict[str, Any]],
    localized: dict[int, dict[str, dict[str, Any]]],
) -> dict[str, pd.DataFrame]:
    """Pure transform: Bronze payloads -> the five typed Silver frames.

    `details` are the `collection_details` payloads; `localized` maps
    collection_id -> {lang: payload}. No I/O, so tests can call it directly.
    """
    coll, parts, images, translations, part_translations = [], [], [], [], []
    for raw in details:
        coll.append(_extract_collection(raw))
        parts.extend(_extract_parts(raw))
        images.extend(_extract_images(raw))
        translations.extend(_extract_translations(raw))
        part_translations.extend(
            _extract_part_translations(raw, localized.get(raw.get("id"), {}))
        )

    frames = {
        "collections": _typed_collections(pd.DataFrame(coll, columns=COLLECTIONS_COLUMNS)),
        "collection_parts": _typed_parts(pd.DataFrame(parts, columns=PART_COLUMNS)),
        "collection_images": _typed_images(pd.DataFrame(images, columns=IMAGE_COLUMNS)),
        "collection_translations": _typed_text(
            pd.DataFrame(translations, columns=TRANSLATION_COLUMNS),
            ("lang", "name", "overview", "homepage"),
        ),
        "collection_part_translations": _typed_text(
            pd.DataFrame(part_translations, columns=PART_TRANSLATION_COLUMNS),
            ("lang", "title", "overview"), int_cols=("collection_id", "movie_id"),
        ),
    }
    return _dedupe(frames)


def _int(df: pd.DataFrame, cols: tuple[str, ...]) -> None:
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")


def _str(df: pd.DataFrame, cols: tuple[str, ...]) -> None:
    for c in cols:
        df[c] = df[c].astype("string")


def _typed_collections(df: pd.DataFrame) -> pd.DataFrame:
    _int(df, ("collection_id",))
    _str(df, tuple(c for c in COLLECTIONS_COLUMNS if c != "collection_id"))
    return df


def _typed_parts(df: pd.DataFrame) -> pd.DataFrame:
    _int(df, ("collection_id", "movie_id", "position", "vote_count"))
    _str(df, ("title", "original_title", "original_language", "overview",
              "poster_path", "backdrop_path"))
    for c in ("popularity", "vote_average"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["release_date"] = pd.to_datetime(df["release_date"], errors="coerce").dt.date
    for c in ("adult", "video"):
        df[c] = df[c].astype("boolean")
    return df


def _typed_images(df: pd.DataFrame) -> pd.DataFrame:
    _int(df, ("collection_id", "width", "height", "vote_count"))
    _str(df, ("file_path", "kind", "iso_639_1", "iso_3166_1"))
    for c in ("aspect_ratio", "vote_average"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _typed_text(
    df: pd.DataFrame, str_cols: tuple[str, ...], int_cols: tuple[str, ...] = ("collection_id",)
) -> pd.DataFrame:
    _int(df, int_cols)
    _str(df, str_cols)
    return df


def _dedupe(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Drop repeated keys (keeping the first) and rows with a null key, logging each."""
    keys = {
        "collections": ["collection_id"],
        "collection_parts": ["collection_id", "movie_id"],
        "collection_images": ["collection_id", "file_path"],
        "collection_translations": ["collection_id", "lang"],
        "collection_part_translations": ["collection_id", "movie_id", "lang"],
    }
    for entity, key in keys.items():
        df = frames[entity]
        n_null = int(df[key].isna().any(axis=1).sum())
        if n_null:
            logger.warning("[%s] Dropping %d row(s) with a null key %s", entity, n_null, key)
            df = df.dropna(subset=key)
        before = len(df)
        df = df.drop_duplicates(subset=key, keep="first")
        if before - len(df):
            logger.info("[%s] Dropped %d duplicate row(s) on %s", entity, before - len(df), key)
        frames[entity] = df.reset_index(drop=True)
    return frames


def _list_keys(bucket: str, prefix: str) -> list[str]:
    client = s3_utils.get_s3_client()
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".json"))
    return keys


def transform_collections(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
) -> dict[str, str]:
    """Read the date's Bronze collection files -> write the five Silver Parquets.

    Returns {entity: s3:// URI}. Raises FileNotFoundError when the date has no
    Bronze collection-detail file at all, and RuntimeError when every one fails
    to parse.
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET

    t0 = time.monotonic()
    logger.info("Starting Silver collections transform for date=%s", ingestion_date)

    detail_keys = _list_keys(
        bucket, s3_utils.build_path("bronze", DETAILS_ENTITY, ingestion_date, "")
    )
    if not detail_keys:
        raise FileNotFoundError(
            f"No Bronze collection-detail files found for ingestion_date={ingestion_date}"
        )
    local_keys = {
        lang: _list_keys(
            bucket,
            s3_utils.build_path("bronze", LOCALIZED_ENTITY, ingestion_date, f"{lang}/"),
        )
        for lang in TRANSLATION_LANGUAGES
    }
    logger.info(
        "Found %d collection-detail file(s) and %s localised file(s)",
        len(detail_keys),
        ", ".join(f"{lang}={len(k)}" for lang, k in local_keys.items()),
    )

    details: list[dict[str, Any]] = []
    errors = 0
    for key, raw, err in s3_utils.read_json_objects(bucket, detail_keys):
        if err is not None or not isinstance(raw, dict):
            errors += 1
            logger.error("Failed to read %s: %s", key, err)
            continue
        details.append(raw)
    if not details:
        raise RuntimeError(
            f"Every Bronze collection file failed to parse for ingestion_date={ingestion_date} — aborting."
        )

    localized: dict[int, dict[str, dict[str, Any]]] = {}
    local_missing = 0
    for lang, keys in local_keys.items():
        for key, raw, err in s3_utils.read_json_objects(bucket, keys):
            if err is not None or not isinstance(raw, dict):
                errors += 1
                logger.error("Failed to read %s: %s", key, err)
                continue
            localized.setdefault(raw.get("id"), {})[lang] = raw
    for raw in details:
        if len(localized.get(raw.get("id"), {})) < len(TRANSLATION_LANGUAGES):
            local_missing += 1
    if local_missing:
        logger.warning(
            "%d collection(s) have no localised file for at least one language; "
            "they contribute no part translations for it", local_missing,
        )

    frames = extract_frames(details, localized)

    uris: dict[str, str] = {}
    for entity, df in frames.items():
        key = s3_utils.build_path("silver", entity, ingestion_date, f"{entity}.parquet")
        uris[entity] = s3_utils.write_parquet(bucket, key, df)

    logger.info(
        "Silver collections transform complete: %s, %d parse error(s) in %.2fs",
        ", ".join(f"{e}={len(df)}" for e, df in frames.items()),
        errors, time.monotonic() - t0,
    )
    return uris


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transform Bronze collection JSON to Silver Parquet."
    )
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging

    setup_logging("transform_collections")
    args = _parse_args()
    transform_collections(ingestion_date=args.date)
