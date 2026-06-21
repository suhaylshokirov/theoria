"""Unit tests for the ETL layer.

These tests mock all HTTP so they never touch the network: they verify the
TMDBClient's retry/backoff and error-handling logic in isolation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
