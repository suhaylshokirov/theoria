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

    assert len(results) == 14
    assert all(r.passed for r in results)


def test_check_fk_integrity_flags_orphans():
    mock_session = MagicMock()
    # First FK check has orphans, rest are clean.
    mock_session.execute.return_value.scalar.side_effect = [5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

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
        body.read.return_value = genre_bytes if "genres.json" in Key else parquet_bytes
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
        if "genres.json" in Key:
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
        if "genres.json" in Key:
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
        if "genres.json" in Key:
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
        if "genres.json" in Key:
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
        if "genres.json" in Key:
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
