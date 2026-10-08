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
