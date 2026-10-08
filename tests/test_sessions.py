"""Task 121: sessions are read through the cache (cached_db)."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.sessions.models import Session  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.db import connections  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.test.utils import (  # noqa: E402
    CaptureQueriesContext, setup_test_environment, teardown_test_environment,
)

from core.cache_backend import build_caches  # noqa: E402
from movies import cached_reads  # noqa: E402

User = get_user_model()
EMAIL = "sessions-test@example.com"


def setup_module(module):
    setup_test_environment()


def teardown_module(module):
    User.objects.filter(email=EMAIL).delete()
    teardown_test_environment()


def _user():
    user, _ = User.objects.get_or_create(email=EMAIL, defaults={"username": "sessions-test"})
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    return user


def _home_without_warehouse():
    """Patch the home page's cached reads so '/' touches no warehouse."""
    return (
        patch.object(cached_reads, "build_home_stats", return_value={"movie_count": 1, "person_count": 1, "avg_rating": None}),
        patch.object(cached_reads, "build_home_mosaic", return_value=[]),
        patch.object(cached_reads, "build_home_shelves", return_value={"top_rated": [], "newest": [], "recently_aired": []}),
    )


def _queries(client, path="/"):
    with CaptureQueriesContext(connections["default"]) as captured:
        response = client.get(path)
    kinds = [
        "session" if "django_session" in q["sql"] else "user" if "accounts_user" in q["sql"] else "other"
        for q in captured.captured_queries
    ]
    return response, kinds


def test_sessions_use_the_cached_db_engine():
    assert settings.SESSION_ENGINE == "django.contrib.sessions.backends.cached_db"


def test_a_signed_in_request_no_longer_reads_the_session_from_the_database():
    a, b, c = _home_without_warehouse()
    with a, b, c:
        client = Client()
        client.force_login(_user())
        _response, kinds = _queries(client)
    # The user row is still read every request (so deactivation is immediate);
    # the session itself came from the cache.
    assert kinds == ["user"]


def test_an_empty_cache_falls_back_to_the_database_then_repopulates():
    a, b, c = _home_without_warehouse()
    with a, b, c:
        client = Client()
        client.force_login(_user())
        cache.clear()  # a Redis flush, or an evicted key
        response, first = _queries(client)
        _response, second = _queries(client)
    assert response.status_code == 200
    assert "session" in first          # read from the database this once...
    assert second == ["user"]          # ...and from the cache again after


def test_a_dead_redis_still_authenticates_from_the_database():
    # A real ResilientRedisCache pointed at a closed port: connection errors
    # are swallowed, so every session read must fall through to the database.
    dead = build_caches("redis://127.0.0.1:1/0", socket_timeout=0.2)
    a, b, c = _home_without_warehouse()
    with override_settings(CACHES=dead), a, b, c:
        client = Client()
        client.force_login(_user())      # saving the session must not need Redis either
        response, kinds = _queries(client)
        me = client.get("/me/")
    assert response.status_code == 200
    assert "session" in kinds
    assert me.status_code == 200         # still signed in on a login-gated page


def test_logout_deletes_the_cached_copy_too():
    # The old cookie must stop working immediately, not when the cache expires.
    a, b, c = _home_without_warehouse()
    with a, b, c:
        client = Client()
        client.force_login(_user())
        old_cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        assert client.get("/me/").status_code == 200   # cached and signed in
        client.post("/accounts/logout/")
        replay = Client()
        replay.cookies[settings.SESSION_COOKIE_NAME] = old_cookie
        assert replay.get("/me/").status_code == 302   # bounced to sign-in
    assert not Session.objects.filter(session_key=old_cookie).exists()


def test_a_deactivated_account_is_signed_out_even_with_a_cached_session():
    a, b, c = _home_without_warehouse()
    with a, b, c:
        user = _user()
        client = Client()
        client.force_login(user)
        assert client.get("/me/").status_code == 200   # session now cached
        User.objects.filter(pk=user.pk).update(is_active=False)
        assert client.get("/me/").status_code == 302
