"""Warehouse loader: franchises from TMDB collections (Task 153).

`load_dimensions.load_dim_collection()` still upserts the three scalars a movie
payload carries (name, poster, backdrop) and must run first. This module loads
the rest of what `GET /collection/{id}` gives, from the five Silver files
`etl/silver/transform_collections.py` writes:

    dim_collection              detail columns upserted onto the existing row
    dim_collection_part         REPLACE by collection_id
    dim_collection_image        REPLACE by collection_id
    collection_translation      REPLACE by collection_id
    collection_part_translation REPLACE by collection_id

and then prunes orphans. Everything runs in **one transaction**, so a reader never
sees a collection with parts but no gallery, and a failure leaves the previous
night's rows exactly as they were.

**Scope: only the ids in today's Silver collections.** A collection whose fetch
failed has no Silver row, so it is neither updated nor replaced: yesterday's
overview, parts and gallery stay put instead of being nulled by a flaky night.
Within that scope the child tables are *replaced* (not upserted), because a
franchise can lose a part or an image upstream and an upsert would strand it
forever with no failing check. The replace scope is passed in explicitly, so a
collection whose parts all vanished is cleared too.

**Order matters twice.** Part translations are deleted *before* parts and inserted
*after* them (their FK points at a part). And children go before parents when
pruning (the Incident 2 lesson).

**Prune.** A `dim_collection` row no `dim_movie` row references is deleted with its
children: 24 such rows existed on 2026-11-16 and TMDB answers 404 for every one
(deleted upstream), and a collection a film is later moved out of ends up here
too. Idempotent: the second run deletes nothing.

`dim_collection_part.movie_id` has no FK (692 of 1,313 parts are films the
catalogue does not hold), so a part is never rejected for being unknown.

Usage:
    python -m etl.warehouse_loader.load_collections --date 2026-11-16
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

import config
from etl.warehouse_loader.common import (
    _existing_ids,
    _read_silver_parquet,
    _replace_by_parent,
    _upsert,
    _write_rejects,
)
from etl.warehouse_loader.load_dimensions import _load_translations, assign_slugs
from warehouse.db import get_session

logger = logging.getLogger(__name__)

_DETAIL_COLS = [
    "name", "poster_path", "backdrop_path", "original_name", "original_language",
    "overview", "homepage",
]
_PART_COLS = [
    "collection_id", "movie_id", "position", "title", "original_title",
    "original_language", "release_date", "overview", "poster_path", "backdrop_path",
    "genre_ids", "popularity", "vote_average", "vote_count", "adult", "video",
]
_IMAGE_COLS = [
    "collection_id", "file_path", "kind", "iso_639_1", "iso_3166_1", "width",
    "height", "aspect_ratio", "vote_average", "vote_count",
]
_PART_TRANSLATION_COLS = ["collection_id", "movie_id", "lang", "title", "overview"]

# Children first, parent last: the FK order the prune walks.
_CHILD_TABLES = (
    "collection_part_translation", "dim_collection_part", "dim_collection_image",
    "collection_translation",
)


def _scalar(value: Any) -> Any:
    """NA/NaT/NaN -> None, numpy scalars -> Python scalars (psycopg2 adapts neither)."""
    if getattr(value, "ndim", 0) > 0:  # a Parquet list column reads back as an ndarray
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    if value is None or (not hasattr(value, "__len__") and pd.isna(value)):
        return None
    return value.item() if hasattr(value, "item") else value


def _rows(df: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    """DataFrame -> list of plain-Python dicts; a `genre_ids` array becomes a list of ints."""
    out = []
    for record in df.to_dict("records"):
        row = {c: _scalar(record.get(c)) for c in columns}
        if "genre_ids" in row and record.get("genre_ids") is not None:
            row["genre_ids"] = [int(g) for g in record["genre_ids"]]
        out.append(row)
    return out


def load_collection_details(
    session: Session, df: pd.DataFrame, valid_ids: set[int],
) -> tuple[int, list[dict[str, Any]]]:
    """Upsert the `/collection/{id}` columns onto dim_collection rows that exist.

    Details win over the movie stub for `name` and `poster_path`. A Silver row for
    an id with no dim_collection row, or with no name (the column is NOT NULL), is
    quarantined, never dropped.
    """
    rows, rejects = [], []
    for row in _rows(df, ["collection_id", *_DETAIL_COLS]):
        if row["collection_id"] is None or int(row["collection_id"]) not in valid_ids:
            rejects.append({**row, "rejection_reason": "unknown collection_id"})
        elif row["name"] is None:
            rejects.append({**row, "rejection_reason": "missing name"})
        else:
            rows.append({**row, "collection_id": int(row["collection_id"])})
    count = _upsert(
        session, "dim_collection", ["collection_id"], ["collection_id", *_DETAIL_COLS], rows
    )
    logger.info("dim_collection: upserted details for %d row(s), rejected %d", count, len(rejects))
    return count, rejects


def _replace_children(
    session: Session, table: str, scope_ids: list[int], columns: list[str],
    rows: list[dict[str, Any]],
) -> int:
    count = _replace_by_parent(session, table, "collection_id", scope_ids, columns, rows)
    logger.info("%s: replaced %d row(s) across %d collection(s)", table, count, len(scope_ids))
    return count


def prune_orphan_collections(session: Session) -> int:
    """Delete every dim_collection row no dim_movie row references, children first.

    Returns the number of collections removed. Idempotent.
    """
    orphans = (
        "SELECT c.collection_id FROM dim_collection c "
        "WHERE NOT EXISTS (SELECT 1 FROM dim_movie m WHERE m.collection_id = c.collection_id)"
    )
    for table in _CHILD_TABLES:
        session.execute(text(f"DELETE FROM {table} WHERE collection_id IN ({orphans})"))
    removed = session.execute(
        text(f"DELETE FROM dim_collection WHERE collection_id IN ({orphans})")
    ).rowcount
    logger.info("dim_collection: pruned %d orphan row(s) (no film references them)", removed)
    return removed


def load_collections(
    ingestion_date: dt.date | None = None,
    bucket: str | None = None,
    rejected_dir: Path | None = None,
) -> dict[str, int]:
    """Load one date's Silver collection files into the warehouse. Returns row counts.

    Must run after load_dimensions() for the same date: it needs dim_collection
    populated and dim_movie loaded (the prune asks which collections films
    still reference).
    """
    if ingestion_date is None:
        ingestion_date = dt.date.today()
    if bucket is None:
        bucket = config.S3_BUCKET
    if rejected_dir is None:
        rejected_dir = config.REJECTED_DIR

    t0 = time.monotonic()
    logger.info("Starting collection load for ingestion_date=%s", ingestion_date)

    def silver(entity: str) -> pd.DataFrame:
        return _read_silver_parquet(bucket, entity, ingestion_date, f"{entity}.parquet")

    collections_df = silver("collections")
    parts_df = silver("collection_parts")
    images_df = silver("collection_images")
    translations_df = silver("collection_translations")
    part_translations_df = silver("collection_part_translations")

    counts: dict[str, int] = {}
    with get_session() as session:
        known = _existing_ids(session, "dim_collection", "collection_id")
        counts["dim_collection"], detail_rejects = load_collection_details(
            session, collections_df, known
        )
        # Only ids that now have details — and a dim_collection row to hang children on.
        scope = sorted(
            int(c) for c in collections_df["collection_id"].dropna() if int(c) in known
        )
        scope_set = set(scope)

        # Part translations reference parts, so they go first on the way out ...
        _replace_by_parent(
            session, "collection_part_translation", "collection_id", scope,
            _PART_TRANSLATION_COLS + ["ingestion_date"], [],
        )
        part_rows = [
            {**r, "ingestion_date": ingestion_date}
            for r in _rows(parts_df, _PART_COLS)
            if r["collection_id"] in scope_set
        ]
        counts["dim_collection_part"] = _replace_children(
            session, "dim_collection_part", scope, _PART_COLS + ["ingestion_date"], part_rows
        )
        image_rows = [
            {**r, "ingestion_date": ingestion_date}
            for r in _rows(images_df, _IMAGE_COLS)
            if r["collection_id"] in scope_set
        ]
        counts["dim_collection_image"] = _replace_children(
            session, "dim_collection_image", scope, _IMAGE_COLS + ["ingestion_date"], image_rows
        )
        counts["collection_translation"], translation_rejects = _load_translations(
            session, translations_df, ingestion_date, table="collection_translation",
            parent_col="collection_id", parent_table="dim_collection",
            text_cols=["name", "overview", "homepage"], replace=True, scope_ids=scope,
        )
        # ... and come back last, once the parts they point at exist again.
        loaded_parts = {(r["collection_id"], r["movie_id"]) for r in part_rows}
        pt_rows, pt_rejects = [], []
        for r in _rows(part_translations_df, _PART_TRANSLATION_COLS):
            if (r["collection_id"], r["movie_id"]) not in loaded_parts:
                if r["collection_id"] in scope_set:
                    pt_rejects.append({**r, "rejection_reason": "unknown part"})
                continue
            pt_rows.append({**r, "ingestion_date": ingestion_date})
        counts["collection_part_translation"] = _replace_by_parent(
            session, "collection_part_translation", "collection_id", scope,
            _PART_TRANSLATION_COLS + ["ingestion_date"], pt_rows,
        )
        logger.info(
            "collection_part_translation: replaced %d row(s), rejected %d",
            counts["collection_part_translation"], len(pt_rejects),
        )

        counts["pruned"] = prune_orphan_collections(session)
        counts["dim_collection_slugs"] = assign_slugs(
            session, "dim_collection", "collection_id", "name"
        )

    _write_rejects(detail_rejects, "dim_collection_details", ingestion_date, rejected_dir)
    _write_rejects(translation_rejects, "collection_translation", ingestion_date, rejected_dir)
    _write_rejects(pt_rejects, "collection_part_translation", ingestion_date, rejected_dir)

    logger.info(
        "Collection load complete: %s in %.2fs",
        ", ".join(f"{k}={v}" for k, v in counts.items()), time.monotonic() - t0,
    )
    return counts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load one date's Silver collection files into the warehouse."
    )
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=None,
        help="Ingestion date (YYYY-MM-DD). Defaults to today.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    from etl.logging_config import setup_logging

    setup_logging("load_collections")
    args = _parse_args()
    load_collections(ingestion_date=args.date)
