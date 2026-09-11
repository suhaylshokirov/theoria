"""Unit tests for data_quality/warehouse_checks.py.

All tests are pure-Python / in-memory — the SQLAlchemy Session and boto3 S3
client are both mocked, so nothing here touches a real database or S3.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl import s3_utils
from data_quality.warehouse_checks import (
    CheckResult,
    _bronze_credits_file_count,
    _bronze_genre_count,
    _bronze_imdb_ratings_row_count,
    _bronze_movie_count,
    _count_orphans,
    _count_s3_objects,
    check_fact_load_sanity,
    check_fk_integrity,
    check_gold_sanity,
    check_row_count_sanity,
    run_warehouse_checks,
)


# ---------------------------------------------------------------------------
# FK integrity
# ---------------------------------------------------------------------------

def test_count_orphans_returns_scalar():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 3

    result = _count_orphans(mock_session, "fact_cast", "actor_id", "dim_actor", "actor_id")

    assert result == 3
    (stmt,), _ = mock_session.execute.call_args
    assert "LEFT JOIN dim_actor" in str(stmt)
    assert "fact_cast" in str(stmt)


def test_check_fk_integrity_all_clean_all_pass():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 0

    results = check_fk_integrity(mock_session)

    assert len(results) == 28  # 16 movie/person + 6 series bridges (79) + 2 fact_series_credit (80) + 1 fact_series_rating (81) + 3 episode grain (84)
    assert all(r.passed for r in results)


def test_check_fk_integrity_flags_orphans():
    mock_session = MagicMock()
    # First FK check has orphans, rest are clean.
    mock_session.execute.return_value.scalar.side_effect = [5] + [0] * 27

    results = check_fk_integrity(mock_session)

    assert results[0].passed is False
    assert "5 row(s)" in results[0].detail
    assert all(r.passed for r in results[1:])


# ---------------------------------------------------------------------------
# Bronze counting helpers
# ---------------------------------------------------------------------------

def test_count_s3_objects_counts_contents_excluding_prefix_marker():
    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"Contents": [
            {"Key": "bronze/movie_details/ingestion_date=2026-06-22/"},
            {"Key": "bronze/movie_details/ingestion_date=2026-06-22/1.json"},
            {"Key": "bronze/movie_details/ingestion_date=2026-06-22/2.json"},
        ]}
    ]
    mock_s3.get_paginator.return_value = paginator

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        count = _count_s3_objects("bucket", "bronze/movie_details/ingestion_date=2026-06-22/")

    assert count == 2


def test_bronze_movie_count_uses_movie_details_prefix():
    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": [{"Key": "bronze/movie_details/ingestion_date=2026-06-22/1.json"}]}]
    mock_s3.get_paginator.return_value = paginator

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        count = _bronze_movie_count("bucket", dt.date(2026, 6, 22))

    assert count == 1
    (_, kwargs) = paginator.paginate.call_args
    assert kwargs["Prefix"] == "bronze/movie_details/ingestion_date=2026-06-22/"


def test_bronze_credits_file_count_uses_credits_prefix():
    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": [
        {"Key": "bronze/credits/ingestion_date=2026-06-22/1.json"},
        {"Key": "bronze/credits/ingestion_date=2026-06-22/2.json"},
    ]}]
    mock_s3.get_paginator.return_value = paginator

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        count = _bronze_credits_file_count("bucket", dt.date(2026, 6, 22))

    assert count == 2


def test_bronze_genre_count_reads_genres_list_length():
    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError
    body = MagicMock()
    body.read.return_value = json.dumps({"genres": [{"id": 1}, {"id": 2}, {"id": 3}]}).encode()
    mock_s3.get_object.return_value = {"Body": body}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        count = _bronze_genre_count("bucket", dt.date(2026, 6, 22))

    assert count == 3


def test_bronze_genre_count_returns_zero_when_missing():
    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError
    mock_s3.get_object.side_effect = KeyError("missing")

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        count = _bronze_genre_count("bucket", dt.date(2026, 6, 22))

    assert count == 0


def test_bronze_genre_count_unions_movie_and_tv_lists_without_double_counting(monkeypatch):
    """Task 85: with_tv is unconditional now, so genres_tv.json always exists
    beside genres.json. 8 ids are shared between the two real TMDB lists
    (measured in tasks.md) — summing the two files' lengths would overcount
    them; the check must match transform_genres' dedupe-on-id merge."""
    movie_bytes = json.dumps({"genres": [{"id": 1}, {"id": 2}, {"id": 3}]}).encode()
    tv_bytes = json.dumps({"genres": [{"id": 2}, {"id": 3}, {"id": 4}]}).encode()

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        body.read.return_value = tv_bytes if "genres_tv.json" in Key else movie_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        count = _bronze_genre_count("bucket", dt.date(2026, 9, 11))

    assert count == 4  # {1, 2, 3} | {2, 3, 4} — not 3 + 3


def test_bronze_genre_count_movie_only_when_tv_file_absent():
    """A pre-Task-85 partition has no genres_tv.json — the count stays
    movie-only rather than raising."""
    movie_bytes = json.dumps({"genres": [{"id": 1}, {"id": 2}]}).encode()

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        if "genres_tv.json" in Key:
            raise mock_s3.exceptions.NoSuchKey("missing")
        body = MagicMock()
        body.read.return_value = movie_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        count = _bronze_genre_count("bucket", dt.date(2026, 6, 22))

    assert count == 2


def test_bronze_imdb_ratings_row_count_parses_gzip_tsv():
    import gzip

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError
    body = MagicMock()
    tsv = "tconst\taverageRating\tnumVotes\ntt0068646\t9.2\t2250628\ntt0468569\t9.1\t3217719\n"
    body.read.return_value = gzip.compress(tsv.encode("utf-8"))
    mock_s3.get_object.return_value = {"Body": body}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        count = _bronze_imdb_ratings_row_count("bucket", dt.date(2026, 6, 22))

    assert count == 2


def test_bronze_imdb_ratings_row_count_returns_zero_when_missing():
    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError
    mock_s3.get_object.side_effect = KeyError("missing")

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        count = _bronze_imdb_ratings_row_count("bucket", dt.date(2026, 6, 22))

    assert count == 0


# ---------------------------------------------------------------------------
# Row-count sanity — dimension entities
# ---------------------------------------------------------------------------

def _silver_movies_df(n: int) -> pd.DataFrame:
    return pd.DataFrame([{"movie_id": i, "title": f"Movie {i}"} for i in range(n)])


def _mock_s3_with_parquet(df: pd.DataFrame) -> MagicMock:
    """S3 client mock that serves `df` as Parquet for movies and an empty genre list otherwise."""
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    parquet_bytes = buf.getvalue()
    genre_bytes = json.dumps({"genres": []}).encode()

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        # "genres" also routes genres_tv.json here — _bronze_genre_count (Task
        # 85) now unions both Bronze genre files, and this fixture models a
        # partition with no TV genres.
        body.read.return_value = genre_bytes if "genres" in Key else parquet_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object
    return mock_s3


def test_check_row_count_sanity_movies_pass_when_consistent(monkeypatch):
    movies_df = _silver_movies_df(3)
    mock_s3 = _mock_s3_with_parquet(movies_df)
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": [
        {"Key": f"bronze/movie_details/ingestion_date=2026-06-22/{i}.json"} for i in range(3)
    ]}]
    mock_s3.get_paginator.return_value = paginator
    mock_s3.exceptions.NoSuchKey = KeyError

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 5  # warehouse count >= silver count

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    movie_results = [r for r in results if r.check.startswith("rowcount:movies")]
    assert len(movie_results) == 2
    assert all(r.passed for r in movie_results)


def test_check_row_count_sanity_fails_when_silver_exceeds_bronze(monkeypatch):
    movies_df = _silver_movies_df(5)
    mock_s3 = _mock_s3_with_parquet(movies_df)
    paginator = MagicMock()
    # Only 2 Bronze files, but Silver has 5 rows.
    paginator.paginate.return_value = [{"Contents": [
        {"Key": "bronze/movie_details/ingestion_date=2026-06-22/0.json"},
        {"Key": "bronze/movie_details/ingestion_date=2026-06-22/1.json"},
    ]}]
    mock_s3.get_paginator.return_value = paginator
    mock_s3.exceptions.NoSuchKey = KeyError

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 10

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    b2s = next(r for r in results if r.check == "rowcount:movies:bronze_to_silver")
    assert b2s.passed is False
    assert "5 row(s)" in b2s.detail


def test_check_row_count_sanity_fails_when_warehouse_shrinks(monkeypatch):
    movies_df = _silver_movies_df(3)
    mock_s3 = _mock_s3_with_parquet(movies_df)
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": [
        {"Key": f"bronze/movie_details/ingestion_date=2026-06-22/{i}.json"} for i in range(3)
    ]}]
    mock_s3.get_paginator.return_value = paginator
    mock_s3.exceptions.NoSuchKey = KeyError

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 1  # fewer than silver_count=3

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    s2w = next(r for r in results if r.check == "rowcount:movies:silver_to_warehouse")
    assert s2w.passed is False
    assert "fewer than" in s2w.detail


def test_check_row_count_sanity_companies_compares_distinct_ids_not_row_count(monkeypatch):
    """movie_companies is a link table — a studio backing 100 films is 100
    Silver rows but 1 warehouse row, so the silver_to_warehouse comparison
    must use nunique(company_id), not len(df)."""
    companies_df = pd.DataFrame([
        {"movie_id": 1, "company_id": 900, "company_name": "Studio", "logo_path": None, "origin_country": "US"},
        {"movie_id": 2, "company_id": 900, "company_name": "Studio", "logo_path": None, "origin_country": "US"},
        {"movie_id": 3, "company_id": 900, "company_name": "Studio", "logo_path": None, "origin_country": "US"},
    ])
    movies_df = _silver_movies_df(0)
    buf = io.BytesIO()
    companies_df.to_parquet(buf, engine="pyarrow", index=False)
    companies_bytes = buf.getvalue()
    buf2 = io.BytesIO()
    movies_df.to_parquet(buf2, engine="pyarrow", index=False)
    movies_bytes = buf2.getvalue()
    genre_bytes = json.dumps({"genres": []}).encode()

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        # "genres" also routes genres_tv.json here — _bronze_genre_count (Task
        # 85) now unions both Bronze genre files.
        if "genres" in Key:
            body.read.return_value = genre_bytes
        elif "movie_companies" in Key:
            body.read.return_value = companies_bytes
        else:
            body.read.return_value = movies_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    mock_s3.get_paginator.return_value = paginator

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 1  # dim_company has 1 row (>= 1 distinct)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    s2w = next(r for r in results if r.check == "rowcount:companies:silver_to_warehouse")
    assert s2w.passed is True
    assert "Silver distinct=1" in s2w.detail


def test_check_row_count_sanity_companies_fails_when_warehouse_shrinks(monkeypatch):
    companies_df = pd.DataFrame([
        {"movie_id": 1, "company_id": 900, "company_name": "Studio", "logo_path": None, "origin_country": "US"},
        {"movie_id": 2, "company_id": 901, "company_name": "Other", "logo_path": None, "origin_country": "US"},
    ])
    movies_df = _silver_movies_df(0)
    buf = io.BytesIO()
    companies_df.to_parquet(buf, engine="pyarrow", index=False)
    companies_bytes = buf.getvalue()
    buf2 = io.BytesIO()
    movies_df.to_parquet(buf2, engine="pyarrow", index=False)
    movies_bytes = buf2.getvalue()
    genre_bytes = json.dumps({"genres": []}).encode()

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        # "genres" also routes genres_tv.json here — _bronze_genre_count (Task
        # 85) now unions both Bronze genre files.
        if "genres" in Key:
            body.read.return_value = genre_bytes
        elif "movie_companies" in Key:
            body.read.return_value = companies_bytes
        else:
            body.read.return_value = movies_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    mock_s3.get_paginator.return_value = paginator

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 1  # fewer than the 2 distinct companies

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    s2w = next(r for r in results if r.check == "rowcount:companies:silver_to_warehouse")
    assert s2w.passed is False


def test_check_row_count_sanity_person_aliases_pass_and_fail(monkeypatch):
    """person_alias is one row per (person_id, alias) — same grain as Silver —
    so a healthy load has warehouse >= Silver, and Bronze here is the whole
    person_details prefix, not one date (Task 72). The companies/countries/
    languages link-table blocks run first and abort on a bad read, so they get
    just enough shape to pass quietly."""
    def to_bytes(df):
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", index=False)
        return buf.getvalue()

    movies_bytes = to_bytes(_silver_movies_df(0))
    aliases_bytes = to_bytes(pd.DataFrame([
        {"person_id": 10, "alias": "Tom Hanks", "ordering": 0},
        {"person_id": 10, "alias": "Thomas Jeffrey Hanks", "ordering": 1},
    ]))
    companies_bytes = to_bytes(pd.DataFrame([{
        "movie_id": 0, "company_id": 900, "company_name": "Studio",
        "logo_path": None, "origin_country": "US",
    }]))
    countries_bytes = to_bytes(pd.DataFrame([
        {"movie_id": 0, "country_code": "US", "country_name": "United States", "relation": "production"},
    ]))
    languages_bytes = to_bytes(pd.DataFrame([
        {"movie_id": 0, "language_code": "en", "language_name": "English", "english_name": "English"},
    ]))
    ratings_bytes = to_bytes(pd.DataFrame([
        {"movie_id": 0, "imdb_id": "tt0000000", "rating": 8.0, "vote_count": 100},
    ]))
    genre_bytes = json.dumps({"genres": []}).encode()

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        # "genres" also routes genres_tv.json here — _bronze_genre_count (Task
        # 85) now unions both Bronze genre files.
        if "genres" in Key:
            body.read.return_value = genre_bytes
        elif "person_aliases" in Key:
            body.read.return_value = aliases_bytes
        elif "movie_companies" in Key:
            body.read.return_value = companies_bytes
        elif "movie_countries" in Key:
            body.read.return_value = countries_bytes
        elif "movie_languages" in Key:
            body.read.return_value = languages_bytes
        elif "silver/imdb_ratings" in Key:
            body.read.return_value = ratings_bytes
        else:
            body.read.return_value = movies_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object
    paginator = MagicMock()
    # one Bronze person-detail file present -> the "Bronze provided nothing" guard is not tripped
    paginator.paginate.return_value = [{"Contents": [
        {"Key": "bronze/person_details/ingestion_date=2026-05-01/31.json"},
    ]}]
    mock_s3.get_paginator.return_value = paginator

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 5  # >= silver 2

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ok = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))
    assert next(r for r in ok if r.check == "rowcount:person_aliases:bronze_to_silver").passed
    assert next(r for r in ok if r.check == "rowcount:person_aliases:silver_to_warehouse").passed

    mock_session.execute.return_value.scalar.return_value = 1  # fewer than silver 2
    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        shrunk = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))
    assert not next(
        r for r in shrunk if r.check == "rowcount:person_aliases:silver_to_warehouse"
    ).passed


def test_check_row_count_sanity_countries_and_languages_compare_distinct_ids(monkeypatch):
    """movie_countries/movie_languages are link tables too — a country/language
    referenced by several films must compare against nunique(), not len(df)."""
    countries_df = pd.DataFrame([
        {"movie_id": 1, "country_code": "US", "country_name": "United States", "relation": "production"},
        {"movie_id": 2, "country_code": "US", "country_name": "United States", "relation": "production"},
        {"movie_id": 1, "country_code": "US", "country_name": "United States", "relation": "origin"},
    ])
    languages_df = pd.DataFrame([
        {"movie_id": 1, "language_code": "en", "language_name": "English", "english_name": "English"},
        {"movie_id": 2, "language_code": "en", "language_name": "English", "english_name": "English"},
    ])
    companies_df = pd.DataFrame([
        {"movie_id": 1, "company_id": 900, "company_name": "Studio", "logo_path": None, "origin_country": "US"},
    ])
    movies_df = _silver_movies_df(0)
    genre_bytes = json.dumps({"genres": []}).encode()

    def to_bytes(df):
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", index=False)
        return buf.getvalue()

    countries_bytes = to_bytes(countries_df)
    languages_bytes = to_bytes(languages_df)
    companies_bytes = to_bytes(companies_df)
    movies_bytes = to_bytes(movies_df)

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        # "genres" also routes genres_tv.json here — _bronze_genre_count (Task
        # 85) now unions both Bronze genre files.
        if "genres" in Key:
            body.read.return_value = genre_bytes
        elif "movie_countries" in Key:
            body.read.return_value = countries_bytes
        elif "movie_languages" in Key:
            body.read.return_value = languages_bytes
        elif "movie_companies" in Key:
            body.read.return_value = companies_bytes
        else:
            body.read.return_value = movies_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    mock_s3.get_paginator.return_value = paginator

    mock_session = MagicMock()
    # dim_country=1 row (>= 1 distinct code), dim_language=1 row (>= 1 distinct code).
    mock_session.execute.return_value.scalar.return_value = 1

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    country_s2w = next(r for r in results if r.check == "rowcount:countries:silver_to_warehouse")
    language_s2w = next(r for r in results if r.check == "rowcount:languages:silver_to_warehouse")
    assert country_s2w.passed is True
    assert "Silver distinct=1" in country_s2w.detail
    assert language_s2w.passed is True
    assert "Silver distinct=1" in language_s2w.detail


def test_check_row_count_sanity_imdb_ratings_pass_when_consistent():
    """fact_movie_rating also carries the tmdb rows, so its row count is
    always >= the imdb-only Silver count when the load is healthy."""
    import gzip

    movies_df = _silver_movies_df(3)
    ratings_df = pd.DataFrame([
        {"movie_id": 0, "imdb_id": "tt0000000", "rating": 8.0, "vote_count": 100},
        {"movie_id": 1, "imdb_id": "tt0000001", "rating": 7.5, "vote_count": 200},
    ])
    genre_bytes = json.dumps({"genres": []}).encode()
    ratings_tsv_gz = gzip.compress(
        "tconst\taverageRating\tnumVotes\ntt0000000\t8.0\t100\ntt0000001\t7.5\t200\n".encode("utf-8")
    )
    # The companies/countries/languages link-table checks run before the
    # imdb_ratings block and abort the whole function on a read failure —
    # give them just enough shape (the right columns) to succeed quietly.
    companies_df = pd.DataFrame([{
        "movie_id": 0, "company_id": 900, "company_name": "Studio",
        "logo_path": None, "origin_country": "US",
    }])
    countries_df = pd.DataFrame([
        {"movie_id": 0, "country_code": "US", "country_name": "United States", "relation": "production"},
    ])
    languages_df = pd.DataFrame([
        {"movie_id": 0, "language_code": "en", "language_name": "English", "english_name": "English"},
    ])

    def to_bytes(df):
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", index=False)
        return buf.getvalue()

    movies_bytes = to_bytes(movies_df)
    ratings_parquet_bytes = to_bytes(ratings_df)
    companies_bytes = to_bytes(companies_df)
    countries_bytes = to_bytes(countries_df)
    languages_bytes = to_bytes(languages_df)

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        # "genres" also routes genres_tv.json here — _bronze_genre_count (Task
        # 85) now unions both Bronze genre files.
        if "genres" in Key:
            body.read.return_value = genre_bytes
        elif "bronze/imdb_ratings" in Key:
            body.read.return_value = ratings_tsv_gz
        elif "silver/imdb_ratings" in Key:
            body.read.return_value = ratings_parquet_bytes
        elif "movie_companies" in Key:
            body.read.return_value = companies_bytes
        elif "movie_countries" in Key:
            body.read.return_value = countries_bytes
        elif "movie_languages" in Key:
            body.read.return_value = languages_bytes
        else:
            body.read.return_value = movies_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    mock_s3.get_paginator.return_value = paginator

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 5  # >= silver imdb_ratings count (2)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    b2s = next(r for r in results if r.check == "rowcount:imdb_ratings:bronze_to_silver")
    s2w = next(r for r in results if r.check == "rowcount:imdb_ratings:silver_to_warehouse")
    assert b2s.passed is True
    assert s2w.passed is True
    assert "Silver=2" in s2w.detail


def test_check_row_count_sanity_imdb_ratings_fails_when_warehouse_shrinks():
    ratings_df = pd.DataFrame([
        {"movie_id": 0, "imdb_id": "tt0000000", "rating": 8.0, "vote_count": 100},
        {"movie_id": 1, "imdb_id": "tt0000001", "rating": 7.5, "vote_count": 200},
    ])
    movies_df = _silver_movies_df(3)
    genre_bytes = json.dumps({"genres": []}).encode()
    companies_df = pd.DataFrame([{
        "movie_id": 0, "company_id": 900, "company_name": "Studio",
        "logo_path": None, "origin_country": "US",
    }])
    countries_df = pd.DataFrame([
        {"movie_id": 0, "country_code": "US", "country_name": "United States", "relation": "production"},
    ])
    languages_df = pd.DataFrame([
        {"movie_id": 0, "language_code": "en", "language_name": "English", "english_name": "English"},
    ])

    def to_bytes(df):
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", index=False)
        return buf.getvalue()

    movies_bytes = to_bytes(movies_df)
    ratings_parquet_bytes = to_bytes(ratings_df)
    companies_bytes = to_bytes(companies_df)
    countries_bytes = to_bytes(countries_df)
    languages_bytes = to_bytes(languages_df)

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        # "genres" also routes genres_tv.json here — _bronze_genre_count (Task
        # 85) now unions both Bronze genre files.
        if "genres" in Key:
            body.read.return_value = genre_bytes
        elif "bronze/imdb_ratings" in Key:
            raise mock_s3.exceptions.NoSuchKey("missing")
        elif "silver/imdb_ratings" in Key:
            body.read.return_value = ratings_parquet_bytes
        elif "movie_companies" in Key:
            body.read.return_value = companies_bytes
        elif "movie_countries" in Key:
            body.read.return_value = countries_bytes
        elif "movie_languages" in Key:
            body.read.return_value = languages_bytes
        else:
            body.read.return_value = movies_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    mock_s3.get_paginator.return_value = paginator

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 1  # fewer than silver's 2 imdb rows

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    s2w = next(r for r in results if r.check == "rowcount:imdb_ratings:silver_to_warehouse")
    assert s2w.passed is False


def _mock_s3_for_full_row_count_sanity(extra_branches: dict[str, bytes]) -> MagicMock:
    """S3 mock giving every pre-movie_videos block enough shape to pass quietly,
    plus caller-supplied branches (keyed by a substring of the S3 key)."""
    def to_bytes(df):
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", index=False)
        return buf.getvalue()

    movies_bytes = to_bytes(_silver_movies_df(3))
    branches = {
        "movie_companies": to_bytes(pd.DataFrame([{
            "movie_id": 0, "company_id": 900, "company_name": "Studio",
            "logo_path": None, "origin_country": "US",
        }])),
        "movie_countries": to_bytes(pd.DataFrame([
            {"movie_id": 0, "country_code": "US", "country_name": "United States", "relation": "production"},
        ])),
        "movie_languages": to_bytes(pd.DataFrame([
            {"movie_id": 0, "language_code": "en", "language_name": "English", "english_name": "English"},
        ])),
        "person_aliases": to_bytes(pd.DataFrame([{"person_id": 10, "alias": "A", "ordering": 0}])),
        "silver/imdb_ratings": to_bytes(pd.DataFrame([
            {"movie_id": 0, "imdb_id": "tt0", "rating": 8.0, "vote_count": 100},
        ])),
    }
    branches.update(extra_branches)
    genre_bytes = json.dumps({"genres": []}).encode()

    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        # "genres" also routes genres_tv.json here — _bronze_genre_count (Task
        # 85) now unions both Bronze genre files.
        if "genres" in Key:
            body.read.return_value = genre_bytes
        else:
            body.read.return_value = next(
                (v for k, v in branches.items() if k in Key), movies_bytes
            )
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": [
        {"Key": "bronze/person_details/ingestion_date=2026-05-01/1.json"},
    ]}]
    mock_s3.get_paginator.return_value = paginator
    return mock_s3


def _silver_videos_df(movie_ids):
    n = len(movie_ids)
    return pd.DataFrame({
        "movie_id": list(movie_ids),
        "video_id": [f"v{i}" for i in range(n)],
        "name": ["x"] * n, "key": ["k"] * n, "site": ["YouTube"] * n,
        "type": ["Trailer"] * n, "official": [True] * n, "size": [1080] * n,
        "iso_639_1": ["en"] * n, "iso_3166_1": ["US"] * n,
        "published_at": ["2016-01-01T00:00:00Z"] * n,
    })


def test_check_row_count_sanity_movie_videos_compares_distinct_movie_ids():
    """silver/movie_videos is one row per (movie_id, video_id) — a film with 8
    clips is 8 Silver rows but must compare against nunique(movie_id)."""
    videos_bytes = io.BytesIO()
    _silver_videos_df([1, 1, 1, 2]).to_parquet(videos_bytes, engine="pyarrow", index=False)
    mock_s3 = _mock_s3_for_full_row_count_sanity({"movie_videos": videos_bytes.getvalue()})

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 4  # >= 2 distinct movie_ids, > 0 loaded

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    s2w = next(r for r in results if r.check == "rowcount:movie_videos:silver_to_warehouse")
    load = next(r for r in results if r.check == "rowcount:movie_videos:load")
    assert s2w.passed is True
    assert "Silver distinct movie_id=2" in s2w.detail
    assert load.passed is True


def test_check_row_count_sanity_movie_videos_fails_when_load_produced_nothing():
    """Silver had video rows but dim_movie_video has 0 for this date — a loader
    that silently wrote nothing from real input is a bug, not clean data."""
    videos_bytes = io.BytesIO()
    _silver_videos_df([1, 2]).to_parquet(videos_bytes, engine="pyarrow", index=False)
    mock_s3 = _mock_s3_for_full_row_count_sanity({"movie_videos": videos_bytes.getvalue()})

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 0  # nothing loaded

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 6, 22))

    load = next(r for r in results if r.check == "rowcount:movie_videos:load")
    assert load.passed is False
    assert "0 row(s)" in load.detail


# --- Task 79: dim_series / dim_network row-count sanity --------------------

def _silver_series_df(n: int) -> pd.DataFrame:
    return pd.DataFrame([{"series_id": 1000 + i, "name": f"Series {i}"} for i in range(n)])


def _silver_networks_df(n: int) -> pd.DataFrame:
    return pd.DataFrame([{"network_id": 100 + i, "name": f"Net {i}"} for i in range(n)])


def _series_branches(n_series: int, n_networks: int) -> dict[str, bytes]:
    def to_bytes(df):
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", index=False)
        return buf.getvalue()
    return {
        "/series/": to_bytes(_silver_series_df(n_series)),
        "/networks/": to_bytes(_silver_networks_df(n_networks)),
    }


def test_check_row_count_sanity_series_and_networks_pass_when_consistent():
    mock_s3 = _mock_s3_for_full_row_count_sanity(_series_branches(2, 1))
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 50  # >= every silver count

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    series = next(r for r in results if r.check == "rowcount:series:silver_to_warehouse")
    networks = next(r for r in results if r.check == "rowcount:networks:silver_to_warehouse")
    assert series.passed and networks.passed


def test_check_row_count_sanity_series_fails_when_warehouse_shrinks():
    mock_s3 = _mock_s3_for_full_row_count_sanity(_series_branches(3, 1))
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 1  # < 3 series rows

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    series = next(r for r in results if r.check == "rowcount:series:silver_to_warehouse")
    assert series.passed is False
    assert "fewer than" in series.detail


def test_check_row_count_sanity_skips_series_when_no_silver_file():
    """A movie-only warehouse has no Silver series file — no series result at all."""
    mock_s3 = _mock_s3_for_full_row_count_sanity({})
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 50

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    assert not any(r.check.startswith("rowcount:series") for r in results)
    assert not any(r.check.startswith("rowcount:networks") for r in results)


# --- Task 80: fact_series_credit row-count sanity + FK -------------------

def _series_credits_branch(n_rows: int) -> dict[str, bytes]:
    df = pd.DataFrame([
        {"series_id": 1000, "person_id": 10 + i, "department": "Acting", "job": "Actor",
         "character_name": f"Role {i}", "episode_count": 5, "ordering": i}
        for i in range(n_rows)
    ])
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    return {"/series_credits/": buf.getvalue()}


def test_check_row_count_sanity_series_credits_pass_when_consistent():
    mock_s3 = _mock_s3_for_full_row_count_sanity(_series_credits_branch(4))
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 50

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    s2w = next(r for r in results if r.check == "rowcount:series_credits:silver_to_warehouse")
    load = next(r for r in results if r.check == "rowcount:series_credits:load")
    assert s2w.passed and load.passed


def test_check_row_count_sanity_series_credits_fails_when_load_produced_nothing():
    mock_s3 = _mock_s3_for_full_row_count_sanity(_series_credits_branch(4))
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 0  # cumulative + this-date both 0

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    load = next(r for r in results if r.check == "rowcount:series_credits:load")
    assert load.passed is False


def test_check_row_count_sanity_skips_series_credits_when_no_silver_file():
    mock_s3 = _mock_s3_for_full_row_count_sanity({})
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 50

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    assert not any(r.check.startswith("rowcount:series_credits") for r in results)


def test_check_fk_integrity_covers_fact_series_credit():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 0
    names = {r.check for r in check_fk_integrity(mock_session)}
    assert "fk:fact_series_credit.series_id->dim_series.series_id" in names
    assert "fk:fact_series_credit.person_id->dim_person.person_id" in names


def test_check_fk_integrity_covers_the_six_series_bridges():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 0

    names = {r.check for r in check_fk_integrity(mock_session)}
    assert "fk:bridge_series_genre.series_id->dim_series.series_id" in names
    assert "fk:bridge_series_genre.genre_id->dim_genre.genre_id" in names
    assert "fk:bridge_series_company.company_id->dim_company.company_id" in names
    assert "fk:bridge_series_country.country_code->dim_country.country_code" in names
    assert "fk:bridge_series_language.language_code->dim_language.language_code" in names
    assert "fk:bridge_series_network.network_id->dim_network.network_id" in names


# --- Task 84: dim_season / dim_episode / fact_episode_rating --------------

def test_check_fk_integrity_covers_the_episode_grain_tables():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 0

    names = {r.check for r in check_fk_integrity(mock_session)}
    assert "fk:dim_season.series_id->dim_series.series_id" in names
    assert "fk:dim_episode.series_id->dim_series.series_id" in names
    assert "fk:fact_episode_rating.episode_id->dim_episode.episode_id" in names


def _silver_episodes_df(n: int) -> pd.DataFrame:
    return pd.DataFrame([
        {"episode_id": 60000 + i, "series_id": 1000, "season_number": 1,
         "episode_number": i + 1, "name": f"E{i + 1}", "imdb_id": None}
        for i in range(n)
    ])


def _episodes_branch(n_rows: int) -> dict[str, bytes]:
    buf = io.BytesIO()
    _silver_episodes_df(n_rows).to_parquet(buf, engine="pyarrow", index=False)
    return {"/episodes/": buf.getvalue()}


def _scalar_router(default: int, *, grain: int = 0):
    """A session.execute side_effect: the (series, season, episode) natural-grain
    query returns `grain`, every other COUNT(*) returns `default`. Lets one test
    make dim_episode's row-count checks pass while steering the grain guard
    independently (a shared scalar return_value can't do both)."""
    def _exec(stmt, *args, **kwargs):
        result = MagicMock()
        if "HAVING COUNT(*) > 1" in str(stmt):
            result.scalar.return_value = grain
        else:
            result.scalar.return_value = default
        return result
    return _exec


def test_check_row_count_sanity_episodes_pass_when_consistent_and_grain_clean():
    mock_s3 = _mock_s3_for_full_row_count_sanity(_episodes_branch(4))
    mock_session = MagicMock()
    mock_session.execute.side_effect = _scalar_router(50, grain=0)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    s2w = next(r for r in results if r.check == "rowcount:episodes:silver_to_warehouse")
    load = next(r for r in results if r.check == "rowcount:episodes:load")
    grain = next(r for r in results if r.check == "grain:dim_episode")
    assert s2w.passed and load.passed and grain.passed


def test_check_row_count_sanity_episodes_fails_when_warehouse_shrinks():
    mock_s3 = _mock_s3_for_full_row_count_sanity(_episodes_branch(5))
    mock_session = MagicMock()
    mock_session.execute.side_effect = _scalar_router(2, grain=0)  # 2 < 5 episodes

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    s2w = next(r for r in results if r.check == "rowcount:episodes:silver_to_warehouse")
    assert s2w.passed is False
    assert "fewer than" in s2w.detail


def test_check_row_count_sanity_episodes_fails_when_load_produced_nothing():
    mock_s3 = _mock_s3_for_full_row_count_sanity(_episodes_branch(4))
    mock_session = MagicMock()
    mock_session.execute.side_effect = _scalar_router(0, grain=0)  # nothing loaded

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    load = next(r for r in results if r.check == "rowcount:episodes:load")
    assert load.passed is False
    assert "0 row(s)" in load.detail


def test_check_row_count_sanity_episode_grain_fails_when_natural_key_repeats():
    mock_s3 = _mock_s3_for_full_row_count_sanity(_episodes_branch(4))
    mock_session = MagicMock()
    mock_session.execute.side_effect = _scalar_router(50, grain=3)  # 3 repeated pairs

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    grain = next(r for r in results if r.check == "grain:dim_episode")
    assert grain.passed is False
    assert "repeat" in grain.detail


def test_check_row_count_sanity_skips_episodes_when_no_silver_file():
    mock_s3 = _mock_s3_for_full_row_count_sanity({})
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.return_value = 50

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_row_count_sanity(mock_session, "bucket", dt.date(2026, 9, 9))

    assert not any(r.check.startswith("rowcount:episodes") for r in results)
    assert not any(r.check == "grain:dim_episode" for r in results)


# ---------------------------------------------------------------------------
# Gold sanity
# ---------------------------------------------------------------------------

def test_check_gold_sanity_passes_when_all_datasets_non_empty():
    df = pd.DataFrame([{"genre_id": 1, "movie_count": 2}])
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    body_bytes = buf.getvalue()

    mock_s3 = MagicMock()

    def fake_get_object(Bucket, Key):
        body = MagicMock()
        body.read.return_value = body_bytes
        return {"Body": body}

    mock_s3.get_object.side_effect = fake_get_object

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_gold_sanity("bucket", dt.date(2026, 6, 22), silver_movies_count=5)

    assert len(results) == 5
    assert all(r.passed for r in results)


def test_check_gold_sanity_fails_when_dataset_missing_but_silver_had_data():
    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError
    mock_s3.get_object.side_effect = KeyError("missing")

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_gold_sanity("bucket", dt.date(2026, 6, 22), silver_movies_count=5)

    assert all(r.passed is False for r in results)


def test_check_gold_sanity_passes_when_no_data_expected():
    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError
    mock_s3.get_object.side_effect = KeyError("missing")

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        results = check_gold_sanity("bucket", dt.date(2026, 6, 22), silver_movies_count=0)

    assert all(r.passed for r in results)


# ---------------------------------------------------------------------------
# Fact load sanity
# ---------------------------------------------------------------------------

def test_check_fact_load_sanity_passes_when_facts_loaded():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.side_effect = [10, 300, 25, 15, 8, 9]

    results = check_fact_load_sanity(
        mock_session, dt.date(2026, 6, 22),
        silver_movies_count=5, silver_credit_count=280, silver_company_count=20,
        silver_country_count=12, silver_language_count=6, silver_rating_count=9,
    )

    assert all(r.passed for r in results)


def test_check_fact_load_sanity_fails_when_zero_rows_loaded_despite_silver_data():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.side_effect = [0, 0, 0, 0, 0, 0]

    results = check_fact_load_sanity(
        mock_session, dt.date(2026, 6, 22),
        silver_movies_count=5, silver_credit_count=280, silver_company_count=20,
        silver_country_count=12, silver_language_count=6, silver_rating_count=9,
    )

    assert all(r.passed is False for r in results)


def test_check_fact_load_sanity_fails_only_the_fact_table_with_no_data():
    """Each fact table is judged on its own Silver input, never on another's."""
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.side_effect = [10, 0, 25, 15, 8, 9]

    results = check_fact_load_sanity(
        mock_session, dt.date(2026, 6, 22),
        silver_movies_count=5, silver_credit_count=280, silver_company_count=20,
        silver_country_count=12, silver_language_count=6, silver_rating_count=9,
    )

    by_check = {r.check: r.passed for r in results}
    assert by_check["facts:fact_movie_metrics"] is True
    assert by_check["facts:fact_credit"] is False
    assert by_check["facts:bridge_movie_company"] is True


def test_check_fact_load_sanity_fails_when_bridge_movie_company_empty():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.side_effect = [10, 300, 0, 15, 8, 9]

    results = check_fact_load_sanity(
        mock_session, dt.date(2026, 6, 22),
        silver_movies_count=5, silver_credit_count=280, silver_company_count=20,
        silver_country_count=12, silver_language_count=6, silver_rating_count=9,
    )

    by_check = {r.check: r.passed for r in results}
    assert by_check["facts:fact_movie_metrics"] is True
    assert by_check["facts:fact_credit"] is True
    assert by_check["facts:bridge_movie_company"] is False


def test_check_fact_load_sanity_fails_when_bridge_movie_country_or_language_empty():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.side_effect = [10, 300, 25, 0, 0, 9]

    results = check_fact_load_sanity(
        mock_session, dt.date(2026, 6, 22),
        silver_movies_count=5, silver_credit_count=280, silver_company_count=20,
        silver_country_count=12, silver_language_count=6, silver_rating_count=9,
    )

    by_check = {r.check: r.passed for r in results}
    assert by_check["facts:bridge_movie_country"] is False
    assert by_check["facts:bridge_movie_language"] is False


def test_check_fact_load_sanity_fails_when_fact_movie_rating_empty():
    """fact_movie_rating is judged against both its Silver inputs: a non-empty
    movies partition alone (source='tmdb') already implies rows are expected."""
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.side_effect = [10, 300, 25, 15, 8, 0]

    results = check_fact_load_sanity(
        mock_session, dt.date(2026, 6, 22),
        silver_movies_count=5, silver_credit_count=280, silver_company_count=20,
        silver_country_count=12, silver_language_count=6, silver_rating_count=9,
    )

    by_check = {r.check: r.passed for r in results}
    assert by_check["facts:fact_movie_rating"] is False


def test_check_fact_load_sanity_passes_when_zero_rows_and_zero_silver():
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar.side_effect = [0, 0, 0, 0, 0, 0]

    results = check_fact_load_sanity(
        mock_session, dt.date(2026, 6, 22),
        silver_movies_count=0, silver_credit_count=0, silver_company_count=0,
        silver_country_count=0, silver_language_count=0, silver_rating_count=0,
    )

    assert all(r.passed for r in results)


# ---------------------------------------------------------------------------
# run_warehouse_checks — orchestration
# ---------------------------------------------------------------------------

def test_run_warehouse_checks_combines_all_check_groups(monkeypatch):
    import data_quality.warehouse_checks as warehouse_checks_module

    mock_session = MagicMock()
    monkeypatch.setattr(
        warehouse_checks_module, "get_session",
        lambda: MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock(return_value=False)),
    )
    monkeypatch.setattr(warehouse_checks_module, "check_fk_integrity",
                         lambda session: [CheckResult("fk:a", True, "ok")])
    monkeypatch.setattr(warehouse_checks_module, "check_row_count_sanity",
                         lambda session, bucket, date: [CheckResult("rowcount:a", True, "ok")])
    monkeypatch.setattr(warehouse_checks_module, "check_gold_sanity",
                         lambda bucket, date, silver_movies_count: [CheckResult("gold:a", True, "ok")])
    monkeypatch.setattr(
        warehouse_checks_module, "check_fact_load_sanity",
        lambda session, date, silver_movies_count, silver_credit_count, silver_company_count,
               silver_country_count, silver_language_count, silver_rating_count: [
            CheckResult("facts:a", True, "ok")
        ],
    )
    monkeypatch.setattr(warehouse_checks_module, "_read_silver_parquet",
                         lambda bucket, entity, date, filename: pd.DataFrame([{"x": 1}]))

    results = run_warehouse_checks(ingestion_date=dt.date(2026, 6, 22), bucket="bucket")

    checks = {r.check for r in results}
    assert checks == {"fk:a", "rowcount:a", "gold:a", "facts:a"}
    assert all(r.passed for r in results)
