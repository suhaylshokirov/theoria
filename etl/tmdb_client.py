"""Reusable TMDB API client.

Single entry point for every TMDB call in the project. Centralises:
- base URL + API key (read from config.py, never hardcoded here),
- a shared requests.Session (connection reuse across calls),
- retry-with-backoff for transient failures (429 + 5xx),
- a clear TMDBAPIError on persistent failure (errors are never swallowed).

All TMDB auth here is v3: the api_key is passed as a query parameter.

Usage:
    from etl.tmdb_client import TMDBClient
    client = TMDBClient()
    genres = client.get_genres()
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)

# HTTP status codes worth retrying: rate limiting + transient server errors.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class TMDBAPIError(RuntimeError):
    """Raised when a TMDB request fails permanently (after retries)."""


class TMDBClient:
    """Thin wrapper over the TMDB v3 REST API with retry/backoff."""

    def __init__(
        self,
        api_key: str = config.TMDB_API_KEY,
        base_url: str = config.TMDB_BASE_URL,
        *,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key
        # Strip trailing slash so f"{base_url}/{path}" never doubles up.
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.timeout = timeout
        # Reuse one Session across calls: connection pooling + fewer handshakes.
        self.session = session or requests.Session()

    # -- core request ------------------------------------------------------
    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a TMDB endpoint and return parsed JSON.

        Injects the api_key, retries transient failures with exponential
        backoff (honouring Retry-After on 429), and raises TMDBAPIError on
        persistent failure. Errors are logged and re-raised, never swallowed.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        request_params = {"api_key": self.api_key, **(params or {})}

        last_exc: Exception | None = None
        # attempts = the first try + max_retries follow-ups.
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    url, params=request_params, timeout=self.timeout
                )
            except requests.RequestException as exc:
                # Network-level failure (timeout, connection reset, ...).
                last_exc = exc
                if attempt < self.max_retries:
                    self._sleep_before_retry(attempt)
                    continue
                logger.error("GET %s failed after %d attempts: %s", path, attempt + 1, exc)
                raise TMDBAPIError(f"Request to {path} failed: {exc}") from exc

            if response.status_code == 200:
                logger.debug("GET %s -> 200", path)
                return response.json()

            if response.status_code in _RETRYABLE_STATUS and attempt < self.max_retries:
                logger.warning(
                    "GET %s -> %d (retryable), attempt %d/%d",
                    path, response.status_code, attempt + 1, self.max_retries,
                )
                self._sleep_before_retry(attempt, response)
                continue

            # Non-retryable status, or retries exhausted: fail loud.
            logger.error("GET %s -> %d (giving up)", path, response.status_code)
            raise TMDBAPIError(
                f"TMDB GET {path} failed with status {response.status_code}: "
                f"{response.text[:200]}"
            )

        # Loop only exits via return/raise above; this guards the type checker.
        raise TMDBAPIError(f"Request to {path} failed: {last_exc}")

    def _sleep_before_retry(
        self, attempt: int, response: requests.Response | None = None
    ) -> None:
        """Sleep before the next retry: Retry-After if given, else backoff."""
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(int(retry_after))
                return
        # Exponential backoff: 0.5s, 1s, 2s, ... with backoff_factor=0.5.
        time.sleep(self.backoff_factor * (2 ** attempt))

    # -- convenience wrappers (used by Bronze ingestion tasks) -------------
    def get_genres(self) -> dict[str, Any]:
        """Official movie genre list."""
        return self.get("genre/movie/list")

    def get_popular_movies(self, page: int = 1) -> dict[str, Any]:
        """One page of the popular-movies catalogue."""
        return self.get("movie/popular", params={"page": page})

    def get_movie_details(self, movie_id: int) -> dict[str, Any]:
        """Full detail record for a single movie."""
        return self.get(f"movie/{movie_id}")

    def get_movie_credits(self, movie_id: int) -> dict[str, Any]:
        """Cast and crew for a single movie."""
        return self.get(f"movie/{movie_id}/credits")
