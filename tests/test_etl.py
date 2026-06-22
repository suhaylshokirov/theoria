"""Unit tests for the ETL layer.

These tests mock all HTTP so they never touch the network: they verify the
TMDBClient's retry/backoff and error-handling logic in isolation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl import s3_utils
from etl.tmdb_client import TMDBAPIError, TMDBClient


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
