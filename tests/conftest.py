"""Shared pytest fixtures.

The site's cache is Redis when REDIS_URL is set, and the developer's .env may
set it. No test should ever reach a real Redis, nor read what another test
cached, so every test runs against its own empty in-process cache.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_cache():
    from django.conf import settings

    # Test modules in this repo call django.setup() themselves at import; a run
    # that selects only non-Django tests (test_etl, ...) never configures it.
    if not settings.configured:
        yield
        return

    from django.core.cache import cache
    from django.test import override_settings

    from core.cache_backend import ResilientRedisCache

    # The "Redis is down" window is process-wide state; one test's simulated
    # outage must not suspend Redis for the next.
    ResilientRedisCache._down_until = 0.0

    caches = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "KEY_PREFIX": "theoria",
            "KEY_FUNCTION": "core.cache_backend.make_cache_key",
        }
    }
    with override_settings(CACHES=caches):
        cache.clear()
        yield
        cache.clear()


@pytest.fixture(autouse=True)
def _serial_tmdb_fetches(monkeypatch):
    """Run Bronze per-id fetches one at a time in tests.

    Many ingest tests hand the TMDB mock a list of payloads in call order; a
    thread pool would consume that list in whatever order threads finish. The
    parallel path has its own tests in test_parallel_fetch.py.
    """
    import config

    monkeypatch.setattr(config, "TMDB_FETCH_WORKERS", 1)


CARTOON_TEST_GENRE_ROWS = {
    "movie": [(16, "Animation"), (35, "Comedy"), (10751, "Family"), (878, "Science Fiction")],
    "series": [(16, "Animation"), (35, "Comedy"), (10751, "Family"), (10762, "Kids"),
               (10765, "Sci-Fi & Fantasy")],
}
# The genres some cartoon carries, besides the Animation + Family/Kids pair
# that defines one (see cached_reads.build_cartoon_genre_ids): Kids is part of
# that pair, so it is not offered although TV has it.
CARTOON_TEST_PRESENT_IDS = {"movie": [35, 878], "series": [35, 10765]}


@pytest.fixture(autouse=True)
def _cartoon_genre_lists(request):
    """/cartoons/ offers a genre cell, which reads the genre lists of both
    catalogs and which genres cartoons carry. Keep those reads off the
    warehouse for every cartoon test."""
    if "cartoon" not in request.node.name:
        yield
        return
    from unittest.mock import patch

    with patch("movies.views.cached_reads.genre_rows",
               side_effect=lambda kind: CARTOON_TEST_GENRE_ROWS[kind]), patch(
        "movies.views.cached_reads.cartoon_genre_ids", return_value=CARTOON_TEST_PRESENT_IDS
    ):
        yield
