"""Task 115: the home page reads through the cache."""

import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from decimal import Decimal  # noqa: E402

from django.core.cache import cache  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402
from redis.exceptions import ConnectionError as RedisConnectionError  # noqa: E402

from core import datacache  # noqa: E402
from movies import cached_reads  # noqa: E402
from movies.i18n import current_lang  # noqa: E402
from movies.models import Movie, MovieRating, Person, Series  # noqa: E402
from tests.test_django_views import _movie, _series  # noqa: E402


def setup_module(module):
    setup_test_environment()


def teardown_module(module):
    teardown_test_environment()


def _patched_warehouse():
    """Patch the four model managers the home builders query. Returns the mocks."""
    movie, show = _movie(), _series()
    stack = [
        patch.object(Movie, "objects", new=MagicMock()),
        patch.object(Series, "objects", new=MagicMock()),
        patch.object(Person, "objects", new=MagicMock()),
        patch.object(MovieRating, "objects", new=MagicMock()),
    ]
    movie_mgr, series_mgr, person_mgr, rating_mgr = (p.start() for p in stack)
    using = movie_mgr.using.return_value
    using.count.return_value = 1217
    using.annotate.return_value.order_by.return_value.__getitem__.return_value = [movie]
    using.filter.return_value.order_by.return_value.values_list.return_value \
        .__getitem__.return_value = [(movie.slug, movie.poster_path)]
    series_using = series_mgr.using.return_value
    series_using.annotate.return_value.order_by.return_value.__getitem__.return_value = [show]
    series_using.filter.return_value.order_by.return_value.values_list.return_value \
        .__getitem__.return_value = [(show.slug, show.poster_path)]
    person_mgr.using.return_value.count.return_value = 122685
    rating_mgr.using.return_value.filter.return_value.aggregate.return_value = {
        "avg_rating": Decimal("6.84")
    }
    return stack, (movie_mgr, series_mgr, person_mgr, rating_mgr)


def _stop(stack):
    for p in stack:
        p.stop()


def _without_csrf(response):
    return re.sub(rb'name="csrfmiddlewaretoken" value="[^"]+"', b"", response.content)


def test_a_second_request_makes_no_warehouse_queries():
    stack, managers = _patched_warehouse()
    try:
        client = Client()
        first = client.get("/")
        assert first.status_code == 200
        assert all(m.using.call_count > 0 for m in managers)  # the miss really queried

        for m in managers:
            m.reset_mock()
        second = client.get("/")
        assert second.status_code == 200
        # The warehouse is untouched: no .using(...), no .count(), nothing.
        assert [m.mock_calls for m in managers] == [[], [], [], []]
        # Same page, apart from the per-request CSRF token in the language
        # switcher -- which is exactly the part that must not be cached.
        assert _without_csrf(second) == _without_csrf(first)
    finally:
        _stop(stack)


def test_server_timing_shows_misses_then_hits():
    stack, _ = _patched_warehouse()
    try:
        client = Client()
        miss = client.get("/")["Server-Timing"]
        hit = client.get("/")["Server-Timing"]
    finally:
        _stop(stack)
    assert miss == 'cache;desc="home_stats=miss,home_shelves=miss,home_mosaic=miss"'
    assert hit == 'cache;desc="home_stats=hit,home_shelves=hit,home_mosaic=hit"'


def _language_aware_shelves():
    """A shelves builder whose titles depend on the active language."""
    lang = current_lang()
    movie = _movie(title=f"title-in-{lang}")
    movie.imdb_rating = None
    return {"top_rated": [movie], "newest": [movie], "recently_aired": []}


def test_one_language_never_serves_another_languages_titles():
    # The leak this guards: home_shelves holds translated titles, so the first
    # Russian reader would fill the cache for everyone if `lang` left the key.
    with patch.object(cached_reads, "build_home_shelves", _language_aware_shelves), patch.object(
        cached_reads, "build_home_stats", return_value={"movie_count": 1, "person_count": 1, "avg_rating": None}
    ), patch.object(cached_reads, "build_home_mosaic", return_value=[]):
        russian = Client()
        russian.cookies["django_language"] = "ru"
        english = Client()

        ru_page = russian.get("/").content.decode()
        en_page = english.get("/").content.decode()
        ru_again = russian.get("/").content.decode()

    assert "title-in-ru" in ru_page and "title-in-en" not in ru_page
    assert "title-in-en" in en_page and "title-in-ru" not in en_page
    assert "title-in-ru" in ru_again  # and the Russian entry survived the English request


def test_a_new_data_version_rebuilds():
    builder = MagicMock(return_value={"movie_count": 1, "person_count": 1, "avg_rating": None})
    with patch.object(cached_reads, "build_home_stats", builder):
        cache.set("data_version", "v1")
        cached_reads.home_stats()
        cached_reads.home_stats()
        assert builder.call_count == 1
        cache.set("data_version", "v2")  # the pipeline just loaded
        cached_reads.home_stats()
        assert builder.call_count == 2


def test_the_page_still_renders_when_the_cache_is_down():
    stack, managers = _patched_warehouse()
    broken = MagicMock()
    broken.get.side_effect = RedisConnectionError("down")
    broken.set.side_effect = RedisConnectionError("down")
    try:
        with patch.object(datacache, "cache", broken):
            response = Client().get("/")
    finally:
        _stop(stack)
    assert response.status_code == 200
    assert b"Test Movie" in response.content  # built from the warehouse, as before
    assert managers[0].using.call_count > 0
