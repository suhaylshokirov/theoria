"""Unit tests for the ETL layer.

These tests mock all HTTP so they never touch the network: they verify the
TMDBClient's retry/backoff and error-handling logic in isolation.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from etl import s3_utils
from etl.tmdb_client import TMDBAPIError, TMDBClient


# --- logging_config -----------------------------------------------------------

def test_setup_logging_creates_console_and_file_handlers(tmp_path):
    """setup_logging() must attach exactly two handlers and create the log file."""
    import logging
    from unittest.mock import patch as _patch

    import config as _config
    from etl.logging_config import setup_logging

    root = logging.getLogger()
    initial_count = len(root.handlers)

    with _patch.object(_config, "LOGS_DIR", tmp_path):
        setup_logging("test_script")

    added = root.handlers[initial_count:]
    assert len(added) == 2
    handler_types = {type(h).__name__ for h in added}
    assert "StreamHandler" in handler_types
    assert "RotatingFileHandler" in handler_types
    assert (tmp_path / "test_script.log").exists()

    # Clean up so other tests aren't affected by extra handlers.
    for h in added:
        root.removeHandler(h)


def _fake_response(status_code: int, json_body: dict | None = None, headers: dict | None = None):
    """Build a stand-in requests.Response with just what the client reads."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.headers = headers or {}
    resp.text = "" if json_body is None else str(json_body)
    return resp


def _client() -> TMDBClient:
    # backoff_factor=0 keeps retry tests instant (no real sleeping).
    return TMDBClient(api_key="test-key", max_retries=2, backoff_factor=0)


def test_get_success_injects_api_key():
    client = _client()
    with patch.object(client.session, "get", return_value=_fake_response(200, {"ok": True})) as mock_get:
        result = client.get("genre/movie/list")

    assert result == {"ok": True}
    # api_key must be injected into the query params.
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["api_key"] == "test-key"


def test_get_retries_on_429_then_succeeds():
    client = _client()
    responses = [_fake_response(429), _fake_response(200, {"recovered": True})]
    with patch.object(client.session, "get", side_effect=responses) as mock_get:
        result = client.get("movie/popular")

    assert result == {"recovered": True}
    assert mock_get.call_count == 2  # one retry


def test_get_raises_after_persistent_500():
    client = _client()
    with patch.object(client.session, "get", return_value=_fake_response(500)) as mock_get:
        with pytest.raises(TMDBAPIError):
            client.get("movie/123")

    # initial attempt + max_retries (2) = 3 calls
    assert mock_get.call_count == 3


def test_get_does_not_retry_on_401():
    client = _client()
    with patch.object(client.session, "get", return_value=_fake_response(401)) as mock_get:
        with pytest.raises(TMDBAPIError):
            client.get("movie/123")

    # 401 is not retryable: exactly one call.
    assert mock_get.call_count == 1


# --- s3_utils -----------------------------------------------------------------

def test_build_path_follows_convention():
    key = s3_utils.build_path("bronze", "genres", "2026-06-21", "genres.json")
    assert key == "bronze/genres/ingestion_date=2026-06-21/genres.json"


def test_build_path_accepts_date_object():
    import datetime as dt

    key = s3_utils.build_path("silver", "movies", dt.date(2026, 6, 21), "movies.parquet")
    assert key == "silver/movies/ingestion_date=2026-06-21/movies.parquet"


def test_write_json_puts_serialised_object():
    mock_client = MagicMock()
    data = {"genres": [{"id": 28, "name": "Action"}]}
    with patch.object(s3_utils, "get_s3_client", return_value=mock_client):
        uri = s3_utils.write_json("theoria-datalake", "bronze/genres/x.json", data)

    assert uri == "s3://theoria-datalake/bronze/genres/x.json"
    _, kwargs = mock_client.put_object.call_args
    assert kwargs["Bucket"] == "theoria-datalake"
    assert kwargs["Key"] == "bronze/genres/x.json"
    # Body must be the JSON-serialised payload, round-tripping back to `data`.
    import json

    assert json.loads(kwargs["Body"].decode("utf-8")) == data


def test_write_parquet_puts_dataframe():
    mock_client = MagicMock()
    df = pd.DataFrame({"movie_id": [1, 2], "title": ["A", "B"]})
    with patch.object(s3_utils, "get_s3_client", return_value=mock_client):
        uri = s3_utils.write_parquet("theoria-datalake", "silver/movies/x.parquet", df)

    assert uri == "s3://theoria-datalake/silver/movies/x.parquet"
    _, kwargs = mock_client.put_object.call_args
    assert kwargs["Key"] == "silver/movies/x.parquet"
    # Body must be readable back into the same DataFrame.
    import io

    round_tripped = pd.read_parquet(io.BytesIO(kwargs["Body"]))
    pd.testing.assert_frame_equal(round_tripped, df)


# --- ingest_genres ------------------------------------------------------------

import datetime as dt

from etl.bronze.ingest_genres import ingest_genres


def test_ingest_genres_writes_to_correct_s3_path():
    """ingest_genres() must build the right Bronze key and return the s3:// URI."""
    fake_payload = {"genres": [{"id": 28, "name": "Action"}, {"id": 12, "name": "Adventure"}]}
    mock_client = MagicMock()
    mock_client.get_genres.return_value = fake_payload
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uri = ingest_genres(ingestion_date=dt.date(2026, 6, 22), client=mock_client)

    assert uri == "s3://theoria-datalake/bronze/genres/ingestion_date=2026-06-22/genres.json"
    mock_client.get_genres.assert_called_once()
    mock_s3.put_object.assert_called_once()


def test_ingest_genres_returns_correct_genre_count():
    """ingest_genres() must write the full payload including all genres."""
    import json

    genres = [{"id": i, "name": f"Genre{i}"} for i in range(19)]
    fake_payload = {"genres": genres}
    mock_client = MagicMock()
    mock_client.get_genres.return_value = fake_payload
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ingest_genres(ingestion_date=dt.date(2026, 6, 22), client=mock_client)

    _, kwargs = mock_s3.put_object.call_args
    written = json.loads(kwargs["Body"].decode("utf-8"))
    assert len(written["genres"]) == 19


# --- ingest_movies ------------------------------------------------------------

from etl.bronze.ingest_movies import ingest_movies


def _movie_page(page: int, ids: list[int]) -> dict:
    """Build a minimal TMDB popular-movies page payload."""
    return {"page": page, "results": [{"id": mid, "title": f"Movie {mid}"} for mid in ids]}


def test_ingest_movies_writes_one_file_per_page():
    """Each page must land in its own S3 key with zero-padded page number."""
    mock_client = MagicMock()
    mock_client.get_popular_movies.side_effect = [
        _movie_page(1, [10, 20]),
        _movie_page(2, [30, 40]),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ingest_movies(ingestion_date=dt.date(2026, 6, 22), client=mock_client, max_pages=2)

    assert mock_s3.put_object.call_count == 2
    keys_written = [call[1]["Key"] for call in mock_s3.put_object.call_args_list]
    assert "bronze/movies/ingestion_date=2026-06-22/page_0001.json" in keys_written
    assert "bronze/movies/ingestion_date=2026-06-22/page_0002.json" in keys_written


def test_ingest_movies_returns_all_movie_ids():
    """movie_ids from every page must be collected and returned."""
    mock_client = MagicMock()
    mock_client.get_popular_movies.side_effect = [
        _movie_page(1, [1, 2, 3]),
        _movie_page(2, [4, 5, 6]),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ids = ingest_movies(ingestion_date=dt.date(2026, 6, 22), client=mock_client, max_pages=2)

    assert ids == [1, 2, 3, 4, 5, 6]


def test_ingest_movies_partial_failure_does_not_lose_written_pages():
    """A failure on page 2 must not roll back page 1 already written to S3."""
    mock_client = MagicMock()
    mock_client.get_popular_movies.side_effect = [
        _movie_page(1, [10, 20]),
        RuntimeError("network blip"),
        _movie_page(3, [50, 60]),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ids = ingest_movies(ingestion_date=dt.date(2026, 6, 22), client=mock_client, max_pages=3)

    # 2 pages written (1 and 3), page 2 failed and was skipped
    assert mock_s3.put_object.call_count == 2
    # IDs from the two successful pages are still returned
    assert ids == [10, 20, 50, 60]


# --- ingest_discover ----------------------------------------------------------

from etl.bronze.ingest_discover import ingest_discover


def _discover_page(page: int, ids: list[int], total_pages: int = 1) -> dict:
    return {
        "page": page,
        "total_pages": total_pages,
        "results": [{"id": mid, "title": f"Movie {mid}"} for mid in ids],
    }


def test_discover_movies_sends_year_and_vote_filters():
    """discover_movies() must translate its args into TMDB query params."""
    client = _client()
    with patch.object(
        client.session, "get", return_value=_fake_response(200, {"results": []})
    ) as mock_get:
        client.discover_movies(page=2, release_year=1994, min_votes=300)

    _, kwargs = mock_get.call_args
    params = kwargs["params"]
    assert params["primary_release_year"] == 1994
    assert params["vote_count.gte"] == 300
    assert params["page"] == 2
    assert params["sort_by"] == "vote_count.desc"


def test_ingest_discover_writes_one_file_per_year_and_page():
    """Each year's page must land under its own year= prefix."""
    mock_client = MagicMock()
    mock_client.discover_movies.side_effect = [
        _discover_page(1, [10, 20]),
        _discover_page(1, [30, 40]),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ingest_discover(
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
            start_year=1994,
            end_year=1995,
            pages_per_year=1,
        )

    keys = [call[1]["Key"] for call in mock_s3.put_object.call_args_list]
    assert "bronze/discover/ingestion_date=2026-06-22/year=1994/page_0001.json" in keys
    assert "bronze/discover/ingestion_date=2026-06-22/year=1995/page_0001.json" in keys


def test_ingest_discover_deduplicates_ids_across_years():
    """A film returned for two years must appear once in the returned list."""
    mock_client = MagicMock()
    mock_client.discover_movies.side_effect = [
        _discover_page(1, [1, 2]),
        _discover_page(1, [2, 3]),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ids = ingest_discover(
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
            start_year=1994,
            end_year=1995,
            pages_per_year=1,
        )

    assert ids == [1, 2, 3]


def test_ingest_discover_continues_after_a_failed_year():
    """One year raising must not lose the years already written."""
    mock_client = MagicMock()
    mock_client.discover_movies.side_effect = [
        _discover_page(1, [10]),
        TMDBAPIError("boom"),
        _discover_page(1, [30]),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ids = ingest_discover(
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
            start_year=1994,
            end_year=1996,
            pages_per_year=1,
        )

    assert mock_s3.put_object.call_count == 2
    assert ids == [10, 30]


def test_ingest_discover_stops_early_when_year_has_no_more_pages():
    """total_pages=1 must stop the crawl rather than request empty page 2."""
    mock_client = MagicMock()
    mock_client.discover_movies.side_effect = [_discover_page(1, [10], total_pages=1)]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ingest_discover(
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
            start_year=1994,
            end_year=1994,
            pages_per_year=5,
        )

    assert mock_client.discover_movies.call_count == 1


# --- ingest_movie_details -----------------------------------------------------

from etl.bronze.ingest_movie_details import ingest_movie_details


def _movie_detail(movie_id: int) -> dict:
    """Build a minimal TMDB movie-detail payload."""
    return {"id": movie_id, "title": f"Movie {movie_id}", "runtime": 120}


def test_ingest_movie_details_writes_one_file_per_movie():
    """Each movie_id must land in its own S3 key named <movie_id>.json."""
    mock_client = MagicMock()
    mock_client.get_movie_details.side_effect = [
        _movie_detail(550),
        _movie_detail(551),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        succeeded, failed = ingest_movie_details(
            movie_ids=[550, 551],
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
        )

    assert succeeded == [550, 551]
    assert failed == []
    assert mock_s3.put_object.call_count == 2
    keys_written = [call[1]["Key"] for call in mock_s3.put_object.call_args_list]
    assert "bronze/movie_details/ingestion_date=2026-06-22/550.json" in keys_written
    assert "bronze/movie_details/ingestion_date=2026-06-22/551.json" in keys_written


def test_ingest_movie_details_logs_failed_movie_id_and_continues():
    """A failed movie_id must be recorded in failed list; successes still write."""
    mock_client = MagicMock()
    mock_client.get_movie_details.side_effect = [
        _movie_detail(100),
        RuntimeError("404 not found"),
        _movie_detail(300),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        succeeded, failed = ingest_movie_details(
            movie_ids=[100, 200, 300],
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
        )

    assert succeeded == [100, 300]
    assert failed == [200]
    # Only 2 S3 writes — the failed movie must not produce a partial file.
    assert mock_s3.put_object.call_count == 2


def test_ingest_movie_details_empty_input_returns_empty_lists():
    """Calling with an empty movie_ids list must succeed with no S3 calls."""
    mock_client = MagicMock()
    mock_s3 = MagicMock()

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        succeeded, failed = ingest_movie_details(
            movie_ids=[],
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
        )

    assert succeeded == []
    assert failed == []
    mock_s3.put_object.assert_not_called()


# --- ingest_credits -----------------------------------------------------------

from etl.bronze.ingest_credits import ingest_credits


def _credits_payload(movie_id: int) -> dict:
    """Build a minimal TMDB credits payload."""
    return {
        "id": movie_id,
        "cast": [{"id": 1, "name": "Actor A", "order": 0}],
        "crew": [{"id": 2, "name": "Director B", "job": "Director"}],
    }


def test_ingest_credits_writes_one_file_per_movie():
    """Each movie_id must land in its own S3 key named <movie_id>.json."""
    mock_client = MagicMock()
    mock_client.get_movie_credits.side_effect = [
        _credits_payload(550),
        _credits_payload(551),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        succeeded, failed = ingest_credits(
            movie_ids=[550, 551],
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
        )

    assert succeeded == [550, 551]
    assert failed == []
    assert mock_s3.put_object.call_count == 2
    keys_written = [call[1]["Key"] for call in mock_s3.put_object.call_args_list]
    assert "bronze/credits/ingestion_date=2026-06-22/550.json" in keys_written
    assert "bronze/credits/ingestion_date=2026-06-22/551.json" in keys_written


def test_ingest_credits_logs_failed_movie_id_and_continues():
    """A failed movie_id must appear in failed list; successes still write."""
    mock_client = MagicMock()
    mock_client.get_movie_credits.side_effect = [
        _credits_payload(100),
        RuntimeError("connection timeout"),
        _credits_payload(300),
    ]
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        succeeded, failed = ingest_credits(
            movie_ids=[100, 200, 300],
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
        )

    assert succeeded == [100, 300]
    assert failed == [200]
    assert mock_s3.put_object.call_count == 2


def test_ingest_credits_empty_input_returns_empty_lists():
    """Calling with an empty movie_ids list must succeed with no S3 calls."""
    mock_client = MagicMock()
    mock_s3 = MagicMock()

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        succeeded, failed = ingest_credits(
            movie_ids=[],
            ingestion_date=dt.date(2026, 6, 22),
            client=mock_client,
        )

    assert succeeded == []
    assert failed == []
    mock_s3.put_object.assert_not_called()


# --- transform_movies ---------------------------------------------------------

import io

from etl.silver.transform_movies import _cast_types, _flatten_movie, transform_movies


def _raw_movie(movie_id: int, **overrides) -> dict:
    """Minimal TMDB movie-detail payload for testing."""
    base = {
        "id": movie_id,
        "title": f"Movie {movie_id}",
        "release_date": "2020-01-15",
        "runtime": 120,
        "budget": 1_000_000,
        "revenue": 5_000_000,
        "original_language": "en",
        "status": "Released",
        "vote_average": 7.5,
        "vote_count": 300,
        "popularity": 42.0,
        "overview": "A test movie.",
        "tagline": "A test tagline.",
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "imdb_id": "tt0000550",
        "original_title": f"Movie {movie_id}",
        "homepage": "https://example.com/movie",
        "genres": [{"id": 28, "name": "Action"}, {"id": 12, "name": "Adventure"}],
    }
    base.update(overrides)
    return base


def _make_s3_mock_with_files(payloads: dict[str, dict]) -> MagicMock:
    """Build an S3 mock whose list_objects_v2 and get_object serve `payloads`.

    payloads: {key: json_dict}
    """
    import json

    mock_s3 = MagicMock()

    # list_objects_v2 paginator
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"Contents": [{"Key": k} for k in payloads]}
    ]
    mock_s3.get_paginator.return_value = paginator

    # get_object returns a streaming body for each key
    def get_object(Bucket, Key):
        body = MagicMock()
        body.read.return_value = json.dumps(payloads[Key]).encode("utf-8")
        return {"Body": body}

    mock_s3.get_object.side_effect = get_object
    mock_s3.put_object.return_value = {}
    return mock_s3


def test_flatten_movie_extracts_genre_ids():
    raw = _raw_movie(550)
    row = _flatten_movie(raw)
    assert row["movie_id"] == 550
    assert row["title"] == "Movie 550"
    assert row["genre_ids"] == [28, 12]
    assert row["release_date"] == "2020-01-15"


def test_flatten_movie_carries_image_fields():
    """Task 36: poster/backdrop/tagline flow through from Bronze, empty strings become None."""
    row = _flatten_movie(_raw_movie(550))
    assert row["poster_path"] == "/poster.jpg"
    assert row["backdrop_path"] == "/backdrop.jpg"
    assert row["tagline"] == "A test tagline."
    empty = _flatten_movie(_raw_movie(1, poster_path="", tagline=""))
    assert empty["poster_path"] is None
    assert empty["tagline"] is None


def test_flatten_movie_carries_identifier_fields():
    """Task 55: imdb_id/original_title/homepage flow through, empty strings become None."""
    row = _flatten_movie(_raw_movie(550))
    assert row["imdb_id"] == "tt0000550"
    assert row["original_title"] == "Movie 550"
    assert row["homepage"] == "https://example.com/movie"
    empty = _flatten_movie(_raw_movie(1, imdb_id="", homepage=""))
    assert empty["imdb_id"] is None
    assert empty["homepage"] is None


def test_flatten_movie_handles_missing_release_date():
    raw = _raw_movie(1, release_date="")
    row = _flatten_movie(raw)
    assert row["release_date"] is None


def test_cast_types_converts_numerics_and_date():
    df = pd.DataFrame([_flatten_movie(_raw_movie(550))])
    df = _cast_types(df)
    assert df["movie_id"].dtype.name == "Int64"
    assert df["runtime"].dtype.name == "Int64"
    assert df["vote_average"].dtype == float
    import datetime
    assert isinstance(df["release_date"].iloc[0], datetime.date)


def test_cast_types_coerces_bad_values_to_null():
    raw = _raw_movie(1, runtime="not-a-number", budget=None)
    df = pd.DataFrame([_flatten_movie(raw)])
    df = _cast_types(df)
    assert pd.isna(df["runtime"].iloc[0])
    assert pd.isna(df["budget"].iloc[0])


def test_transform_movies_writes_silver_parquet():
    """transform_movies must read Bronze JSON and write a Silver Parquet file."""
    key = "bronze/movie_details/ingestion_date=2026-06-22/550.json"
    mock_s3 = _make_s3_mock_with_files({key: _raw_movie(550)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uri = transform_movies(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
        )

    assert uri == "s3://theoria-datalake/silver/movies/ingestion_date=2026-06-22/movies.parquet"
    mock_s3.put_object.assert_called_once()
    _, kwargs = mock_s3.put_object.call_args
    assert kwargs["Key"] == "silver/movies/ingestion_date=2026-06-22/movies.parquet"
    # Verify the Parquet round-trip contains our movie.
    df_out = pd.read_parquet(io.BytesIO(kwargs["Body"]))
    assert len(df_out) == 1
    assert df_out["movie_id"].iloc[0] == 550


def test_transform_movies_deduplicates_on_movie_id():
    """Duplicate movie_ids across Bronze files must be reduced to one row each."""
    key1 = "bronze/movie_details/ingestion_date=2026-06-22/550.json"
    key2 = "bronze/movie_details/ingestion_date=2026-06-22/550_dup.json"
    mock_s3 = _make_s3_mock_with_files({
        key1: _raw_movie(550, title="Original"),
        key2: _raw_movie(550, title="Duplicate"),
    })

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        transform_movies(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")

    _, kwargs = mock_s3.put_object.call_args
    df_out = pd.read_parquet(io.BytesIO(kwargs["Body"]))
    assert len(df_out) == 1


def test_transform_movies_raises_when_no_bronze_files():
    """FileNotFoundError must be raised when no Bronze files exist for the date."""
    import pytest

    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    mock_s3.get_paginator.return_value = paginator

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with pytest.raises(FileNotFoundError):
            transform_movies(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")


# --- transform_people ---------------------------------------------------------

from etl.silver.transform_people import (
    _cast_people_types,
    _extract_people,
    transform_people,
)


def _raw_credits(movie_id: int, extra_cast: list | None = None, extra_crew: list | None = None) -> dict:
    """Minimal TMDB credits payload for testing."""
    cast = [
        {"id": 10, "name": "Alice", "gender": 1, "popularity": 20.0, "profile_path": "/alice.jpg"},
        {"id": 11, "name": "Bob", "gender": 2, "popularity": 15.0, "profile_path": "/bob.jpg"},
    ]
    crew = [
        {"id": 20, "name": "Carol", "job": "Director", "department": "Directing",
         "gender": 1, "popularity": 30.0, "profile_path": "/carol.jpg",
         "known_for_department": "Directing"},
        {"id": 21, "name": "Dave", "job": "Producer", "department": "Production",
         "gender": 2, "popularity": 5.0},
    ]
    if extra_cast:
        cast.extend(extra_cast)
    if extra_crew:
        crew.extend(extra_crew)
    return {"id": movie_id, "cast": cast, "crew": crew}


def test_cast_people_types_converts_numerics():
    rows = _extract_people(_raw_credits(550))
    df = pd.DataFrame(rows)
    df = _cast_people_types(df)
    assert df["person_id"].dtype.name == "Int64"
    assert df["gender"].dtype.name == "Int64"
    assert df["popularity"].dtype == float


def test_cast_people_types_coerces_bad_values_to_null():
    rows = [{"person_id": "bad", "name": "X", "gender": None, "popularity": "nope"}]
    df = pd.DataFrame(rows)
    df = _cast_people_types(df)
    assert pd.isna(df["person_id"].iloc[0])
    assert pd.isna(df["popularity"].iloc[0])


def test_extract_people_includes_every_crew_member_not_just_directors():
    """A person is a person: non-director crew must survive extraction.

    Regression guard for the pre-Phase-10 behaviour, where _extract_directors'
    job=="Director" filter silently decided who existed in the warehouse at all.
    """
    payload = _raw_credits(550)
    rows = _extract_people(payload)
    ids = {r["person_id"] for r in rows}
    # 2 cast + the director + the producer — the producer is the point.
    assert ids == {10, 11, 20, 21}
    producer = next(r for r in rows if r["person_id"] == 21)
    assert producer["name"] == "Dave"


def test_extract_people_carries_known_for_department():
    rows = _extract_people(_raw_credits(550))
    carol = next(r for r in rows if r["person_id"] == 20)
    assert carol["known_for_department"] == "Directing"


def test_transform_people_writes_one_people_parquet():
    """One dataset now: everyone with a credit, cast or crew."""
    key = "bronze/credits/ingestion_date=2026-06-22/550.json"
    mock_s3 = _make_s3_mock_with_files({key: _raw_credits(550)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        people_uri = transform_people(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
        )

    assert people_uri == "s3://theoria-datalake/silver/people/ingestion_date=2026-06-22/people.parquet"
    assert mock_s3.put_object.call_count == 1

    df = pd.read_parquet(io.BytesIO(mock_s3.put_object.call_args[1]["Body"]))
    assert set(df["person_id"]) == {10, 11, 20, 21}
    assert "known_for_department" in df.columns


def test_transform_people_raises_when_no_bronze_files():
    """FileNotFoundError must be raised when no Bronze credits files exist."""
    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    mock_s3.get_paginator.return_value = paginator

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with pytest.raises(FileNotFoundError):
            transform_people(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")


# --- transform_genres ---------------------------------------------------------

from etl.silver.transform_genres import (
    _cast_genre_types,
    _extract_genres,
    transform_genres,
)

import datetime as dt  # already imported above, but explicit for readability


def _raw_genres_payload(genres: list[dict] | None = None) -> dict:
    """Build a minimal TMDB genre-list payload."""
    if genres is None:
        genres = [
            {"id": 28, "name": "Action"},
            {"id": 12, "name": "Adventure"},
            {"id": 35, "name": "Comedy"},
        ]
    return {"genres": genres}


def _make_s3_mock_with_genre_file(payload: dict) -> MagicMock:
    """Build an S3 mock whose get_object returns the genre payload."""
    import json
    mock_s3 = MagicMock()
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode("utf-8")
    mock_s3.get_object.return_value = {"Body": body}
    mock_s3.put_object.return_value = {}
    return mock_s3


def test_extract_genres_returns_all_genres():
    payload = _raw_genres_payload()
    rows = _extract_genres(payload)
    assert len(rows) == 3
    assert rows[0] == {"genre_id": 28, "genre_name": "Action"}
    assert rows[2] == {"genre_id": 35, "genre_name": "Comedy"}


def test_extract_genres_empty_payload_returns_empty_list():
    rows = _extract_genres({"genres": []})
    assert rows == []


def test_cast_genre_types_converts_id_to_int64():
    rows = _extract_genres(_raw_genres_payload())
    df = pd.DataFrame(rows)
    df = _cast_genre_types(df)
    assert df["genre_id"].dtype.name == "Int64"
    assert df["genre_name"].dtype.name == "string"


def test_cast_genre_types_coerces_bad_id_to_null():
    rows = [{"genre_id": "bad", "genre_name": "Unknown"}]
    df = pd.DataFrame(rows)
    df = _cast_genre_types(df)
    assert pd.isna(df["genre_id"].iloc[0])


def test_transform_genres_writes_silver_parquet():
    """transform_genres must read Bronze JSON and write a Silver Parquet file."""
    mock_s3 = _make_s3_mock_with_genre_file(_raw_genres_payload())

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uri = transform_genres(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
        )

    assert uri == "s3://theoria-datalake/silver/genres/ingestion_date=2026-06-22/genres.parquet"
    mock_s3.put_object.assert_called_once()
    _, kwargs = mock_s3.put_object.call_args
    assert kwargs["Key"] == "silver/genres/ingestion_date=2026-06-22/genres.parquet"
    df_out = pd.read_parquet(io.BytesIO(kwargs["Body"]))
    assert len(df_out) == 3
    assert set(df_out["genre_id"].tolist()) == {28, 12, 35}


def test_transform_genres_deduplicates_on_genre_id():
    """Duplicate genre_ids in the Bronze payload must collapse to one row each."""
    payload = _raw_genres_payload([
        {"id": 28, "name": "Action"},
        {"id": 28, "name": "Action (dup)"},
        {"id": 12, "name": "Adventure"},
    ])
    mock_s3 = _make_s3_mock_with_genre_file(payload)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        transform_genres(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")

    _, kwargs = mock_s3.put_object.call_args
    df_out = pd.read_parquet(io.BytesIO(kwargs["Body"]))
    assert len(df_out) == 2


def test_transform_genres_raises_when_no_bronze_file():
    """FileNotFoundError must be raised when no Bronze genre file exists."""
    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey = Exception("NoSuchKey")
    mock_s3.exceptions.NoSuchKey = Exception

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with pytest.raises(Exception):
            transform_genres(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")


# --- transform_credits_bridge -------------------------------------------------

from etl.silver.transform_credits_bridge import (
    _cast_bridge_types,
    _extract_bridge_rows,
    transform_credits_bridge,
)


def _raw_credits_with_movie_id(movie_id: int) -> dict:
    """Minimal TMDB credits payload with movie_id in root."""
    return {
        "id": movie_id,
        "cast": [
            {"id": 10, "name": "Alice", "character": "Hero", "order": 0},
            {"id": 11, "name": "Bob", "character": "Villain", "order": 1},
        ],
        "crew": [
            {"id": 20, "name": "Carol", "job": "Director", "department": "Directing"},
            {"id": 21, "name": "Dave", "job": "Producer", "department": "Production"},
        ],
    }


def test_extract_bridge_rows_returns_cast_and_crew():
    payload = _raw_credits_with_movie_id(550)
    rows = _extract_bridge_rows(payload)
    # 2 cast + 2 crew = 4 rows
    assert len(rows) == 4
    cast_rows = [r for r in rows if r["credit_type"] == "cast"]
    crew_rows = [r for r in rows if r["credit_type"] == "crew"]
    assert len(cast_rows) == 2
    assert len(crew_rows) == 2


def test_extract_bridge_rows_sets_movie_id_from_payload():
    payload = _raw_credits_with_movie_id(999)
    rows = _extract_bridge_rows(payload)
    assert all(r["movie_id"] == 999 for r in rows)


def test_extract_bridge_rows_cast_has_ordering_crew_has_none():
    payload = _raw_credits_with_movie_id(550)
    rows = _extract_bridge_rows(payload)
    cast_row = next(r for r in rows if r["credit_type"] == "cast")
    crew_row = next(r for r in rows if r["credit_type"] == "crew")
    assert cast_row["ordering"] == 0
    assert crew_row["ordering"] is None


def test_cast_bridge_types_converts_numerics():
    payload = _raw_credits_with_movie_id(550)
    rows = _extract_bridge_rows(payload)
    df = pd.DataFrame(rows)
    df = _cast_bridge_types(df)
    assert df["movie_id"].dtype.name == "Int64"
    assert df["person_id"].dtype.name == "Int64"
    assert df["ordering"].dtype.name == "Int64"
    assert df["credit_type"].dtype.name == "string"
    assert df["role"].dtype.name == "string"


def test_cast_bridge_types_coerces_bad_values_to_null():
    rows = [{"movie_id": "bad", "person_id": None, "credit_type": "cast",
             "department": "Acting", "role": "Hero", "ordering": "nope"}]
    df = pd.DataFrame(rows)
    df = _cast_bridge_types(df)
    assert pd.isna(df["movie_id"].iloc[0])
    assert pd.isna(df["person_id"].iloc[0])
    assert pd.isna(df["ordering"].iloc[0])


def test_transform_credits_bridge_writes_silver_parquet():
    """transform_credits_bridge must read Bronze JSON and write a Silver Parquet."""
    key = "bronze/credits/ingestion_date=2026-06-22/550.json"
    mock_s3 = _make_s3_mock_with_files({key: _raw_credits_with_movie_id(550)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uri = transform_credits_bridge(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
        )

    assert uri == "s3://theoria-datalake/silver/credits_bridge/ingestion_date=2026-06-22/credits_bridge.parquet"
    mock_s3.put_object.assert_called_once()
    _, kwargs = mock_s3.put_object.call_args
    assert kwargs["Key"] == "silver/credits_bridge/ingestion_date=2026-06-22/credits_bridge.parquet"
    df_out = pd.read_parquet(io.BytesIO(kwargs["Body"]))
    assert len(df_out) == 4  # 2 cast + 2 crew for movie 550
    assert set(df_out["credit_type"].tolist()) == {"cast", "crew"}


def test_transform_credits_bridge_deduplicates_on_movie_person_credit_type_role():
    """Same (movie_id, person_id, credit_type, role) across two files → one row."""
    key1 = "bronze/credits/ingestion_date=2026-06-22/550.json"
    key2 = "bronze/credits/ingestion_date=2026-06-22/550_dup.json"
    mock_s3 = _make_s3_mock_with_files({
        key1: _raw_credits_with_movie_id(550),
        key2: _raw_credits_with_movie_id(550),
    })

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        transform_credits_bridge(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")

    _, kwargs = mock_s3.put_object.call_args
    df_out = pd.read_parquet(io.BytesIO(kwargs["Body"]))
    assert len(df_out) == 4  # still 4, not 8


def test_transform_credits_bridge_keeps_every_job_for_a_multi_role_crew_member():
    """A director who also produced the same film must keep BOTH crew rows.

    Regression test for the dedup-grain bug: with the key
    (movie_id, person_id, credit_type) the two crew rows for person 20 collapse
    to whichever sorts last, silently destroying the "Director" role — which is
    what left 52 of 99 movies with no director in the warehouse.
    """
    payload = {
        "id": 550,
        "cast": [],
        "crew": [
            {"id": 20, "name": "Carol", "job": "Director", "department": "Directing"},
            {"id": 20, "name": "Carol", "job": "Producer", "department": "Production"},
        ],
    }
    key = "bronze/credits/ingestion_date=2026-06-22/550.json"
    mock_s3 = _make_s3_mock_with_files({key: payload})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        transform_credits_bridge(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")

    _, kwargs = mock_s3.put_object.call_args
    df_out = pd.read_parquet(io.BytesIO(kwargs["Body"]))

    assert len(df_out) == 2
    assert set(df_out["role"]) == {"Director", "Producer"}


def test_transform_credits_bridge_flags_orphan_movie_ids(caplog):
    """Rows whose movie_id is not in known_movie_ids must be logged as orphans."""
    import logging
    key = "bronze/credits/ingestion_date=2026-06-22/550.json"
    mock_s3 = _make_s3_mock_with_files({key: _raw_credits_with_movie_id(550)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with caplog.at_level(logging.WARNING):
            transform_credits_bridge(
                ingestion_date=dt.date(2026, 6, 22),
                bucket="theoria-datalake",
                known_movie_ids={999},  # 550 is NOT in the known set
            )

    assert any("unknown movie_id" in record.message for record in caplog.records)


def test_transform_credits_bridge_raises_when_no_bronze_files():
    """FileNotFoundError must be raised when no Bronze credits files exist."""
    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    mock_s3.get_paginator.return_value = paginator

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with pytest.raises(FileNotFoundError):
            transform_credits_bridge(
                ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake"
            )


# --- transform_movie_links -----------------------------------------------------

from etl.silver.transform_movie_links import (
    _extract_company_rows,
    _extract_country_rows,
    _extract_language_rows,
    transform_movie_links,
)


def _raw_movie_with_links(movie_id: int, **overrides) -> dict:
    """A movie-detail payload carrying production_companies/countries and languages."""
    raw = _raw_movie(
        movie_id,
        production_companies=[
            {"id": 711, "name": "Fox 2000 Pictures", "logo_path": "/logo.png", "origin_country": "US"},
            {"id": 508, "name": "Regency Enterprises", "logo_path": None, "origin_country": "US"},
        ],
        production_countries=[
            {"iso_3166_1": "US", "name": "United States of America"},
        ],
        origin_country=["US"],
        spoken_languages=[
            {"iso_639_1": "en", "name": "English", "english_name": "English"},
        ],
    )
    raw.update(overrides)
    return raw


def test_extract_company_rows_one_row_per_company():
    raw = _raw_movie_with_links(550)
    rows = _extract_company_rows(raw)
    assert len(rows) == 2
    assert {r["company_id"] for r in rows} == {711, 508}
    assert all(r["movie_id"] == 550 for r in rows)
    assert rows[1]["logo_path"] is None  # TMDB null passes through as None


def test_extract_company_rows_empty_when_absent():
    raw = _raw_movie(550)  # no production_companies key
    assert _extract_company_rows(raw) == []


def test_extract_country_rows_tags_relation_and_fills_origin_name_from_production():
    raw = _raw_movie_with_links(550)
    rows = _extract_country_rows(raw)
    assert len(rows) == 2
    production = next(r for r in rows if r["relation"] == "production")
    origin = next(r for r in rows if r["relation"] == "origin")
    assert production["country_name"] == "United States of America"
    # origin_country only carries a code; the name is backfilled from the
    # production_countries row for the same code in the same payload.
    assert origin["country_name"] == "United States of America"


def test_extract_country_rows_leaves_origin_name_null_when_no_match():
    """An origin country with no matching production_countries entry keeps a
    null name rather than guessing — Bond films et al. disagree on this ~23%
    of the time, so no name is safer than a wrong one."""
    raw = _raw_movie_with_links(
        550,
        production_countries=[{"iso_3166_1": "GB", "name": "United Kingdom"}],
        origin_country=["US"],
    )
    rows = _extract_country_rows(raw)
    origin = next(r for r in rows if r["relation"] == "origin")
    assert origin["country_code"] == "US"
    assert origin["country_name"] is None


def test_extract_language_rows_one_row_per_language():
    raw = _raw_movie_with_links(
        550,
        spoken_languages=[
            {"iso_639_1": "en", "name": "English", "english_name": "English"},
            {"iso_639_1": "fr", "name": "Français", "english_name": "French"},
        ],
    )
    rows = _extract_language_rows(raw)
    assert len(rows) == 2
    assert {r["language_code"] for r in rows} == {"en", "fr"}


def test_transform_movie_links_writes_three_silver_files():
    key = "bronze/movie_details/ingestion_date=2026-06-22/550.json"
    mock_s3 = _make_s3_mock_with_files({key: _raw_movie_with_links(550)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        companies_uri, countries_uri, languages_uri = transform_movie_links(
            ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake"
        )

    assert companies_uri == "s3://theoria-datalake/silver/movie_companies/ingestion_date=2026-06-22/movie_companies.parquet"
    assert countries_uri == "s3://theoria-datalake/silver/movie_countries/ingestion_date=2026-06-22/movie_countries.parquet"
    assert languages_uri == "s3://theoria-datalake/silver/movie_languages/ingestion_date=2026-06-22/movie_languages.parquet"
    assert mock_s3.put_object.call_count == 3

    written = {}
    for call in mock_s3.put_object.call_args_list:
        _, kwargs = call
        written[kwargs["Key"]] = pd.read_parquet(io.BytesIO(kwargs["Body"]))

    companies_df = written["silver/movie_companies/ingestion_date=2026-06-22/movie_companies.parquet"]
    countries_df = written["silver/movie_countries/ingestion_date=2026-06-22/movie_countries.parquet"]
    languages_df = written["silver/movie_languages/ingestion_date=2026-06-22/movie_languages.parquet"]

    assert len(companies_df) == 2
    assert len(countries_df) == 2  # one production + one origin row
    assert set(countries_df["relation"]) == {"production", "origin"}
    assert len(languages_df) == 1


def test_transform_movie_links_deduplicates_on_true_grain():
    """Same company/country-relation/language across two files -> one row each."""
    key1 = "bronze/movie_details/ingestion_date=2026-06-22/550.json"
    key2 = "bronze/movie_details/ingestion_date=2026-06-22/550_dup.json"
    payload = _raw_movie_with_links(550)
    mock_s3 = _make_s3_mock_with_files({key1: payload, key2: dict(payload)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        transform_movie_links(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")

    written = {}
    for call in mock_s3.put_object.call_args_list:
        _, kwargs = call
        written[kwargs["Key"]] = pd.read_parquet(io.BytesIO(kwargs["Body"]))

    companies_df = written["silver/movie_companies/ingestion_date=2026-06-22/movie_companies.parquet"]
    countries_df = written["silver/movie_countries/ingestion_date=2026-06-22/movie_countries.parquet"]
    assert len(companies_df) == 2  # still 2, not 4
    assert len(countries_df) == 2  # still 2 (production + origin), not 4


def test_transform_movie_links_drops_null_id_rows_with_warning(caplog):
    import logging
    raw = _raw_movie_with_links(
        550,
        production_companies=[
            {"id": None, "name": "No ID Studio", "logo_path": None, "origin_country": None},
        ],
    )
    key = "bronze/movie_details/ingestion_date=2026-06-22/550.json"
    mock_s3 = _make_s3_mock_with_files({key: raw})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with caplog.at_level(logging.WARNING):
            transform_movie_links(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")

    assert any("null company_id" in record.message for record in caplog.records)


def test_transform_movie_links_raises_when_no_bronze_files():
    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": []}]
    mock_s3.get_paginator.return_value = paginator

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with pytest.raises(FileNotFoundError):
            transform_movie_links(
                ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake"
            )


# ---------------------------------------------------------------------------
# Gold layer: build_gold_datasets
# ---------------------------------------------------------------------------

from etl.gold.build_gold_datasets import (
    _build_actor_filmography,
    _build_collaboration_edges,
    _build_decade_stats,
    _build_director_ratings,
    _build_genre_metrics,
    _key_credits,
    build_gold_datasets,
)


# --- Fixture DataFrames ---

def _silver_movies() -> pd.DataFrame:
    return pd.DataFrame([
        {"movie_id": 1, "title": "Film A", "release_date": dt.date(1994, 1, 1),
         "vote_average": 8.0, "revenue": 100_000_000, "genre_ids": [28, 12]},
        {"movie_id": 2, "title": "Film B", "release_date": dt.date(1999, 6, 1),
         "vote_average": 7.0, "revenue": 50_000_000, "genre_ids": [28]},
        {"movie_id": 3, "title": "Film C", "release_date": dt.date(2005, 3, 15),
         "vote_average": 6.5, "revenue": 200_000_000, "genre_ids": [12]},
    ])


def _silver_genres() -> pd.DataFrame:
    return pd.DataFrame([
        {"genre_id": 28, "genre_name": "Action"},
        {"genre_id": 12, "genre_name": "Adventure"},
    ])


def _silver_people() -> pd.DataFrame:
    return pd.DataFrame([
        {"person_id": 10, "name": "Actor A", "gender": 2, "popularity": 50.0},
        {"person_id": 11, "name": "Actor B", "gender": 1, "popularity": 30.0},
        {"person_id": 20, "name": "Dir A", "gender": 2, "popularity": 40.0},
    ])


def _silver_bridge() -> pd.DataFrame:
    return pd.DataFrame([
        {"movie_id": 1, "person_id": 10, "credit_type": "cast", "role": "Hero", "ordering": 0},
        {"movie_id": 2, "person_id": 10, "credit_type": "cast", "role": "Villain", "ordering": 0},
        {"movie_id": 3, "person_id": 11, "credit_type": "cast", "role": "Lead", "ordering": 0},
        {"movie_id": 1, "person_id": 20, "credit_type": "crew", "role": "Director", "ordering": None},
        {"movie_id": 2, "person_id": 20, "credit_type": "crew", "role": "Director", "ordering": None},
    ])


def _collab_bridge() -> pd.DataFrame:
    """One film with 2 top-billed actors, 1 deep-billed actor, a director and a caterer."""
    return pd.DataFrame([
        {"movie_id": 1, "person_id": 10, "credit_type": "cast", "role": "Hero", "ordering": 0},
        {"movie_id": 1, "person_id": 11, "credit_type": "cast", "role": "Villain", "ordering": 1},
        {"movie_id": 1, "person_id": 12, "credit_type": "cast", "role": "Extra", "ordering": 40},
        {"movie_id": 1, "person_id": 20, "credit_type": "crew", "role": "Director", "ordering": None},
        {"movie_id": 1, "person_id": 30, "credit_type": "crew", "role": "Craft Service", "ordering": None},
    ])


def test_key_credits_excludes_deep_billing_and_minor_crew():
    """Only top-billed cast and principal craft roles count as collaborators."""
    key = _key_credits(_collab_bridge())

    assert set(key["person_id"]) == {10, 11, 20}


def test_build_collaboration_edges_pairs_are_canonical_and_counted_once():
    """Each pair appears once, with person_a_id < person_b_id."""
    movies = pd.DataFrame([{"movie_id": 1, "release_date": dt.date(1994, 1, 1)}])

    edges = _build_collaboration_edges(movies, _collab_bridge())

    pairs = {(int(r.person_a_id), int(r.person_b_id)) for r in edges.itertuples()}
    # 3 key people -> exactly C(3,2) = 3 pairs, never their mirror images.
    assert pairs == {(10, 11), (10, 20), (11, 20)}
    assert all(r.person_a_id < r.person_b_id for r in edges.itertuples())


def test_build_collaboration_edges_counts_repeat_pairings_and_spans_years():
    """Two people on two films are one edge with films_together=2 and a year span."""
    movies = pd.DataFrame([
        {"movie_id": 1, "release_date": dt.date(1994, 1, 1)},
        {"movie_id": 2, "release_date": dt.date(2001, 5, 1)},
    ])
    bridge = pd.DataFrame([
        {"movie_id": 1, "person_id": 10, "credit_type": "cast", "role": "Hero", "ordering": 0},
        {"movie_id": 1, "person_id": 20, "credit_type": "crew", "role": "Director", "ordering": None},
        {"movie_id": 2, "person_id": 10, "credit_type": "cast", "role": "Hero", "ordering": 0},
        {"movie_id": 2, "person_id": 20, "credit_type": "crew", "role": "Director", "ordering": None},
    ])

    edges = _build_collaboration_edges(movies, bridge)

    assert len(edges) == 1
    row = edges.iloc[0]
    assert (row["person_a_id"], row["person_b_id"]) == (10, 20)
    assert row["films_together"] == 2
    assert row["first_year"] == 1994
    assert row["last_year"] == 2001


def test_build_collaboration_edges_survives_a_film_with_no_release_date():
    """A missing year must leave first/last null, not crash or poison the count."""
    movies = pd.DataFrame([{"movie_id": 1, "release_date": None}])

    edges = _build_collaboration_edges(movies, _collab_bridge())

    assert len(edges) == 3
    assert edges["first_year"].isna().all()
    assert (edges["films_together"] == 1).all()


def _parquet_body(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    return buf.getvalue()


def _make_multi_entity_s3_mock(ingestion_date: dt.date) -> MagicMock:
    """S3 mock that returns correct Silver Parquet for each entity."""
    entities = {
        "movies": _silver_movies(),
        "people": _silver_people(),
        "genres": _silver_genres(),
        "credits_bridge": _silver_bridge(),
    }

    def fake_get_object(Bucket, Key):
        for entity, df in entities.items():
            if f"/{entity}/" in Key:
                body = MagicMock()
                body.read.return_value = _parquet_body(df)
                return {"Body": body}
        raise KeyError(f"Unrecognised key in mock: {Key}")

    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = fake_get_object
    mock_s3.put_object.return_value = {}
    mock_s3.exceptions.NoSuchKey = KeyError
    return mock_s3


# --- _build_genre_metrics ---

def test_genre_metrics_row_count_matches_unique_genres():
    """Each unique genre in the exploded movie list must produce exactly one row."""
    result = _build_genre_metrics(_silver_movies(), _silver_genres())
    assert set(result["genre_name"]) == {"Action", "Adventure"}


def test_genre_metrics_movie_count_per_genre():
    """Action (id=28) appears in films 1 and 2; Adventure (id=12) in films 1 and 3."""
    result = _build_genre_metrics(_silver_movies(), _silver_genres())
    action_row = result[result["genre_name"] == "Action"].iloc[0]
    adventure_row = result[result["genre_name"] == "Adventure"].iloc[0]
    assert int(action_row["movie_count"]) == 2
    assert int(adventure_row["movie_count"]) == 2


def test_genre_metrics_avg_rating_is_mean_of_member_movies():
    """Action avg rating = (8.0 + 7.0) / 2 = 7.5."""
    result = _build_genre_metrics(_silver_movies(), _silver_genres())
    action_row = result[result["genre_name"] == "Action"].iloc[0]
    assert abs(float(action_row["avg_rating"]) - 7.5) < 0.01


def test_genre_metrics_total_revenue_sums_correctly():
    """Action total revenue = 100M + 50M = 150M."""
    result = _build_genre_metrics(_silver_movies(), _silver_genres())
    action_row = result[result["genre_name"] == "Action"].iloc[0]
    assert int(action_row["total_revenue"]) == 150_000_000


# --- _build_decade_stats ---

def test_decade_stats_correct_decade_assignment():
    """Films from 1994 and 1999 are in the 1990s; 2005 in the 2000s."""
    result = _build_decade_stats(_silver_movies())
    decades = list(result["decade"])
    assert 1990 in decades
    assert 2000 in decades


def test_decade_stats_movie_count_per_decade():
    result = _build_decade_stats(_silver_movies())
    nineties = result[result["decade"] == 1990].iloc[0]
    assert int(nineties["movie_count"]) == 2


def test_decade_stats_excludes_movies_with_no_release_date():
    movies = _silver_movies().copy()
    movies.loc[0, "release_date"] = None
    result = _build_decade_stats(movies)
    # Film A (1994) is dropped; nineties only has Film B (1999), 2000s has Film C
    nineties_rows = result[result["decade"] == 1990]
    if len(nineties_rows):
        assert int(nineties_rows.iloc[0]["movie_count"]) == 1


def test_decade_stats_sorted_by_decade():
    result = _build_decade_stats(_silver_movies())
    assert list(result["decade"]) == sorted(result["decade"])


# --- _build_actor_filmography ---

def test_actor_filmography_film_counts():
    """Actor A (id=10) appears in 2 films; Actor B (id=11) in 1 film."""
    result = _build_actor_filmography(_silver_movies(), _silver_people(), _silver_bridge())
    actor_a = result[result["person_id"] == 10].iloc[0]
    actor_b = result[result["person_id"] == 11].iloc[0]
    assert int(actor_a["film_count"]) == 2
    assert int(actor_b["film_count"]) == 1


def test_actor_filmography_avg_rating_for_actor_a():
    """Actor A avg rating = (8.0 + 7.0) / 2 = 7.5."""
    result = _build_actor_filmography(_silver_movies(), _silver_people(), _silver_bridge())
    actor_a = result[result["person_id"] == 10].iloc[0]
    assert abs(float(actor_a["avg_rating"]) - 7.5) < 0.01


def test_actor_filmography_excludes_crew_rows():
    """Director rows (credit_type='crew') must not be counted in actor filmography."""
    result = _build_actor_filmography(_silver_movies(), _silver_people(), _silver_bridge())
    # person_id=20 is a director — should not appear
    assert 20 not in list(result["person_id"])


# --- _build_director_ratings ---

def test_director_ratings_film_count():
    """Director A (id=20) directed films 1 and 2 → film_count=2."""
    result = _build_director_ratings(_silver_movies(), _silver_people(), _silver_bridge())
    dir_a = result[result["person_id"] == 20].iloc[0]
    assert int(dir_a["film_count"]) == 2


def test_director_ratings_avg_rating():
    """Director A avg rating = (8.0 + 7.0) / 2 = 7.5."""
    result = _build_director_ratings(_silver_movies(), _silver_people(), _silver_bridge())
    dir_a = result[result["person_id"] == 20].iloc[0]
    assert abs(float(dir_a["avg_rating"]) - 7.5) < 0.01


def test_director_ratings_total_revenue():
    """Director A total revenue = 100M + 50M = 150M."""
    result = _build_director_ratings(_silver_movies(), _silver_people(), _silver_bridge())
    dir_a = result[result["person_id"] == 20].iloc[0]
    assert int(dir_a["total_revenue"]) == 150_000_000


def test_director_ratings_excludes_non_director_crew_credits():
    """A Producer-only crew credit must not count as a directing credit.

    Without the `role == "Director"` filter this counts every crew credit, which
    both inflates film_count and emits a null-name row for the non-director —
    making Gold disagree with fact_crew about what a director credit is.
    """
    bridge = pd.DataFrame(
        _silver_bridge().to_dict("records")
        + [
            {"movie_id": 3, "person_id": 20, "credit_type": "crew",
             "role": "Producer", "ordering": None},
            {"movie_id": 3, "person_id": 21, "credit_type": "crew",
             "role": "Editor", "ordering": None},
        ]
    )

    result = _build_director_ratings(_silver_movies(), _silver_people(), bridge)

    dir_a = result[result["person_id"] == 20].iloc[0]
    assert int(dir_a["film_count"]) == 2  # films 1 and 2 only, not 3
    assert 21 not in set(result["person_id"].dropna())


# --- build_gold_datasets (integration) ---

def test_build_gold_datasets_writes_four_parquet_files():
    """build_gold_datasets must call put_object exactly 4 times (one per dataset)."""
    date = dt.date(2026, 6, 26)
    mock_s3 = _make_multi_entity_s3_mock(date)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uris = build_gold_datasets(ingestion_date=date, bucket="theoria-datalake")

    assert mock_s3.put_object.call_count == 5
    assert set(uris.keys()) == {
        "genre_metrics", "decade_stats", "actor_filmography", "director_ratings",
        "collaboration_edges",
    }


def test_build_gold_datasets_keys_follow_path_convention():
    """All Gold S3 keys must follow gold/<dataset>/ingestion_date=.../dataset.parquet."""
    date = dt.date(2026, 6, 26)
    mock_s3 = _make_multi_entity_s3_mock(date)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uris = build_gold_datasets(ingestion_date=date, bucket="theoria-datalake")

    for name, uri in uris.items():
        assert uri.startswith("s3://theoria-datalake/gold/")
        assert "ingestion_date=2026-06-26" in uri
        assert uri.endswith(f"{name}.parquet")


def test_build_gold_datasets_raises_on_missing_silver(monkeypatch):
    """FileNotFoundError must be raised if a Silver file is missing."""
    mock_s3 = MagicMock()
    mock_s3.exceptions.NoSuchKey = KeyError
    mock_s3.get_object.side_effect = KeyError("No such key")

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with pytest.raises(FileNotFoundError):
            build_gold_datasets(ingestion_date=dt.date(2026, 6, 26), bucket="theoria-datalake")


# ---------------------------------------------------------------------------
# Task 15 — warehouse/db.py
# ---------------------------------------------------------------------------
from unittest.mock import MagicMock, patch, PropertyMock


def test_get_engine_returns_singleton():
    """get_engine() must return the same object on repeated calls."""
    from warehouse.db import get_engine, reset_engine

    reset_engine()
    with patch("warehouse.db.create_engine") as mock_create:
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine

        e1 = get_engine()
        e2 = get_engine()

    assert e1 is e2
    mock_create.assert_called_once()
    reset_engine()


def test_get_engine_uses_database_url(monkeypatch):
    """get_engine() must pass config.DATABASE_URL to create_engine."""
    import config
    from warehouse.db import get_engine, reset_engine

    reset_engine()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+psycopg2://test:pw@localhost/testdb")

    with patch("warehouse.db.create_engine") as mock_create:
        mock_create.return_value = MagicMock()
        get_engine()

    args, _ = mock_create.call_args
    assert args[0] == "postgresql+psycopg2://test:pw@localhost/testdb"
    reset_engine()


def test_get_session_commits_on_success():
    """get_session() must commit the session when no exception is raised."""
    from warehouse.db import get_session, reset_engine
    from sqlalchemy.orm import Session

    reset_engine()
    mock_session = MagicMock(spec=Session)
    mock_factory = MagicMock(return_value=mock_session)

    with patch("warehouse.db._get_session_factory", return_value=mock_factory):
        with get_session() as s:
            assert s is mock_session

    mock_session.commit.assert_called_once()
    mock_session.rollback.assert_not_called()
    mock_session.close.assert_called_once()
    reset_engine()


def test_get_session_rolls_back_on_exception():
    """get_session() must rollback and re-raise on any exception inside the block."""
    from warehouse.db import get_session, reset_engine
    from sqlalchemy.orm import Session

    reset_engine()
    mock_session = MagicMock(spec=Session)
    mock_factory = MagicMock(return_value=mock_session)

    with patch("warehouse.db._get_session_factory", return_value=mock_factory):
        with pytest.raises(ValueError):
            with get_session():
                raise ValueError("boom")

    mock_session.rollback.assert_called_once()
    mock_session.commit.assert_not_called()
    mock_session.close.assert_called_once()
    reset_engine()


def test_check_connection_returns_true_on_success():
    """check_connection() returns True when the DB responds."""
    from warehouse.db import check_connection, reset_engine

    reset_engine()
    mock_conn = MagicMock()
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    with patch("warehouse.db.get_engine", return_value=mock_engine):
        result = check_connection()

    assert result is True
    reset_engine()


def test_check_connection_returns_false_on_failure():
    """check_connection() returns False when the DB is unreachable."""
    from warehouse.db import check_connection, reset_engine

    reset_engine()
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = Exception("connection refused")

    with patch("warehouse.db.get_engine", return_value=mock_engine):
        result = check_connection()

    assert result is False
    reset_engine()


def test_reset_engine_disposes_and_clears():
    """reset_engine() must dispose the existing engine and clear the singleton."""
    from warehouse import db
    from warehouse.db import reset_engine

    reset_engine()
    mock_engine = MagicMock()

    with patch("warehouse.db.create_engine", return_value=mock_engine):
        from warehouse.db import get_engine
        get_engine()

    assert db._engine is mock_engine
    reset_engine()
    assert db._engine is None
    mock_engine.dispose.assert_called_once()


# ---------------------------------------------------------------------------
# Task 18 — etl/warehouse_loader/load_dimensions.py
# ---------------------------------------------------------------------------
from etl.warehouse_loader.load_dimensions import (
    _build_calendar,
    _records,
    _slugify,
    _upsert,
    assign_slugs,
    load_dim_collection,
    load_dim_company,
    load_dim_country,
    load_dim_date,
    load_dim_genre,
    load_dim_language,
    load_dim_movie,
    load_dimensions,
)


def _dim_movies_df():
    return pd.DataFrame({
        "movie_id": pd.array([1, 2], dtype="Int64"),
        "title": ["Alpha", "Beta"],
        "release_date": [dt.date(2020, 1, 1), None],
        "runtime": pd.array([100, None], dtype="Int64"),
        "budget": pd.array([1000, 2000], dtype="Int64"),
        "revenue": pd.array([5000, 6000], dtype="Int64"),
        "original_language": ["en", "fr"],
        "status": ["Released", "Released"],
        "vote_average": [7.5, 6.1],
        "vote_count": pd.array([100, 50], dtype="Int64"),
        "popularity": [10.0, 5.0],
        "overview": ["a", "b"],
        "collection_id": pd.array([100, None], dtype="Int64"),
        "collection_name": ["Alpha Collection", None],
        "collection_poster_path": ["/ac.jpg", None],
        "tagline": ["Tag A", None],
        "poster_path": ["/a.jpg", None],
        "backdrop_path": ["/a_bd.jpg", None],
        "imdb_id": ["tt0000001", None],
        "original_title": ["Alpha", "Beta"],
        "homepage": ["https://example.com/alpha", None],
        "genre_ids": [[1], [2]],
    })


def _dim_people_df():
    return pd.DataFrame({
        "person_id": pd.array([10, 20], dtype="Int64"),
        "name": ["Person A", "Person B"],
        "gender": pd.array([1, 2], dtype="Int64"),
        "popularity": [3.5, 4.5],
        "profile_path": ["/p.jpg", None],
        "known_for_department": ["Acting", "Directing"],
    })


def _dim_genres_df():
    return pd.DataFrame({
        "genre_id": pd.array([1, 2], dtype="Int64"),
        "genre_name": ["Action", "Comedy"],
    })


def _dim_companies_df():
    """Two link rows, one distinct company — mirrors movie_companies' shape:
    one row per (movie_id, company_id), not one row per company."""
    return pd.DataFrame({
        "movie_id": pd.array([1, 2], dtype="Int64"),
        "company_id": pd.array([900, 900], dtype="Int64"),
        "company_name": ["Warner Bros.", "Warner Bros."],
        "logo_path": ["/wb.png", "/wb.png"],
        "origin_country": ["US", "US"],
    })


def _dim_countries_df():
    """Two link rows for the same country, one origin without a matching
    production name — mirrors movie_countries' shape from Task 57."""
    return pd.DataFrame({
        "movie_id": pd.array([1, 2, 3], dtype="Int64"),
        "country_code": ["US", "US", "JP"],
        "country_name": ["United States", "United States", None],
        "relation": ["production", "origin", "origin"],
    })


def _dim_languages_df():
    return pd.DataFrame({
        "movie_id": pd.array([1, 2], dtype="Int64"),
        "language_code": ["en", "en"],
        "language_name": ["English", "English"],
        "english_name": ["English", "English"],
    })


def test_records_converts_na_to_none():
    """_records() must turn pandas NA/NaN values into plain None for psycopg2."""
    df = _dim_movies_df()
    records = _records(df, ["movie_id", "title", "runtime"])
    assert records[1]["runtime"] is None
    assert records[0]["movie_id"] == 1


def test_upsert_builds_on_conflict_sql_and_executes():
    """_upsert() must build an INSERT ... ON CONFLICT DO UPDATE statement and execute it once."""
    mock_session = MagicMock()
    records = [{"a": 1, "b": "x"}]

    count = _upsert(mock_session, "some_table", ["a"], ["a", "b"], records)

    assert count == 1
    mock_session.execute.assert_called_once()
    (stmt, params), _ = mock_session.execute.call_args
    sql = str(stmt)
    assert "INSERT INTO some_table" in sql
    assert "ON CONFLICT (a) DO UPDATE SET b = EXCLUDED.b" in sql
    assert params == records


def test_upsert_skips_execute_when_no_records():
    """_upsert() must not call session.execute() for an empty record list."""
    mock_session = MagicMock()
    count = _upsert(mock_session, "some_table", ["a"], ["a", "b"], [])
    assert count == 0
    mock_session.execute.assert_not_called()


def test_load_dim_movie_upserts_expected_columns():
    """load_dim_movie() must upsert only the dim_movie columns, keyed on movie_id."""
    mock_session = MagicMock()
    count = load_dim_movie(mock_session, _dim_movies_df())

    assert count == 2
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO dim_movie" in str(stmt)
    assert set(params[0].keys()) == {
        "movie_id", "title", "release_date", "runtime", "budget", "revenue",
        "original_language", "status", "overview", "tagline", "poster_path",
        "backdrop_path", "collection_id", "imdb_id", "original_title", "homepage",
    }


def test_load_dim_collection_takes_the_distinct_named_collections():
    """Films without a franchise contribute no dimension row — that's ~half the catalog."""
    mock_session = MagicMock()
    count = load_dim_collection(mock_session, _dim_movies_df())

    assert count == 1
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO dim_collection" in str(stmt)
    assert params == [
        {"collection_id": 100, "name": "Alpha Collection", "poster_path": "/ac.jpg"},
    ]


def test_load_dim_collection_deduplicates_a_franchise_shared_by_several_films():
    """Silver stores the collection inline per movie, so the dimension must dedupe it."""
    df = _dim_movies_df()
    df.loc[1, ["collection_id", "collection_name", "collection_poster_path"]] = [
        100, "Alpha Collection", "/ac.jpg",
    ]
    mock_session = MagicMock()

    count = load_dim_collection(mock_session, df)

    assert count == 1


def test_load_dim_company_deduplicates_a_studio_shared_by_several_films():
    """movie_companies is a link table — one row per (movie_id, company_id) —
    so the dimension must take the distinct set of company_id, same pattern
    as load_dim_collection()."""
    mock_session = MagicMock()
    count = load_dim_company(mock_session, _dim_companies_df())

    assert count == 1
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO dim_company" in str(stmt)
    assert params == [
        {"company_id": 900, "name": "Warner Bros.", "logo_path": "/wb.png", "origin_country": "US"},
    ]


def test_load_dim_company_excludes_rows_with_null_id_or_name():
    extra = pd.DataFrame({
        "movie_id": pd.array([3, 4], dtype="Int64"),
        "company_id": pd.array([pd.NA, 901], dtype="Int64"),
        "company_name": [None, None],  # null name on both, null id on the first
        "logo_path": [None, "/x.png"],
        "origin_country": [None, "GB"],
    })
    df = pd.concat([_dim_companies_df(), extra], ignore_index=True)
    mock_session = MagicMock()

    count = load_dim_company(mock_session, df)

    assert count == 1  # still just the one named, id'd company


def test_load_dim_country_deduplicates_and_drops_unnamed_origin_only_codes():
    """A country_code with no name anywhere in the partition (JP here) gets
    no dimension row — dim_country.name is NOT NULL, and the bridge loader
    quarantines that code's link rows via the normal unresolvable-FK path."""
    mock_session = MagicMock()
    count = load_dim_country(mock_session, _dim_countries_df())

    assert count == 1
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO dim_country" in str(stmt)
    assert params == [{"country_code": "US", "name": "United States"}]


def test_load_dim_language_deduplicates_a_language_shared_by_several_films():
    mock_session = MagicMock()
    count = load_dim_language(mock_session, _dim_languages_df())

    assert count == 1
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO dim_language" in str(stmt)
    assert params == [{"language_code": "en", "name": "English", "english_name": "English"}]


def test_load_dim_genre_upserts_expected_columns():
    """load_dim_genre() must upsert genre_id and genre_name only."""
    mock_session = MagicMock()
    count = load_dim_genre(mock_session, _dim_genres_df())

    assert count == 2
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO dim_genre" in str(stmt)
    assert set(params[0].keys()) == {"genre_id", "genre_name"}


def test_slugify_lowercases_and_hyphenates():
    """_slugify() must produce a lowercase, hyphenated, ASCII-only slug."""
    assert _slugify("Tom Holland") == "tom-holland"


def test_slugify_folds_accented_characters():
    """Accents fold to their plain-ASCII base rather than vanishing entirely."""
    assert _slugify("Zoë Kravitz") == "zoe-kravitz"


def test_slugify_empty_name_falls_back_to_untitled():
    """An empty/blank name must not produce an empty slug."""
    assert _slugify("") == "untitled"


def test_assign_slugs_numbers_name_collisions_in_id_order():
    """Two rows with the same name must get distinct slugs, numbered by id order."""
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = [
        (1, "John Smith"), (2, "John Smith"), (3, "Jane Doe"),
    ]

    count = assign_slugs(mock_session, "dim_actor", "actor_id", "name")

    assert count == 3
    (_, update_params), _ = mock_session.execute.call_args
    assert update_params == [
        {"id": 1, "slug": "john-smith"},
        {"id": 2, "slug": "john-smith-2"},
        {"id": 3, "slug": "jane-doe"},
    ]


def test_assign_slugs_is_stable_across_reruns():
    """Re-running assign_slugs() over the same rows must produce the same slugs."""
    mock_session = MagicMock()
    rows = [(1, "John Smith"), (2, "John Smith"), (3, "Jane Doe")]
    mock_session.execute.return_value.fetchall.return_value = rows

    assign_slugs(mock_session, "dim_actor", "actor_id", "name")
    (_, first_run), _ = mock_session.execute.call_args

    assign_slugs(mock_session, "dim_actor", "actor_id", "name")
    (_, second_run), _ = mock_session.execute.call_args

    assert first_run == second_run


def test_assign_slugs_clears_slugs_before_rewriting_them():
    """The rewrite must be preceded by a clear, or a permutation hits the unique index.

    Recomputing over the whole table can hand row B a slug that row A still
    holds (A moves to `-2` in the same batch). The rewrite is an executemany, so
    the index is checked per row and Postgres rejects that transient duplicate
    even though the final state is unique. Live regression from the Task 48
    backfill, which failed with "Key (slug)=(dee-wallace) already exists".
    """
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = [
        (1, "Dee Wallace"), (2, "Dee Wallace"),
    ]

    assign_slugs(mock_session, "dim_person", "person_id", "name")

    statements = [str(call.args[0]) for call in mock_session.execute.call_args_list]
    assert "SET slug = NULL" in statements[1]
    assert "SET slug = :slug" in statements[2]


def test_build_calendar_computes_surrogate_key_and_decade():
    """_build_calendar() must produce one row per day with a YYYYMMDD date_id and correct decade."""
    df = _build_calendar(dt.date(1999, 12, 30), dt.date(2000, 1, 1))

    assert len(df) == 3
    row_1999 = df[df["full_date"] == dt.date(1999, 12, 31)].iloc[0]
    assert int(row_1999["date_id"]) == 19991231
    assert int(row_1999["decade"]) == 1990

    row_2000 = df[df["full_date"] == dt.date(2000, 1, 1)].iloc[0]
    assert int(row_2000["date_id"]) == 20000101
    assert int(row_2000["decade"]) == 2000


def test_load_dim_date_upserts_full_range():
    """load_dim_date() must upsert one row per day in the given range."""
    mock_session = MagicMock()
    count = load_dim_date(mock_session, dt.date(2020, 1, 1), dt.date(2020, 1, 5))

    assert count == 5
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO dim_date" in str(stmt)
    assert len(params) == 5


def test_load_dimensions_reads_all_silver_entities_and_upserts(monkeypatch):
    """load_dimensions() must read all four Silver Parquet files and upsert every dim table."""
    date = dt.date(2026, 6, 26)

    def fake_read(bucket, entity, ingestion_date, filename):
        if entity == "movies":
            return _dim_movies_df()
        if entity == "people":
            return _dim_people_df()
        if entity == "genres":
            return _dim_genres_df()
        if entity == "movie_companies":
            return _dim_companies_df()
        if entity == "movie_countries":
            return _dim_countries_df()
        if entity == "movie_languages":
            return _dim_languages_df()
        raise AssertionError(f"unexpected entity {entity}")

    mock_session = MagicMock()

    import etl.warehouse_loader.load_dimensions as load_dimensions_module

    monkeypatch.setattr(load_dimensions_module, "_read_silver_parquet", fake_read)
    monkeypatch.setattr(
        load_dimensions_module, "get_session",
        lambda: MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock(return_value=False)),
    )

    counts = load_dimensions(
        ingestion_date=date, bucket="theoria-datalake",
        calendar_start=dt.date(2020, 1, 1), calendar_end=dt.date(2020, 1, 2),
    )

    assert counts == {
        "dim_collection": 1, "dim_movie": 2, "dim_person": 2,
        "dim_genre": 2, "dim_company": 1, "dim_country": 1, "dim_language": 1,
        "dim_date": 2,
        "dim_movie_slugs": 0, "dim_person_slugs": 0, "dim_collection_slugs": 0,
        "dim_company_slugs": 0,
    }
    # 8 upserts + 4 slug SELECTs (the mocked session's empty fetchall() means
    # no matching UPDATE is issued for any of the four slugged tables).
    assert mock_session.execute.call_count == 12


# ---------------------------------------------------------------------------
# Task 19 / Task 35 — etl/warehouse_loader/load_facts.py
# ---------------------------------------------------------------------------
from etl.warehouse_loader.load_facts import (
    _build_bridge_company_rows,
    _build_bridge_country_rows,
    _build_bridge_language_rows,
    _build_credit_rows,
    _build_movie_metrics_rows,
    _existing_ids,
    _records,
    _write_rejects,
    load_bridge_movie_company,
    load_bridge_movie_country,
    load_bridge_movie_language,
    load_fact_movie_metrics,
    load_facts,
)


def _fact_movies_df():
    return pd.DataFrame({
        "movie_id": pd.array([1, 2, 3, 4], dtype="Int64"),
        "title": ["Alpha", "Beta", "Gamma", "Delta"],
        "release_date": [dt.date(2020, 1, 1), None, dt.date(2020, 1, 2), dt.date(2020, 1, 2)],
        "vote_average": [7.5, 6.1, 8.0, 5.0],
        "vote_count": pd.array([100, 50, 20, 10], dtype="Int64"),
        "revenue": pd.array([5000, 6000, 7000, 8000], dtype="Int64"),
        "budget": pd.array([1000, 2000, 3000, 4000], dtype="Int64"),
        "popularity": [10.0, 5.0, 2.0, 1.0],
        # movie 1: known genre; movie 3: unknown genre; movie 4: no genres at all.
        "genre_ids": [[1], [1], [99], []],
    })


def _fact_bridge_df():
    return pd.DataFrame({
        "movie_id": pd.array([1, 1, 1, 2, 3, 3], dtype="Int64"),
        "person_id": pd.array([10, 11, 20, 30, 40, 999], dtype="Int64"),
        "credit_type": ["cast", "cast", "crew", "cast", "cast", "crew"],
        "department": ["Acting", "Acting", "Directing", "Acting", "Acting", "Directing"],
        "role": ["Hero", "Villain", "Director", "Lead", "Lead", "Director"],
        "ordering": pd.array([0, 1, None, 0, 0, None], dtype="Int64"),
    })


def _fact_companies_df():
    return pd.DataFrame({
        "movie_id": pd.array([1, 999], dtype="Int64"),
        "company_id": pd.array([1, 2], dtype="Int64"),
        "company_name": ["Studio One", "Unknown Studio"],
        "logo_path": [None, None],
        "origin_country": ["US", "US"],
    })


def _fact_countries_df():
    return pd.DataFrame({
        "movie_id": pd.array([1, 999], dtype="Int64"),
        "country_code": ["US", "ZZ"],
        "country_name": ["United States", "Nowhere"],
        "relation": ["production", "origin"],
    })


def _fact_languages_df():
    return pd.DataFrame({
        "movie_id": pd.array([1, 999], dtype="Int64"),
        "language_code": ["en", "zz"],
        "language_name": ["English", "Nowherish"],
        "english_name": ["English", "Nowherish"],
    })


def test_existing_ids_queries_and_returns_set():
    """_existing_ids() must select the PK column and return it as a set of ints."""
    mock_session = MagicMock()
    mock_session.execute.return_value.scalars.return_value.all.return_value = [1, 2, 2]

    result = _existing_ids(mock_session, "dim_movie", "movie_id")

    assert result == {1, 2}
    (stmt,), _ = mock_session.execute.call_args
    assert "SELECT movie_id" in str(stmt)
    assert "dim_movie" in str(stmt)


def test_records_converts_na_to_none():
    """_records() must turn NaN values into plain None for psycopg2."""
    records = _records([{"a": 1, "b": float("nan")}, {"a": 2, "b": 3}])
    assert records[0]["b"] is None
    assert records[1]["b"] == 3


def test_build_movie_metrics_rows_explodes_genres_and_resolves_date_id():
    """A movie with known movie_id/date_id/genre_id produces one row per genre."""
    rows, rejects = _build_movie_metrics_rows(
        _fact_movies_df(), valid_movie_ids={1}, valid_date_ids={20200101}, valid_genre_ids={1},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert len(rows) == 1
    assert rows[0] == {
        "movie_id": 1, "date_id": 20200101, "genre_id": 1,
        "rating": 7.5, "vote_count": 100, "revenue": 5000, "budget": 1000, "popularity": 10.0,
        "ingestion_date": dt.date(2026, 6, 26),
    }
    # movies 2, 3, 4 all fail one lookup or another.
    assert len(rejects) == 3


def test_build_movie_metrics_rows_rejects_unknown_movie_id():
    """A movie_id absent from dim_movie must be rejected, not inserted."""
    rows, rejects = _build_movie_metrics_rows(
        _fact_movies_df(), valid_movie_ids=set(), valid_date_ids={20200101, 20200102},
        valid_genre_ids={1}, ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == []
    assert all(r["rejection_reason"] == "unknown movie_id" for r in rejects)


def test_build_movie_metrics_rows_rejects_missing_release_date():
    """A movie with a null release_date cannot be assigned a date_id and must be rejected."""
    rows, rejects = _build_movie_metrics_rows(
        _fact_movies_df(), valid_movie_ids={2}, valid_date_ids={20200101, 20200102},
        valid_genre_ids={1}, ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == []
    reject = next(r for r in rejects if r["movie_id"] == 2)
    assert reject["rejection_reason"] == "missing release_date"


def test_build_movie_metrics_rows_rejects_unknown_genre_id():
    """A genre_id absent from dim_genre must be rejected while other genres on the same movie still load."""
    rows, rejects = _build_movie_metrics_rows(
        _fact_movies_df(), valid_movie_ids={3}, valid_date_ids={20200102}, valid_genre_ids=set(),
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == []
    reject = next(r for r in rejects if r["movie_id"] == 3)
    assert reject["rejection_reason"] == "unknown genre_id"


def test_build_movie_metrics_rows_rejects_movie_with_no_genres():
    """A movie with an empty genre_ids list must be rejected (fact_movie_metrics.genre_id is NOT NULL)."""
    rows, rejects = _build_movie_metrics_rows(
        _fact_movies_df(), valid_movie_ids={4}, valid_date_ids={20200102}, valid_genre_ids={1},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == []
    reject = next(r for r in rejects if r["movie_id"] == 4)
    assert reject["rejection_reason"] == "no genres"


def _credit_bridge_df():
    """Bridge rows exercising cast, director, and non-director crew on one film."""
    return pd.DataFrame({
        "movie_id": pd.array([1, 1, 1, 1, 1], dtype="Int64"),
        "person_id": pd.array([10, 11, 20, 20, 21], dtype="Int64"),
        "credit_type": ["cast", "cast", "crew", "crew", "crew"],
        "department": ["Acting", "Acting", "Directing", "Writing", "Editing"],
        "role": ["Hero", "Villain", "Director", "Screenplay", "Editor"],
        "ordering": pd.array([0, 1, None, None, None], dtype="Int64"),
    })


def test_build_credit_rows_keeps_non_director_crew():
    """Every crew credit survives — this is the ~99% of crew the old loader dropped.

    _build_crew_rows() filters role == "Director"; fact_credit must not.
    """
    rows, rejects = _build_credit_rows(
        _credit_bridge_df(), valid_movie_ids={1}, valid_person_ids={10, 11, 20, 21},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert len(rows) == 5
    assert not rejects
    jobs = {(r["person_id"], r["job"]) for r in rows}
    assert (21, "Editor") in jobs
    assert (20, "Screenplay") in jobs


def test_build_credit_rows_keeps_both_jobs_of_a_multi_job_person():
    """A director who also wrote the film is two credits, not one.

    The PK is (movie, person, department, job) precisely so this cannot collapse.
    """
    rows, _ = _build_credit_rows(
        _credit_bridge_df(), valid_movie_ids={1}, valid_person_ids={20},
        ingestion_date=dt.date(2026, 6, 26),
    )

    person_20 = sorted(r["job"] for r in rows if r["person_id"] == 20)
    assert person_20 == ["Director", "Screenplay"]


def test_build_credit_rows_normalises_cast_to_actor_job():
    """Cast credits become department=Acting / job=Actor, with the part in character_name."""
    rows, _ = _build_credit_rows(
        _credit_bridge_df(), valid_movie_ids={1}, valid_person_ids={10},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == [{
        "movie_id": 1, "person_id": 10, "department": "Acting", "job": "Actor",
        "character_name": "Hero", "ordering": 0,
        "ingestion_date": dt.date(2026, 6, 26),
    }]


def test_build_credit_rows_leaves_character_null_for_crew():
    rows, _ = _build_credit_rows(
        _credit_bridge_df(), valid_movie_ids={1}, valid_person_ids={21},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows[0]["character_name"] is None
    assert rows[0]["ordering"] is None


def test_build_credit_rows_collapses_one_actor_playing_two_characters():
    """Two character credits share the fact PK; keep the top-billed one, don't duplicate."""
    df = pd.DataFrame({
        "movie_id": pd.array([1, 1], dtype="Int64"),
        "person_id": pd.array([10, 10], dtype="Int64"),
        "credit_type": ["cast", "cast"],
        "department": ["Acting", "Acting"],
        "role": ["Twin B", "Twin A"],
        "ordering": pd.array([4, 1], dtype="Int64"),
    })

    rows, _ = _build_credit_rows(
        df, valid_movie_ids={1}, valid_person_ids={10},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert len(rows) == 1
    assert rows[0]["character_name"] == "Twin A"


def test_build_credit_rows_rejects_unknown_person_id():
    rows, rejects = _build_credit_rows(
        _credit_bridge_df(), valid_movie_ids={1}, valid_person_ids={10},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert len(rows) == 1
    assert {r["rejection_reason"] for r in rejects} == {"unknown person_id"}


def test_build_bridge_company_rows_resolves_known_links():
    rows, rejects = _build_bridge_company_rows(
        _fact_companies_df(), valid_movie_ids={1}, valid_company_ids={1},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == [{"movie_id": 1, "company_id": 1, "ingestion_date": dt.date(2026, 6, 26)}]
    assert len(rejects) == 1
    assert rejects[0]["rejection_reason"] in {"unknown movie_id", "unknown company_id"}


def test_build_bridge_company_rows_rejects_unknown_company_id():
    df = pd.DataFrame({
        "movie_id": pd.array([1], dtype="Int64"),
        "company_id": pd.array([999], dtype="Int64"),
        "company_name": ["Ghost Studio"],
        "logo_path": [None],
        "origin_country": [None],
    })
    rows, rejects = _build_bridge_company_rows(
        df, valid_movie_ids={1}, valid_company_ids=set(), ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == []
    assert rejects[0]["rejection_reason"] == "unknown company_id"


def test_load_bridge_movie_company_upserts_and_returns_rejects(monkeypatch):
    mock_session = MagicMock()
    import etl.warehouse_loader.load_facts as load_facts_module

    monkeypatch.setattr(
        load_facts_module, "_existing_ids",
        lambda session, table, pk_col: {1} if table == "dim_movie" else {1},
    )

    count, rejects = load_bridge_movie_company(
        mock_session, _fact_companies_df(), dt.date(2026, 6, 26)
    )

    assert count == 1
    assert len(rejects) == 1
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO bridge_movie_company" in str(stmt)
    assert params == [{"movie_id": 1, "company_id": 1, "ingestion_date": dt.date(2026, 6, 26)}]


def test_build_bridge_country_rows_resolves_known_links_and_keeps_relation():
    rows, rejects = _build_bridge_country_rows(
        _fact_countries_df(), valid_movie_ids={1}, valid_country_codes={"US"},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == [{
        "movie_id": 1, "country_code": "US", "relation": "production",
        "ingestion_date": dt.date(2026, 6, 26),
    }]
    assert len(rejects) == 1
    assert rejects[0]["rejection_reason"] in {"unknown movie_id", "unknown country_code"}


def test_build_bridge_country_rows_rejects_unknown_country_code():
    df = pd.DataFrame({
        "movie_id": pd.array([1], dtype="Int64"),
        "country_code": ["ZZ"],
        "country_name": ["Nowhere"],
        "relation": ["origin"],
    })
    rows, rejects = _build_bridge_country_rows(
        df, valid_movie_ids={1}, valid_country_codes=set(), ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == []
    assert rejects[0]["rejection_reason"] == "unknown country_code"


def test_load_bridge_movie_country_upserts_and_returns_rejects(monkeypatch):
    mock_session = MagicMock()
    import etl.warehouse_loader.load_facts as load_facts_module

    monkeypatch.setattr(load_facts_module, "_existing_ids", lambda session, table, pk_col: {1})
    monkeypatch.setattr(load_facts_module, "_existing_str_ids", lambda session, table, pk_col: {"US"})

    count, rejects = load_bridge_movie_country(
        mock_session, _fact_countries_df(), dt.date(2026, 6, 26)
    )

    assert count == 1
    assert len(rejects) == 1
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO bridge_movie_country" in str(stmt)
    assert params == [{
        "movie_id": 1, "country_code": "US", "relation": "production",
        "ingestion_date": dt.date(2026, 6, 26),
    }]


def test_build_bridge_language_rows_resolves_known_links():
    rows, rejects = _build_bridge_language_rows(
        _fact_languages_df(), valid_movie_ids={1}, valid_language_codes={"en"},
        ingestion_date=dt.date(2026, 6, 26),
    )

    assert rows == [{"movie_id": 1, "language_code": "en", "ingestion_date": dt.date(2026, 6, 26)}]
    assert len(rejects) == 1
    assert rejects[0]["rejection_reason"] in {"unknown movie_id", "unknown language_code"}


def test_load_bridge_movie_language_upserts_and_returns_rejects(monkeypatch):
    mock_session = MagicMock()
    import etl.warehouse_loader.load_facts as load_facts_module

    monkeypatch.setattr(load_facts_module, "_existing_ids", lambda session, table, pk_col: {1})
    monkeypatch.setattr(load_facts_module, "_existing_str_ids", lambda session, table, pk_col: {"en"})

    count, rejects = load_bridge_movie_language(
        mock_session, _fact_languages_df(), dt.date(2026, 6, 26)
    )

    assert count == 1
    assert len(rejects) == 1
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO bridge_movie_language" in str(stmt)
    assert params == [{"movie_id": 1, "language_code": "en", "ingestion_date": dt.date(2026, 6, 26)}]


def test_write_rejects_writes_parquet_file(tmp_path):
    """_write_rejects() must write a Parquet file named <entity>_rejected_<date>.parquet."""
    path = _write_rejects(
        [{"movie_id": 1, "rejection_reason": "unknown movie_id"}],
        "fact_movie_metrics", dt.date(2026, 6, 26), tmp_path,
    )

    assert path is not None
    assert path.name == "fact_movie_metrics_rejected_2026-06-26.parquet"
    assert pd.read_parquet(path)["rejection_reason"].iloc[0] == "unknown movie_id"


def test_write_rejects_returns_none_when_empty(tmp_path):
    """_write_rejects() must return None and write nothing when there are no rejects."""
    assert _write_rejects([], "fact_movie_metrics", dt.date(2026, 6, 26), tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_load_fact_movie_metrics_resolves_fks_and_upserts(monkeypatch):
    """load_fact_movie_metrics() must query dimension tables for valid IDs, then upsert only resolvable rows."""
    import etl.warehouse_loader.load_facts as load_facts_module

    mock_session = MagicMock()
    id_sets = {
        "dim_movie": {1, 2, 3, 4}, "dim_date": {20200101, 20200102}, "dim_genre": {1},
    }
    monkeypatch.setattr(
        load_facts_module, "_existing_ids",
        lambda session, table, pk_col: id_sets[table],
    )

    count, rejects = load_fact_movie_metrics(mock_session, _fact_movies_df(), dt.date(2026, 6, 26))

    assert count == 1
    assert len(rejects) == 3
    (stmt, params), _ = mock_session.execute.call_args
    assert "INSERT INTO fact_movie_metrics" in str(stmt)
    assert params[0]["genre_id"] == 1
    assert params[0]["ingestion_date"] == dt.date(2026, 6, 26)


def test_load_facts_reads_both_silver_entities_and_upserts(monkeypatch, tmp_path):
    """load_facts() must read movies + credits_bridge, upsert all three fact tables, and write rejects."""
    date = dt.date(2026, 6, 26)
    mock_session = MagicMock()

    def fake_read(bucket, entity, ingestion_date, filename):
        if entity == "movies":
            return _fact_movies_df()
        if entity == "credits_bridge":
            return _fact_bridge_df()
        if entity == "movie_companies":
            return _fact_companies_df()
        if entity == "movie_countries":
            return _fact_countries_df()
        if entity == "movie_languages":
            return _fact_languages_df()
        raise AssertionError(f"unexpected entity {entity}")

    import etl.warehouse_loader.load_facts as load_facts_module

    monkeypatch.setattr(load_facts_module, "_read_silver_parquet", fake_read)
    monkeypatch.setattr(
        load_facts_module, "get_session",
        lambda: MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock(return_value=False)),
    )
    id_sets = {
        "dim_movie": {1, 2, 3, 4}, "dim_date": {20200101, 20200102}, "dim_genre": {1},
        "dim_person": {10, 11, 20, 30, 40}, "dim_company": {1},
    }
    str_id_sets = {"dim_country": {"US"}, "dim_language": {"en"}}
    monkeypatch.setattr(
        load_facts_module, "_existing_ids",
        lambda session, table, pk_col: id_sets[table],
    )
    monkeypatch.setattr(
        load_facts_module, "_existing_str_ids",
        lambda session, table, pk_col: str_id_sets[table],
    )

    counts = load_facts(ingestion_date=date, bucket="theoria-datalake", rejected_dir=tmp_path)

    assert counts == {
        "fact_movie_metrics": 1, "fact_credit": 5, "bridge_movie_company": 1,
        "bridge_movie_country": 1, "bridge_movie_language": 1,
    }
    rejected_files = sorted(p.name for p in tmp_path.iterdir())
    assert rejected_files == [
        "bridge_movie_company_rejected_2026-06-26.parquet",
        "bridge_movie_country_rejected_2026-06-26.parquet",
        "bridge_movie_language_rejected_2026-06-26.parquet",
        "fact_credit_rejected_2026-06-26.parquet",
        "fact_movie_metrics_rejected_2026-06-26.parquet",
    ]


# ---------------------------------------------------------------------------
# Task 20 — etl/incremental.py
# ---------------------------------------------------------------------------
from etl.incremental import get_watermark, list_available_partitions, pending_partitions, set_watermark


def test_get_watermark_returns_date_when_row_exists():
    """get_watermark() must return the stored last_ingestion_date for a loader."""
    mock_session = MagicMock()
    mock_session.execute.return_value.first.return_value = (dt.date(2026, 6, 20),)

    result = get_watermark(mock_session, "load_dimensions")

    assert result == dt.date(2026, 6, 20)
    (stmt, params), _ = mock_session.execute.call_args
    assert "etl_watermarks" in str(stmt)
    assert params == {"name": "load_dimensions"}


def test_get_watermark_returns_none_when_no_row():
    """get_watermark() must return None if the loader has never recorded a watermark."""
    mock_session = MagicMock()
    mock_session.execute.return_value.first.return_value = None

    assert get_watermark(mock_session, "load_dimensions") is None


def test_set_watermark_upserts_loader_row():
    """set_watermark() must upsert (loader_name, ingestion_date) via ON CONFLICT DO UPDATE."""
    mock_session = MagicMock()

    set_watermark(mock_session, "load_facts", dt.date(2026, 6, 21))

    (stmt, params), _ = mock_session.execute.call_args
    sql = str(stmt)
    assert "INSERT INTO etl_watermarks" in sql
    assert "ON CONFLICT (loader_name) DO UPDATE" in sql
    assert params == {"name": "load_facts", "date": dt.date(2026, 6, 21)}


def test_list_available_partitions_parses_ingestion_date_prefixes(monkeypatch):
    """list_available_partitions() must parse ingestion_date=YYYY-MM-DD prefixes and sort ascending."""
    import etl.incremental as incremental_module

    mock_client = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"CommonPrefixes": [
            {"Prefix": "silver/movies/ingestion_date=2026-06-22/"},
            {"Prefix": "silver/movies/ingestion_date=2026-06-20/"},
        ]},
        {"CommonPrefixes": [
            {"Prefix": "silver/movies/ingestion_date=2026-06-21/"},
            {"Prefix": "silver/movies/not_a_date/"},
        ]},
    ]
    mock_client.get_paginator.return_value = mock_paginator
    monkeypatch.setattr(incremental_module.s3_utils, "get_s3_client", lambda: mock_client)

    dates = list_available_partitions("theoria-datalake", "silver", "movies")

    assert dates == [dt.date(2026, 6, 20), dt.date(2026, 6, 21), dt.date(2026, 6, 22)]
    mock_paginator.paginate.assert_called_once_with(
        Bucket="theoria-datalake", Prefix="silver/movies/", Delimiter="/",
    )


def test_pending_partitions_returns_all_when_no_watermark(monkeypatch):
    """pending_partitions() must return every available partition if no watermark exists yet."""
    import etl.incremental as incremental_module

    mock_session = MagicMock()
    monkeypatch.setattr(incremental_module, "get_watermark", lambda session, name: None)
    monkeypatch.setattr(
        incremental_module, "list_available_partitions",
        lambda bucket, layer, entity: [dt.date(2026, 6, 20), dt.date(2026, 6, 21)],
    )

    dates = pending_partitions(mock_session, "load_dimensions", "theoria-datalake", "silver", "movies")

    assert dates == [dt.date(2026, 6, 20), dt.date(2026, 6, 21)]


def test_pending_partitions_filters_to_dates_after_watermark(monkeypatch):
    """pending_partitions() must only return partitions strictly newer than the watermark."""
    import etl.incremental as incremental_module

    mock_session = MagicMock()
    monkeypatch.setattr(incremental_module, "get_watermark", lambda session, name: dt.date(2026, 6, 20))
    monkeypatch.setattr(
        incremental_module, "list_available_partitions",
        lambda bucket, layer, entity: [dt.date(2026, 6, 19), dt.date(2026, 6, 20), dt.date(2026, 6, 21)],
    )

    dates = pending_partitions(mock_session, "load_dimensions", "theoria-datalake", "silver", "movies")

    assert dates == [dt.date(2026, 6, 21)]


# ---------------------------------------------------------------------------
# Task 20 — load_dimensions_incremental() / load_facts_incremental()
# ---------------------------------------------------------------------------
from etl.warehouse_loader.load_dimensions import load_dimensions_incremental
from etl.warehouse_loader.load_facts import load_facts_incremental


def test_load_dimensions_incremental_processes_pending_dates_and_advances_watermark(monkeypatch):
    """load_dimensions_incremental() must call load_dimensions() per pending date and advance the watermark each time."""
    import etl.warehouse_loader.load_dimensions as load_dimensions_module

    mock_session = MagicMock()
    monkeypatch.setattr(
        load_dimensions_module, "get_session",
        lambda: MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock(return_value=False)),
    )
    monkeypatch.setattr(
        load_dimensions_module, "pending_partitions",
        lambda session, loader_name, bucket, layer, entity: [dt.date(2026, 6, 20), dt.date(2026, 6, 21)],
    )

    calls = []
    monkeypatch.setattr(
        load_dimensions_module, "load_dimensions",
        lambda ingestion_date, bucket, calendar_start, calendar_end: (
            calls.append(ingestion_date) or {"dim_movie": 1}
        ),
    )
    watermark_calls = []
    monkeypatch.setattr(
        load_dimensions_module, "set_watermark",
        lambda session, loader_name, ingestion_date: watermark_calls.append((loader_name, ingestion_date)),
    )

    results = load_dimensions_incremental(bucket="theoria-datalake")

    assert calls == [dt.date(2026, 6, 20), dt.date(2026, 6, 21)]
    assert watermark_calls == [
        ("load_dimensions", dt.date(2026, 6, 20)),
        ("load_dimensions", dt.date(2026, 6, 21)),
    ]
    assert results == {"2026-06-20": {"dim_movie": 1}, "2026-06-21": {"dim_movie": 1}}


def test_load_dimensions_incremental_noop_when_no_pending_dates(monkeypatch):
    """load_dimensions_incremental() must return {} and call neither loader nor set_watermark when nothing is pending."""
    import etl.warehouse_loader.load_dimensions as load_dimensions_module

    mock_session = MagicMock()
    monkeypatch.setattr(
        load_dimensions_module, "get_session",
        lambda: MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock(return_value=False)),
    )
    monkeypatch.setattr(
        load_dimensions_module, "pending_partitions",
        lambda session, loader_name, bucket, layer, entity: [],
    )
    monkeypatch.setattr(
        load_dimensions_module, "load_dimensions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    assert load_dimensions_incremental(bucket="theoria-datalake") == {}


def test_load_facts_incremental_processes_pending_dates_and_advances_watermark(monkeypatch):
    """load_facts_incremental() must call load_facts() per pending date and advance the watermark each time."""
    import etl.warehouse_loader.load_facts as load_facts_module

    mock_session = MagicMock()
    monkeypatch.setattr(
        load_facts_module, "get_session",
        lambda: MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock(return_value=False)),
    )
    monkeypatch.setattr(
        load_facts_module, "pending_partitions",
        lambda session, loader_name, bucket, layer, entity: [dt.date(2026, 6, 20)],
    )

    calls = []
    monkeypatch.setattr(
        load_facts_module, "load_facts",
        lambda ingestion_date, bucket, rejected_dir: (
            calls.append(ingestion_date) or {"fact_movie_metrics": 1, "fact_cast": 2, "fact_crew": 1}
        ),
    )
    watermark_calls = []
    monkeypatch.setattr(
        load_facts_module, "set_watermark",
        lambda session, loader_name, ingestion_date: watermark_calls.append((loader_name, ingestion_date)),
    )

    results = load_facts_incremental(bucket="theoria-datalake")

    assert calls == [dt.date(2026, 6, 20)]
    assert watermark_calls == [("load_facts", dt.date(2026, 6, 20))]
    assert results == {"2026-06-20": {"fact_movie_metrics": 1, "fact_cast": 2, "fact_crew": 1}}


def test_load_facts_incremental_noop_when_no_pending_dates(monkeypatch):
    """load_facts_incremental() must return {} when nothing is pending."""
    import etl.warehouse_loader.load_facts as load_facts_module

    mock_session = MagicMock()
    monkeypatch.setattr(
        load_facts_module, "get_session",
        lambda: MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock(return_value=False)),
    )
    monkeypatch.setattr(
        load_facts_module, "pending_partitions",
        lambda session, loader_name, bucket, layer, entity: [],
    )
    monkeypatch.setattr(
        load_facts_module, "load_facts",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    assert load_facts_incremental(bucket="theoria-datalake") == {}


# --- load_gold ----------------------------------------------------------------

from etl.warehouse_loader.load_gold import load_fact_collaboration, load_gold


def _gold_edges_df() -> pd.DataFrame:
    return pd.DataFrame({
        "person_a_id": pd.array([10, 10, 11], dtype="Int64"),
        "person_b_id": pd.array([20, 99, 20], dtype="Int64"),
        "films_together": pd.array([3, 1, 2], dtype="Int64"),
        "first_year": pd.array([1994, 2001, 1998], dtype="Int64"),
        "last_year": pd.array([2005, 2001, 1998], dtype="Int64"),
    })


def test_load_fact_collaboration_upserts_resolved_edges(monkeypatch):
    """Edges whose two people both exist in dim_person are upserted."""
    import etl.warehouse_loader.load_gold as load_gold_module

    monkeypatch.setattr(load_gold_module, "_existing_ids",
                        lambda session, table, pk_col: {10, 11, 20})
    mock_session = MagicMock()

    count = load_fact_collaboration(mock_session, _gold_edges_df())

    # The (10, 99) edge references a person absent from dim_person.
    assert count == 2
    (_, params), _ = mock_session.execute.call_args
    assert {(p["person_a_id"], p["person_b_id"]) for p in params} == {(10, 20), (11, 20)}


def test_load_fact_collaboration_logs_unresolvable_edges_as_an_error(caplog):
    """An FK miss here means Gold and the dimension load disagree — surface it loudly."""
    import etl.warehouse_loader.load_gold as load_gold_module

    with patch.object(load_gold_module, "_existing_ids", return_value={10, 11, 20}):
        with caplog.at_level(logging.ERROR):
            load_fact_collaboration(MagicMock(), _gold_edges_df())

    assert "absent from dim_person" in caplog.text


def test_load_gold_reads_gold_not_silver(monkeypatch):
    """load_gold must read from the gold/ prefix — the layer is the whole point."""
    import etl.warehouse_loader.load_gold as load_gold_module

    mock_session = MagicMock()
    monkeypatch.setattr(
        load_gold_module, "get_session",
        lambda: MagicMock(__enter__=MagicMock(return_value=mock_session), __exit__=MagicMock(return_value=False)),
    )
    monkeypatch.setattr(load_gold_module, "_existing_ids",
                        lambda session, table, pk_col: {10, 11, 20})

    mock_s3 = MagicMock()
    body = MagicMock()
    body.read.return_value = _parquet_body(_gold_edges_df())
    mock_s3.get_object.return_value = {"Body": body}

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        counts = load_gold(ingestion_date=dt.date(2026, 6, 26), bucket="theoria-datalake")

    assert counts == {"fact_collaboration": 2}
    key = mock_s3.get_object.call_args[1]["Key"]
    assert key == "gold/collaboration_edges/ingestion_date=2026-06-26/collaboration_edges.parquet"
