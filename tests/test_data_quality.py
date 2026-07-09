"""Unit tests for data_quality/silver_checks.py.

All tests are pure-Python / in-memory — no S3 access, no disk writes
(except the reject-writer tests which use tmp_path).
"""

from __future__ import annotations

import datetime as dt
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl import s3_utils
from data_quality.silver_checks import (
    CheckResult,
    _check_schema,
    _duplicate_mask,
    _null_mask,
    _range_mask,
    _run_entity_checks,
    _write_rejects,
    run_silver_checks,
    ENTITY_CONFIGS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _movies_df(**overrides) -> pd.DataFrame:
    """Minimal valid Silver movies DataFrame (1 row)."""
    row = {
        "movie_id": 550,
        "title": "Fight Club",
        "release_date": dt.date(1999, 10, 15),
        "runtime": 139,
        "budget": 63_000_000,
        "revenue": 101_000_000,
        "original_language": "en",
        "status": "Released",
        "vote_average": 8.4,
        "vote_count": 24000,
        "popularity": 55.0,
        "overview": "An insomniac office worker...",
        "tagline": "Mischief. Mayhem. Soap.",
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "genre_ids": [18, 53],
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _bridge_df(**overrides) -> pd.DataFrame:
    """Minimal valid Silver credits_bridge DataFrame (1 row)."""
    row = {
        "movie_id": 550,
        "person_id": 10,
        "credit_type": "cast",
        "role": "Narrator",
        "ordering": 0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _make_s3_mock_for_entity(entity: str, df: pd.DataFrame) -> MagicMock:
    """Build an S3 mock whose get_object returns `df` as Parquet bytes."""
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    mock_s3 = MagicMock()
    body = MagicMock()
    body.read.return_value = buf.getvalue()
    mock_s3.get_object.return_value = {"Body": body}
    mock_s3.put_object.return_value = {}
    return mock_s3


# ---------------------------------------------------------------------------
# _check_schema
# ---------------------------------------------------------------------------

def test_check_schema_returns_empty_when_all_present():
    df = _movies_df()
    missing = _check_schema(df, ENTITY_CONFIGS["movies"]["expected_cols"])
    assert missing == []


def test_check_schema_returns_missing_column_names():
    df = _movies_df().drop(columns=["title", "budget"])
    missing = _check_schema(df, ENTITY_CONFIGS["movies"]["expected_cols"])
    assert "title" in missing
    assert "budget" in missing


# ---------------------------------------------------------------------------
# _null_mask
# ---------------------------------------------------------------------------

def test_null_mask_clean_df_returns_all_false():
    df = _movies_df()
    mask = _null_mask(df, ["movie_id", "title"])
    assert not mask.any()


def test_null_mask_flags_row_with_null_required_field():
    df = _movies_df(title=None)
    mask = _null_mask(df, ["movie_id", "title"])
    assert mask.iloc[0]


def test_null_mask_ignores_null_in_non_required_column():
    df = _movies_df(overview=None)
    mask = _null_mask(df, ["movie_id", "title"])
    assert not mask.any()


# ---------------------------------------------------------------------------
# _duplicate_mask
# ---------------------------------------------------------------------------

def test_duplicate_mask_unique_rows_returns_all_false():
    df = pd.DataFrame([{"movie_id": 1}, {"movie_id": 2}])
    mask = _duplicate_mask(df, ["movie_id"])
    assert not mask.any()


def test_duplicate_mask_flags_second_occurrence_of_pk():
    df = pd.DataFrame([{"movie_id": 1}, {"movie_id": 1}, {"movie_id": 2}])
    mask = _duplicate_mask(df, ["movie_id"])
    assert int(mask.sum()) == 1
    assert mask.iloc[1]  # second row is the dup


def test_duplicate_mask_composite_pk():
    rows = [
        {"movie_id": 1, "person_id": 10, "credit_type": "cast"},
        {"movie_id": 1, "person_id": 10, "credit_type": "cast"},  # dup
        {"movie_id": 1, "person_id": 10, "credit_type": "crew"},  # different credit_type — ok
    ]
    df = pd.DataFrame(rows)
    mask = _duplicate_mask(df, ["movie_id", "person_id", "credit_type"])
    assert int(mask.sum()) == 1


# ---------------------------------------------------------------------------
# _range_mask
# ---------------------------------------------------------------------------

def test_range_mask_clean_values_returns_all_false():
    df = _movies_df()
    mask = _range_mask(df, ENTITY_CONFIGS["movies"]["ranges"])
    assert not mask.any()


def test_range_mask_flags_vote_average_above_10():
    df = _movies_df(vote_average=10.1)
    mask = _range_mask(df, {"vote_average": (0.0, 10.0)})
    assert mask.iloc[0]


def test_range_mask_flags_negative_vote_count():
    df = _movies_df(vote_count=-1)
    mask = _range_mask(df, {"vote_count": (0, None)})
    assert mask.iloc[0]


def test_range_mask_ignores_null_values():
    """Null in a numeric column must not be treated as a range failure."""
    df = _movies_df(vote_average=None)
    mask = _range_mask(df, {"vote_average": (0.0, 10.0)})
    assert not mask.any()


# ---------------------------------------------------------------------------
# _write_rejects
# ---------------------------------------------------------------------------

def test_write_rejects_creates_parquet_file(tmp_path):
    df = _movies_df()
    df["rejection_reason"] = "test_reason"
    path = _write_rejects([df], "movies", dt.date(2026, 6, 22), tmp_path)
    assert path is not None
    assert path.exists()
    df_back = pd.read_parquet(path)
    assert "rejection_reason" in df_back.columns
    assert len(df_back) == 1


def test_write_rejects_returns_none_when_no_rejects(tmp_path):
    path = _write_rejects([], "movies", dt.date(2026, 6, 22), tmp_path)
    assert path is None


# ---------------------------------------------------------------------------
# _run_entity_checks
# ---------------------------------------------------------------------------

def test_run_entity_checks_clean_df_all_pass(tmp_path):
    df = _movies_df()
    results = _run_entity_checks(df, "movies", ENTITY_CONFIGS["movies"],
                                 dt.date(2026, 6, 22), tmp_path)
    assert all(r.passed for r in results)
    checks = {r.check for r in results}
    assert {"schema", "nulls", "duplicates", "ranges"} == checks


def test_run_entity_checks_null_required_field_fails_and_writes_reject(tmp_path):
    df = _movies_df(title=None)
    results = _run_entity_checks(df, "movies", ENTITY_CONFIGS["movies"],
                                 dt.date(2026, 6, 22), tmp_path)
    null_result = next(r for r in results if r.check == "nulls")
    assert not null_result.passed
    assert null_result.bad_count == 1
    reject_file = tmp_path / "movies_rejected_2026-06-22.parquet"
    assert reject_file.exists()


def test_run_entity_checks_duplicate_pk_fails(tmp_path):
    df = pd.concat([_movies_df(), _movies_df()], ignore_index=True)
    results = _run_entity_checks(df, "movies", ENTITY_CONFIGS["movies"],
                                 dt.date(2026, 6, 22), tmp_path)
    dup_result = next(r for r in results if r.check == "duplicates")
    assert not dup_result.passed
    assert dup_result.bad_count == 1


def test_run_entity_checks_out_of_range_fails(tmp_path):
    df = _movies_df(vote_average=11.0)
    results = _run_entity_checks(df, "movies", ENTITY_CONFIGS["movies"],
                                 dt.date(2026, 6, 22), tmp_path)
    range_result = next(r for r in results if r.check == "ranges")
    assert not range_result.passed
    assert range_result.bad_count == 1


def test_run_entity_checks_missing_column_fails_schema(tmp_path):
    df = _movies_df().drop(columns=["title"])
    results = _run_entity_checks(df, "movies", ENTITY_CONFIGS["movies"],
                                 dt.date(2026, 6, 22), tmp_path)
    schema_result = next(r for r in results if r.check == "schema")
    assert not schema_result.passed
    assert schema_result.bad_count == 1


# ---------------------------------------------------------------------------
# run_silver_checks (integration-level, S3 mocked)
# ---------------------------------------------------------------------------

def _make_multi_entity_s3_mock(entity_dfs: dict[str, pd.DataFrame]) -> MagicMock:
    """Return an S3 mock that serves a different Parquet body per entity key."""
    buffers: dict[str, bytes] = {}
    for entity, df in entity_dfs.items():
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", index=False)
        buffers[entity] = buf.getvalue()

    mock_s3 = MagicMock()

    def get_object(Bucket, Key):
        # Key contains the entity name as a path segment
        for entity, data in buffers.items():
            if f"/{entity}/" in Key:
                body = MagicMock()
                body.read.return_value = data
                return {"Body": body}
        raise Exception(f"No mock data for key: {Key}")

    mock_s3.get_object.side_effect = get_object
    return mock_s3


def _all_entity_dfs() -> dict[str, pd.DataFrame]:
    """Minimal valid Silver DataFrames for all five entities."""
    actors_df = pd.DataFrame([{"person_id": 10, "name": "Alice", "gender": 1, "popularity": 20.0, "profile_path": "/a.jpg"}])
    directors_df = pd.DataFrame([{"person_id": 20, "name": "Carol", "gender": 1, "popularity": 30.0, "profile_path": "/c.jpg"}])
    genres_df = pd.DataFrame([{"genre_id": 28, "genre_name": "Action"}])
    return {
        "movies": _movies_df(),
        "actors": actors_df,
        "directors": directors_df,
        "genres": genres_df,
        "credits_bridge": _bridge_df(),
    }


def test_run_silver_checks_all_clean_all_pass(tmp_path):
    mock_s3 = _make_multi_entity_s3_mock(_all_entity_dfs())

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_run_silver_checks_bad_row_produces_failed_result(tmp_path):
    """A bad vote_average in movies must cause the ranges check to fail."""
    dfs = _all_entity_dfs()
    dfs["movies"] = _movies_df(vote_average=99.0)
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    movies_range = next(r for r in results if r.entity == "movies" and r.check == "ranges")
    assert not movies_range.passed


def test_run_silver_checks_missing_file_records_load_failure(tmp_path):
    """If a Silver Parquet cannot be read, a load-failure CheckResult is added."""
    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = Exception("NoSuchKey")

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    load_failures = [r for r in results if r.check == "load" and not r.passed]
    assert len(load_failures) == len(ENTITY_CONFIGS)
