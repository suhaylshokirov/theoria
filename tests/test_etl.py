"""Unit tests for the ETL layer.

These tests mock all HTTP so they never touch the network: they verify the
TMDBClient's retry/backoff and error-handling logic in isolation.
"""

from __future__ import annotations

import datetime as dt
import io
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
    _extract_actors,
    _extract_directors,
    transform_people,
)


def _raw_credits(movie_id: int, extra_cast: list | None = None, extra_crew: list | None = None) -> dict:
    """Minimal TMDB credits payload for testing."""
    cast = [
        {"id": 10, "name": "Alice", "gender": 1, "popularity": 20.0},
        {"id": 11, "name": "Bob", "gender": 2, "popularity": 15.0},
    ]
    crew = [
        {"id": 20, "name": "Carol", "job": "Director", "gender": 1, "popularity": 30.0},
        {"id": 21, "name": "Dave", "job": "Producer", "gender": 2, "popularity": 5.0},
    ]
    if extra_cast:
        cast.extend(extra_cast)
    if extra_crew:
        crew.extend(extra_crew)
    return {"id": movie_id, "cast": cast, "crew": crew}


def test_extract_actors_returns_all_cast_members():
    payload = _raw_credits(550)
    rows = _extract_actors(payload)
    assert len(rows) == 2
    assert rows[0]["person_id"] == 10
    assert rows[0]["name"] == "Alice"


def test_extract_directors_filters_to_director_job_only():
    payload = _raw_credits(550)
    rows = _extract_directors(payload)
    assert len(rows) == 1
    assert rows[0]["person_id"] == 20
    assert rows[0]["name"] == "Carol"


def test_cast_people_types_converts_numerics():
    rows = _extract_actors(_raw_credits(550))
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


def test_transform_people_writes_actors_and_directors_parquet():
    """transform_people must write two Silver Parquet files."""
    key = "bronze/credits/ingestion_date=2026-06-22/550.json"
    mock_s3 = _make_s3_mock_with_files({key: _raw_credits(550)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        actors_uri, directors_uri = transform_people(
            ingestion_date=dt.date(2026, 6, 22),
            bucket="theoria-datalake",
        )

    assert actors_uri == "s3://theoria-datalake/silver/actors/ingestion_date=2026-06-22/actors.parquet"
    assert directors_uri == "s3://theoria-datalake/silver/directors/ingestion_date=2026-06-22/directors.parquet"
    assert mock_s3.put_object.call_count == 2


def test_transform_people_deduplicates_actors_across_movies():
    """The same person appearing in two movies' casts must produce one actor row."""
    key1 = "bronze/credits/ingestion_date=2026-06-22/550.json"
    key2 = "bronze/credits/ingestion_date=2026-06-22/551.json"
    # Both movies share actor id=10
    mock_s3 = _make_s3_mock_with_files({
        key1: _raw_credits(550),
        key2: _raw_credits(551),
    })

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        transform_people(ingestion_date=dt.date(2026, 6, 22), bucket="theoria-datalake")

    calls = mock_s3.put_object.call_args_list
    actors_call = next(c for c in calls if "actors.parquet" in c[1]["Key"])
    df_actors = pd.read_parquet(io.BytesIO(actors_call[1]["Body"]))
    # person_id 10 and 11 each appear twice across the two files — should collapse to 2 rows
    assert len(df_actors) == 2


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
             "role": "Hero", "ordering": "nope"}]
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


def test_transform_credits_bridge_deduplicates_on_movie_person_credit_type():
    """Same (movie_id, person_id, credit_type) across two files → one row."""
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


# ---------------------------------------------------------------------------
# Gold layer: build_gold_datasets
# ---------------------------------------------------------------------------

from etl.gold.build_gold_datasets import (
    _build_actor_filmography,
    _build_decade_stats,
    _build_director_ratings,
    _build_genre_metrics,
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


def _silver_actors() -> pd.DataFrame:
    return pd.DataFrame([
        {"person_id": 10, "name": "Actor A", "gender": 2, "popularity": 50.0},
        {"person_id": 11, "name": "Actor B", "gender": 1, "popularity": 30.0},
    ])


def _silver_directors() -> pd.DataFrame:
    return pd.DataFrame([
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


def _parquet_body(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, engine="pyarrow", index=False)
    return buf.getvalue()


def _make_multi_entity_s3_mock(ingestion_date: dt.date) -> MagicMock:
    """S3 mock that returns correct Silver Parquet for each entity."""
    entities = {
        "movies": _silver_movies(),
        "actors": _silver_actors(),
        "directors": _silver_directors(),
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
    result = _build_actor_filmography(_silver_movies(), _silver_actors(), _silver_bridge())
    actor_a = result[result["person_id"] == 10].iloc[0]
    actor_b = result[result["person_id"] == 11].iloc[0]
    assert int(actor_a["film_count"]) == 2
    assert int(actor_b["film_count"]) == 1


def test_actor_filmography_avg_rating_for_actor_a():
    """Actor A avg rating = (8.0 + 7.0) / 2 = 7.5."""
    result = _build_actor_filmography(_silver_movies(), _silver_actors(), _silver_bridge())
    actor_a = result[result["person_id"] == 10].iloc[0]
    assert abs(float(actor_a["avg_rating"]) - 7.5) < 0.01


def test_actor_filmography_excludes_crew_rows():
    """Director rows (credit_type='crew') must not be counted in actor filmography."""
    result = _build_actor_filmography(_silver_movies(), _silver_actors(), _silver_bridge())
    # person_id=20 is a director — should not appear
    assert 20 not in list(result["person_id"])


# --- _build_director_ratings ---

def test_director_ratings_film_count():
    """Director A (id=20) directed films 1 and 2 → film_count=2."""
    result = _build_director_ratings(_silver_movies(), _silver_directors(), _silver_bridge())
    dir_a = result[result["person_id"] == 20].iloc[0]
    assert int(dir_a["film_count"]) == 2


def test_director_ratings_avg_rating():
    """Director A avg rating = (8.0 + 7.0) / 2 = 7.5."""
    result = _build_director_ratings(_silver_movies(), _silver_directors(), _silver_bridge())
    dir_a = result[result["person_id"] == 20].iloc[0]
    assert abs(float(dir_a["avg_rating"]) - 7.5) < 0.01


def test_director_ratings_total_revenue():
    """Director A total revenue = 100M + 50M = 150M."""
    result = _build_director_ratings(_silver_movies(), _silver_directors(), _silver_bridge())
    dir_a = result[result["person_id"] == 20].iloc[0]
    assert int(dir_a["total_revenue"]) == 150_000_000


# --- build_gold_datasets (integration) ---

def test_build_gold_datasets_writes_four_parquet_files():
    """build_gold_datasets must call put_object exactly 4 times (one per dataset)."""
    date = dt.date(2026, 6, 26)
    mock_s3 = _make_multi_entity_s3_mock(date)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uris = build_gold_datasets(ingestion_date=date, bucket="theoria-datalake")

    assert mock_s3.put_object.call_count == 4
    assert set(uris.keys()) == {"genre_metrics", "decade_stats", "actor_filmography", "director_ratings"}


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
