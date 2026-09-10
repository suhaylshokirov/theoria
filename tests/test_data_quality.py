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
    _TV_ENTITIES,
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
        "collection_id": None,
        "collection_name": None,
        "collection_poster_path": None,
        "imdb_id": "tt0137523",
        "original_title": "Fight Club",
        "homepage": None,
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
        "department": "Acting",
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
    """Minimal valid Silver DataFrames for every checked entity."""
    people_df = pd.DataFrame([
        {"person_id": 10, "name": "Alice", "gender": 1, "popularity": 20.0,
         "profile_path": "/a.jpg", "known_for_department": "Acting"},
        {"person_id": 30, "name": "Erin", "gender": 1, "popularity": 3.0,
         "profile_path": None, "known_for_department": "Editing"},
    ])
    actors_df = pd.DataFrame([{"person_id": 10, "name": "Alice", "gender": 1, "popularity": 20.0, "profile_path": "/a.jpg"}])
    directors_df = pd.DataFrame([{"person_id": 20, "name": "Carol", "gender": 1, "popularity": 30.0, "profile_path": "/c.jpg"}])
    genres_df = pd.DataFrame([{"genre_id": 28, "genre_name": "Action"}])
    companies_df = pd.DataFrame([{
        "movie_id": 550, "company_id": 711, "company_name": "Fox 2000 Pictures",
        "logo_path": "/logo.png", "origin_country": "US",
    }])
    countries_df = pd.DataFrame([
        {"movie_id": 550, "country_code": "US", "country_name": "United States of America", "relation": "production"},
        {"movie_id": 550, "country_code": "US", "country_name": "United States of America", "relation": "origin"},
    ])
    languages_df = pd.DataFrame([{
        "movie_id": 550, "language_code": "en", "language_name": "English", "english_name": "English",
    }])
    imdb_ratings_df = pd.DataFrame([{
        "movie_id": 550, "imdb_id": "tt0110912", "rating": 8.9, "vote_count": 2_150_000,
    }])
    company_details_df = pd.DataFrame([
        {"company_id": 711, "description": None, "headquarters": "Los Angeles, California",
         "homepage": None, "parent_company_id": 25, "parent_company_name": "20th Century Fox"},
        {"company_id": 25, "description": "A film studio.", "headquarters": None,
         "homepage": "https://example.com", "parent_company_id": None,
         "parent_company_name": None},
    ])
    person_details_df = pd.DataFrame([
        {"person_id": 10, "biography": "An actor.", "birthday": dt.date(1956, 7, 9),
         "deathday": None, "place_of_birth": "Concord, California",
         "homepage": None, "imdb_id": "nm0000158"},
        {"person_id": 30, "biography": None, "birthday": None, "deathday": None,
         "place_of_birth": None, "homepage": None, "imdb_id": None},
    ])
    person_aliases_df = pd.DataFrame([
        {"person_id": 10, "alias": "Tom Hanks", "ordering": 0},
        {"person_id": 10, "alias": "Thomas Jeffrey Hanks", "ordering": 1},
    ])
    movie_videos_df = pd.DataFrame([
        {"movie_id": 550, "video_id": "533ec654c3a36854480003eb", "name": "Trailer",
         "key": "6JnN1DmbqoU", "site": "YouTube", "type": "Trailer", "official": True,
         "size": 1080, "iso_639_1": "en", "iso_3166_1": "US",
         "published_at": "2013-10-08T19:15:32.000Z"},
    ])
    return {
        "movies": _movies_df(),
        "people": people_df,
        "actors": actors_df,
        "directors": directors_df,
        "genres": genres_df,
        "credits_bridge": _bridge_df(),
        "movie_companies": companies_df,
        "company_details": company_details_df,
        "person_details": person_details_df,
        "person_aliases": person_aliases_df,
        "movie_countries": countries_df,
        "movie_languages": languages_df,
        "movie_videos": movie_videos_df,
        "imdb_ratings": imdb_ratings_df,
    }


def _tv_entity_dfs() -> dict[str, pd.DataFrame]:
    """Minimal valid Silver DataFrames for the TV entities (Task 78)."""
    return {
        "series": pd.DataFrame([{
            "series_id": 1396, "name": "Breaking Bad", "original_name": "Breaking Bad",
            "first_air_date": dt.date(2008, 1, 20), "last_air_date": dt.date(2013, 9, 29),
            "number_of_seasons": 5, "number_of_episodes": 62, "status": "Ended",
            "type": "Scripted", "in_production": False, "original_language": "en",
            "overview": "A teacher turns to crime.", "tagline": "Change the equation.",
            "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
            "homepage": "https://example.com", "imdb_id": "tt0903747",
            "vote_average": 8.9, "vote_count": 12000,
        }]),
        "series_companies": pd.DataFrame([{
            "series_id": 1396, "company_id": 11073, "company_name": "Sony Pictures Television",
            "logo_path": "/s.png", "origin_country": "US",
        }]),
        "series_countries": pd.DataFrame([
            {"series_id": 1396, "country_code": "US", "country_name": "United States of America", "relation": "production"},
            {"series_id": 1396, "country_code": "US", "country_name": "United States of America", "relation": "origin"},
        ]),
        "series_languages": pd.DataFrame([{
            "series_id": 1396, "language_code": "en", "language_name": "English", "english_name": "English",
        }]),
        "series_networks": pd.DataFrame([{
            "series_id": 1396, "network_id": 174, "network_name": "AMC",
            "logo_path": "/amc.png", "origin_country": "US",
        }]),
        "networks": pd.DataFrame([{
            "network_id": 174, "name": "AMC", "logo_path": "/amc.png", "origin_country": "US",
        }]),
        "series_genres": pd.DataFrame([
            {"series_id": 1396, "genre_id": 18},
            {"series_id": 1396, "genre_id": 80},
        ]),
        "series_credits": pd.DataFrame([
            {"series_id": 1396, "person_id": 17419, "department": "Acting", "job": "Actor",
             "character_name": "Walter White", "episode_count": 62, "ordering": 0},
            {"series_id": 1396, "person_id": 66633, "department": "Production",
             "job": "Executive Producer", "character_name": "", "episode_count": 62,
             "ordering": None},
        ]),
        "series_ratings": pd.DataFrame([{
            "series_id": 1396, "imdb_id": "tt0903747", "rating": 9.5, "vote_count": 2200000,
        }]),
        "seasons": pd.DataFrame([{
            "series_id": 1396, "season_number": 1, "season_id": 3572, "name": "Season 1",
            "air_date": dt.date(2008, 1, 20), "episode_count": 7,
            "overview": "High school chemistry teacher Walter White.", "poster_path": "/s1.jpg",
        }]),
        "episodes": pd.DataFrame([{
            "episode_id": 62085, "series_id": 1396, "season_number": 1, "episode_number": 1,
            "name": "Pilot", "air_date": dt.date(2008, 1, 20), "runtime": 58,
            "overview": "Walter White begins.", "still_path": "/e1.jpg",
            "episode_type": "standard", "production_code": "", "vote_average": 8.2,
            "vote_count": 250, "imdb_id": "tt0959621",
        }]),
        "episode_ratings": pd.DataFrame([
            {"episode_id": 62085, "source": "imdb", "rating": 8.9, "vote_count": 32000},
            {"episode_id": 62085, "source": "tmdb", "rating": 8.2, "vote_count": 250},
        ]),
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
    # TV entities are skipped unless with_tv=True, so only the movie set is read.
    assert len(load_failures) == len(ENTITY_CONFIGS) - len(_TV_ENTITIES)


def test_run_silver_checks_movie_countries_requires_relation(tmp_path):
    """relation is part of the PK/required set: an origin and a production row
    for the same country must both survive as distinct rows, and a null
    relation must fail nulls — it's the field that keeps the two meanings apart."""
    dfs = _all_entity_dfs()
    dfs["movie_countries"] = pd.DataFrame([
        {"movie_id": 550, "country_code": "US", "country_name": "United States of America", "relation": None},
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    countries_nulls = next(r for r in results if r.entity == "movie_countries" and r.check == "nulls")
    assert not countries_nulls.passed


def test_run_silver_checks_imdb_ratings_rating_out_of_range_fails(tmp_path):
    """rating is checked against IMDb's own published 0-10 scale, not a guess
    mirroring the transform — a bad value must fail the ranges check."""
    dfs = _all_entity_dfs()
    dfs["imdb_ratings"] = pd.DataFrame([{
        "movie_id": 550, "imdb_id": "tt0110912", "rating": 15.0, "vote_count": 100,
    }])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    ratings_range = next(r for r in results if r.entity == "imdb_ratings" and r.check == "ranges")
    assert not ratings_range.passed


def test_run_silver_checks_company_details_only_company_id_is_required(tmp_path):
    """description/headquarters/homepage/parent are all sparse on TMDB (~1-53%
    coverage), so a company row with every one of them null must still pass —
    only company_id is required (Task 65)."""
    dfs = _all_entity_dfs()
    dfs["company_details"] = pd.DataFrame([{
        "company_id": 711, "description": None, "headquarters": None,
        "homepage": None, "parent_company_id": None, "parent_company_name": None,
    }])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    company_checks = [r for r in results if r.entity == "company_details"]
    assert company_checks and all(r.passed for r in company_checks)


def test_run_silver_checks_person_details_only_person_id_is_required(tmp_path):
    """biography/birthday/deathday/place_of_birth/homepage/imdb_id are all
    sparse even among people with a photo, so a person row with every one of
    them null must still pass — only person_id is required (Task 72)."""
    dfs = _all_entity_dfs()
    dfs["person_details"] = pd.DataFrame([{
        "person_id": 10, "biography": None, "birthday": None, "deathday": None,
        "place_of_birth": None, "homepage": None, "imdb_id": None,
    }])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    person_checks = [r for r in results if r.entity == "person_details"]
    assert person_checks and all(r.passed for r in person_checks)


def test_run_silver_checks_person_aliases_negative_ordering_fails(tmp_path):
    """ordering is the alias's index in also_known_as, so it is >= 0 (Task 72)."""
    dfs = _all_entity_dfs()
    dfs["person_aliases"] = pd.DataFrame([
        {"person_id": 10, "alias": "Tom Hanks", "ordering": -1},
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    aliases_range = next(
        r for r in results if r.entity == "person_aliases" and r.check == "ranges"
    )
    assert not aliases_range.passed


def test_run_silver_checks_movie_videos_negative_size_fails(tmp_path):
    """size is a video resolution (1080/720/...), never negative (Task 73)."""
    dfs = _all_entity_dfs()
    dfs["movie_videos"] = pd.DataFrame([
        {"movie_id": 550, "video_id": "abc", "name": "T", "key": "k", "site": "YouTube",
         "type": "Trailer", "official": True, "size": -1, "iso_639_1": "en",
         "iso_3166_1": "US", "published_at": "2013-10-08T19:15:32.000Z"},
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    videos_range = next(
        r for r in results if r.entity == "movie_videos" and r.check == "ranges"
    )
    assert not videos_range.passed


def test_run_silver_checks_movie_videos_empty_partition_passes(tmp_path):
    """Every movie_details payload written before Task 73 has no videos key, so
    that partition's movie_videos Parquet is legitimately empty — an empty but
    well-formed frame must pass every check, not fail nulls/schema."""
    dfs = _all_entity_dfs()
    dfs["movie_videos"] = pd.DataFrame(
        {c: pd.Series(dtype="object")
         for c in ENTITY_CONFIGS["movie_videos"]["expected_cols"]}
    )
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    videos_checks = [r for r in results if r.entity == "movie_videos"]
    assert videos_checks and all(r.passed for r in videos_checks), [
        r for r in videos_checks if not r.passed
    ]


# ---------------------------------------------------------------------------
# TV Shows entities (Task 78) — only checked with with_tv=True
# ---------------------------------------------------------------------------

def test_run_silver_checks_skips_tv_entities_by_default(tmp_path):
    """A movie-only run never writes the TV partitions, so they must not be
    read — no load-failure, no result of any kind for a TV entity."""
    mock_s3 = _make_multi_entity_s3_mock(_all_entity_dfs())

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
        )

    assert not any(r.entity in _TV_ENTITIES for r in results)


def test_run_silver_checks_with_tv_all_clean_all_pass(tmp_path):
    mock_s3 = _make_multi_entity_s3_mock(_all_entity_dfs() | _tv_entity_dfs())

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
            with_tv=True,
        )

    tv_results = [r for r in results if r.entity in _TV_ENTITIES]
    assert {r.entity for r in tv_results} == set(_TV_ENTITIES)
    assert all(r.passed for r in results), [r for r in results if not r.passed]


def test_run_silver_checks_with_tv_series_null_name_fails(tmp_path):
    """name is required on the series grain — a null must fail the nulls check."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["series"] = dfs["series"].assign(name=[None])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
            with_tv=True,
        )

    series_nulls = next(r for r in results if r.entity == "series" and r.check == "nulls")
    assert not series_nulls.passed


def test_run_silver_checks_with_tv_series_genres_duplicate_grain_fails(tmp_path):
    """(series_id, genre_id) is the grain — a repeat must fail duplicates."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["series_genres"] = pd.DataFrame([
        {"series_id": 1396, "genre_id": 18},
        {"series_id": 1396, "genre_id": 18},
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
            with_tv=True,
        )

    sg_dupes = next(
        r for r in results if r.entity == "series_genres" and r.check == "duplicates"
    )
    assert not sg_dupes.passed


def test_run_silver_checks_with_tv_series_credits_multi_character_grain(tmp_path):
    """One actor, two characters in one series — both rows are valid, not a dup (Task 80)."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["series_credits"] = pd.DataFrame([
        {"series_id": 1396, "person_id": 17419, "department": "Acting", "job": "Actor",
         "character_name": "Twin A", "episode_count": 10, "ordering": 0},
        {"series_id": 1396, "person_id": 17419, "department": "Acting", "job": "Actor",
         "character_name": "Twin B", "episode_count": 4, "ordering": 0},
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
            with_tv=True,
        )

    sc_results = [r for r in results if r.entity == "series_credits"]
    assert sc_results and all(r.passed for r in sc_results)


def test_run_silver_checks_with_tv_series_credits_negative_episode_count_fails(tmp_path):
    """episode_count is a real measure, >= 0 — a negative value must fail ranges."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["series_credits"] = pd.DataFrame([
        {"series_id": 1396, "person_id": 17419, "department": "Acting", "job": "Actor",
         "character_name": "Walter White", "episode_count": -3, "ordering": 0},
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9),
            bucket="theoria-datalake",
            rejected_dir=tmp_path,
            with_tv=True,
        )

    sc_ranges = next(
        r for r in results if r.entity == "series_credits" and r.check == "ranges"
    )
    assert not sc_ranges.passed


def _episode_row(**overrides):
    base = {
        "episode_id": 1, "series_id": 1396, "season_number": 1, "episode_number": 1,
        "name": "E", "air_date": dt.date(2020, 1, 1), "runtime": 45, "overview": "o",
        "still_path": "/s.jpg", "episode_type": "standard", "production_code": "",
        "vote_average": 8.0, "vote_count": 100, "imdb_id": "tt1234567",
    }
    base.update(overrides)
    return base


def test_run_silver_checks_with_tv_episodes_duplicate_natural_grain_fails(tmp_path):
    """(series, season, episode) must be unique even when episode_id differs — the
    secondary 'grain' check catches a renumbering collision the PK check misses."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["episodes"] = pd.DataFrame([
        _episode_row(episode_id=1, episode_number=1),
        _episode_row(episode_id=2, episode_number=1),  # same (series, season, episode)
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9), bucket="theoria-datalake",
            rejected_dir=tmp_path, with_tv=True,
        )

    grain = next(r for r in results if r.entity == "episodes" and r.check == "grain")
    assert not grain.passed
    pk = next(r for r in results if r.entity == "episodes" and r.check == "duplicates")
    assert pk.passed  # episode_id is still unique


def test_run_silver_checks_with_tv_episodes_zero_episode_number_fails_ranges(tmp_path):
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["episodes"] = pd.DataFrame([_episode_row(episode_number=0)])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9), bucket="theoria-datalake",
            rejected_dir=tmp_path, with_tv=True,
        )

    ep_ranges = next(r for r in results if r.entity == "episodes" and r.check == "ranges")
    assert not ep_ranges.passed


def test_run_silver_checks_with_tv_episodes_imdb_id_is_optional(tmp_path):
    """imdb_id is backfilled onto episodes.parquet but sparse — only episodes
    that matched a tconst carry one, so a null must still pass nulls (Task 83)."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["episodes"] = pd.DataFrame([_episode_row(imdb_id=None)])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9), bucket="theoria-datalake",
            rejected_dir=tmp_path, with_tv=True,
        )

    ep_results = [r for r in results if r.entity == "episodes"]
    assert ep_results and all(r.passed for r in ep_results), [r for r in ep_results if not r.passed]


def test_run_silver_checks_with_tv_episodes_missing_imdb_id_column_fails_schema(tmp_path):
    """A --with-tv run always runs transform_imdb_ratings after transform_episodes,
    so episodes.parquet must carry the backfilled imdb_id column (Task 83)."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["episodes"] = dfs["episodes"].drop(columns=["imdb_id"])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9), bucket="theoria-datalake",
            rejected_dir=tmp_path, with_tv=True,
        )

    ep_schema = next(r for r in results if r.entity == "episodes" and r.check == "schema")
    assert not ep_schema.passed


def test_run_silver_checks_with_tv_episode_ratings_rating_out_of_range_fails(tmp_path):
    """rating is on IMDb's / TMDB's 0-10 scale — a bad value must fail ranges."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["episode_ratings"] = pd.DataFrame([
        {"episode_id": 62085, "source": "imdb", "rating": 42.0, "vote_count": 10},
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9), bucket="theoria-datalake",
            rejected_dir=tmp_path, with_tv=True,
        )

    er_ranges = next(
        r for r in results if r.entity == "episode_ratings" and r.check == "ranges"
    )
    assert not er_ranges.passed


def test_run_silver_checks_with_tv_episode_ratings_duplicate_source_grain_fails(tmp_path):
    """(episode_id, source) is the grain — two 'imdb' rows for one episode must
    fail duplicates, the same one-row-per-(entity, source) rule as films."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["episode_ratings"] = pd.DataFrame([
        {"episode_id": 62085, "source": "imdb", "rating": 8.9, "vote_count": 32000},
        {"episode_id": 62085, "source": "imdb", "rating": 9.1, "vote_count": 33000},
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9), bucket="theoria-datalake",
            rejected_dir=tmp_path, with_tv=True,
        )

    er_dupes = next(
        r for r in results if r.entity == "episode_ratings" and r.check == "duplicates"
    )
    assert not er_dupes.passed


def test_run_silver_checks_with_tv_episode_ratings_both_sources_for_one_episode_pass(tmp_path):
    """An 'imdb' and a 'tmdb' row for the same episode are two distinct facts,
    not a duplicate — both must survive (Task 83)."""
    dfs = _all_entity_dfs() | _tv_entity_dfs()
    dfs["episode_ratings"] = pd.DataFrame([
        {"episode_id": 62085, "source": "imdb", "rating": 8.9, "vote_count": 32000},
        {"episode_id": 62085, "source": "tmdb", "rating": 8.2, "vote_count": 250},
    ])
    mock_s3 = _make_multi_entity_s3_mock(dfs)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = run_silver_checks(
            ingestion_date=dt.date(2026, 9, 9), bucket="theoria-datalake",
            rejected_dir=tmp_path, with_tv=True,
        )

    er_results = [r for r in results if r.entity == "episode_ratings"]
    assert er_results and all(r.passed for r in er_results), [r for r in er_results if not r.passed]
