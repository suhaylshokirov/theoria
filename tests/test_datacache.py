"""Task 113: the cache foundation -- fail-open backend, key contract, datacache."""

import os
import pickle
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

import pytest  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from redis.exceptions import ConnectionError as RedisConnectionError  # noqa: E402

from accounts.ratelimit import is_rate_limited  # noqa: E402
from core import cache_backend, datacache  # noqa: E402
from core.cache_backend import ResilientRedisCache, build_caches  # noqa: E402
from core.middleware import CacheTimingMiddleware  # noqa: E402


class FakeRedisClient:
    """Stands in for Django's RedisCacheClient: same method names, a dict inside.

    Mirrors the one behaviour the rate limiter depends on -- incr on a missing
    key raises ValueError -- and can be told to fail like a dead server.
    """

    def __init__(self, fail=False):
        self.store = {}
        self.fail = fail
        self.calls = 0

    def _go(self):
        self.calls += 1
        if self.fail:
            raise RedisConnectionError("Error 111 connecting to localhost:6379. Connection refused.")

    def get(self, key, default):
        self._go()
        return self.store.get(key, default)

    def set(self, key, value, timeout):
        self._go()
        self.store[key] = value

    def add(self, key, value, timeout):
        self._go()
        return self.store.setdefault(key, value) is value

    def incr(self, key, delta):
        self._go()
        if key not in self.store:
            raise ValueError(f"Key '{key}' not found.")
        self.store[key] += delta
        return self.store[key]

    def delete(self, key):
        self._go()
        return self.store.pop(key, None) is not None

    def get_many(self, keys):
        self._go()
        return {k: self.store[k] for k in keys if k in self.store}

    def set_many(self, data, timeout):
        self._go()
        self.store.update(data)


def redis_shaped_cache(client):
    backend = ResilientRedisCache(
        "redis://localhost:6379",
        {
            "KEY_PREFIX": "theoria",
            "KEY_FUNCTION": cache_backend.make_cache_key,
        },
    )
    backend._cache = client  # replaces the cached_property; no socket is ever opened
    return backend


# --- settings ----------------------------------------------------------------


def test_without_redis_url_the_cache_is_in_process():
    caches = build_caches("")
    assert caches["default"]["BACKEND"].endswith("LocMemCache")


def test_with_redis_url_the_cache_is_the_resilient_backend_with_short_timeouts():
    default = build_caches("rediss://example:6379")["default"]
    assert default["BACKEND"] == "core.cache_backend.ResilientRedisCache"
    assert default["LOCATION"] == "rediss://example:6379"
    assert default["OPTIONS"]["socket_connect_timeout"] <= 0.5
    assert default["OPTIONS"]["socket_timeout"] <= 0.5


def test_the_socket_timeout_can_be_raised_for_a_distant_runner():
    default = build_caches("rediss://example:6379", socket_timeout=10)["default"]
    assert default["OPTIONS"] == {"socket_connect_timeout": 10, "socket_timeout": 10}


def test_deployed_without_redis_warns_instead_of_raising(caplog):
    with caplog.at_level("WARNING", logger="core.cache_backend"):
        caches = build_caches("", on_vercel=True)
    assert caches["default"]["BACKEND"].endswith("LocMemCache")
    assert "REDIS_URL is not set" in caplog.text


def test_key_contract_matches_what_the_pipeline_writes():
    # Task 114 writes the raw key "theoria:data_version" with a plain redis
    # client; the site must read the same bytes -- no version segment.
    backend = redis_shaped_cache(FakeRedisClient())
    assert backend.make_key("data_version") == "theoria:data_version"
    assert cache_backend.make_cache_key("x", "", 1) == "x"


# --- keys ---------------------------------------------------------------------


def test_cache_schema_is_an_int():
    # The comment above CACHE_SCHEMA promises someone bumps it; a string or
    # None here would make that promise unfalsifiable.
    assert isinstance(datacache.CACHE_SCHEMA, int)


def test_same_inputs_make_the_same_key():
    with patch.object(datacache, "data_version", return_value="v1"):
        assert datacache.make_key("home", 3, lang="ru") == datacache.make_key("home", 3, lang="ru")


def test_key_differs_by_language():
    with patch.object(datacache, "data_version", return_value="v1"):
        assert datacache.make_key("home", lang="ru") != datacache.make_key("home", lang="en")


def test_key_differs_by_data_version():
    with patch.object(datacache, "data_version", return_value="v1"):
        old = datacache.make_key("home", lang="ru")
    with patch.object(datacache, "data_version", return_value="v2"):
        new = datacache.make_key("home", lang="ru")
    assert old != new


def test_key_shape_with_and_without_a_version():
    with patch.object(datacache, "data_version", return_value="2026-10-07.1"):
        assert datacache.make_key("home", lang="ru") == f"home:s{datacache.CACHE_SCHEMA}:2026-10-07.1:ru"
    with patch.object(datacache, "data_version", return_value=None):
        assert datacache.make_key("home", 5) == f"home:s{datacache.CACHE_SCHEMA}:ttl:5"


def test_data_version_reads_the_published_key():
    assert datacache.data_version() is None
    cache.set("data_version", "2026-10-07.1")
    assert datacache.data_version() == "2026-10-07.1"


# --- cached() -----------------------------------------------------------------


def test_cached_builds_once_then_serves_from_cache():
    builder = MagicMock(return_value=[1, 2, 3])
    assert datacache.cached("n", builder) == [1, 2, 3]
    assert datacache.cached("n", builder) == [1, 2, 3]
    assert builder.call_count == 1


def test_cached_rebuilds_when_the_data_version_changes():
    builder = MagicMock(side_effect=["old", "new"])
    cache.set("data_version", "v1")
    assert datacache.cached("n", builder) == "old"
    cache.set("data_version", "v2")
    assert datacache.cached("n", builder) == "new"
    assert builder.call_count == 2


def test_cached_keeps_languages_apart():
    assert datacache.cached("n", lambda: "привет", lang="ru") == "привет"
    assert datacache.cached("n", lambda: "hello", lang="en") == "hello"


def test_cached_hands_back_a_fresh_copy_each_time():
    # accounts/analytics code mutates cached rows in place (localize_rows);
    # that is only safe because a hit never returns a shared object.
    datacache.cached("rows", lambda: [{"name": "Drama"}])
    first = datacache.cached("rows", lambda: [])
    first[0]["name"] = "Драма"
    assert datacache.cached("rows", lambda: [])[0]["name"] == "Drama"


def test_ttl_is_long_with_a_version_and_short_without():
    with patch.object(datacache, "cache") as fake:
        fake.get.side_effect = lambda key, default=None: default if key != "data_version" else "v1"
        datacache.cached("n", lambda: 1)
        assert fake.set.call_args.kwargs["timeout"] == datacache.TTL_VERSIONED
    with patch.object(datacache, "cache") as fake:
        fake.get.side_effect = lambda key, default=None: default
        datacache.cached("n", lambda: 1)
        assert fake.set.call_args.kwargs["timeout"] == datacache.TTL_UNVERSIONED
        datacache.cached("n", lambda: 1, ttl=7)
        assert fake.set.call_args.kwargs["timeout"] == 7


def test_cached_survives_a_backend_that_raises():
    broken = MagicMock()
    broken.get.side_effect = RedisConnectionError("down")
    broken.set.side_effect = RedisConnectionError("down")
    with patch.object(datacache, "cache", broken):
        assert datacache.cached("n", lambda: "built") == "built"


def test_a_corrupt_payload_is_a_miss_and_is_overwritten():
    # A truncated or foreign pickle surfaces as an exception from cache.get().
    broken = MagicMock()
    broken.get.side_effect = lambda key, default=None: (
        "v1" if key == "data_version" else (_ for _ in ()).throw(pickle.UnpicklingError("truncated"))
    )
    with patch.object(datacache, "cache", broken):
        assert datacache.cached("n", lambda: "rebuilt") == "rebuilt"
    assert broken.set.call_args.args[1] == "rebuilt"


def test_an_oversize_value_is_returned_but_not_stored(caplog):
    big = "x" * (datacache.MAX_VALUE_BYTES + 1)
    with caplog.at_level("WARNING", logger="core.datacache"):
        assert datacache.cached("big", lambda: big) == big
    assert "exceeds" in caplog.text
    builder = MagicMock(return_value=big)
    datacache.cached("big", builder)
    assert builder.call_count == 1  # nothing was stored, so it built again


def test_an_unpicklable_value_is_returned_but_not_stored():
    value = lambda: None  # noqa: E731 - lambdas cannot be pickled
    assert datacache.cached("fn", lambda: value) is value


# --- the resilient backend ------------------------------------------------------


def test_backend_get_and_set_swallow_connection_errors():
    backend = redis_shaped_cache(FakeRedisClient(fail=True))
    assert backend.get("k", "fallback") == "fallback"
    backend.set("k", 1)  # must not raise
    assert backend.get_many(["a", "b"]) == {}
    assert backend.delete("k") is False


def test_backend_works_normally_while_redis_is_up():
    backend = redis_shaped_cache(FakeRedisClient())
    backend.set("k", {"a": 1})
    assert backend.get("k") == {"a": 1}
    assert backend.get("missing", "d") == "d"
    backend.set_many({"x": 1, "y": 2})
    assert backend.get_many(["x", "y", "z"]) == {"x": 1, "y": 2}


def test_down_flag_skips_redis_until_the_window_closes():
    client = FakeRedisClient(fail=True)
    backend = redis_shaped_cache(client)
    clock = [1000.0]
    with patch.object(cache_backend.time, "monotonic", lambda: clock[0]):
        backend.get("k")
        assert client.calls == 1
        backend.get("k")
        backend.set("k", 1)
        assert client.calls == 1  # skipped: no second timeout paid
        clock[0] += cache_backend.DOWN_SECONDS + 1
        client.fail = False
        backend.set("k", 1)
        assert client.calls == 2  # window over, Redis is tried again
        assert backend.get("k") == 1


def test_down_flag_is_shared_by_every_instance():
    # Django hands each thread its own cache instance; a flag kept per
    # instance would cost every thread (every request, on a threaded server) a
    # fresh socket timeout instead of one per process per window.
    first_client = FakeRedisClient(fail=True)
    first = redis_shaped_cache(first_client)
    second_client = FakeRedisClient(fail=True)
    second = redis_shaped_cache(second_client)
    first.get("k")
    assert first_client.calls == 1
    second.get("k")
    assert second_client.calls == 0  # skipped: the first instance already learned


def test_incr_on_a_missing_key_still_raises_value_error():
    backend = redis_shaped_cache(FakeRedisClient())
    with pytest.raises(ValueError):
        backend.incr("nope")


def test_incr_while_redis_is_down_reads_as_a_missing_key():
    backend = redis_shaped_cache(FakeRedisClient(fail=True))
    with pytest.raises(ValueError):
        backend.incr("k")


# --- rate limiting through the Redis-shaped path ----------------------------------


def test_rate_limit_counts_through_the_redis_backend():
    client = FakeRedisClient()
    backend = redis_shaped_cache(client)
    request = RequestFactory().post("/signup/", REMOTE_ADDR="203.0.113.9")
    with patch("accounts.ratelimit.cache", backend):
        assert not is_rate_limited(request, scope="signup", limit=2, window_seconds=60)
        assert not is_rate_limited(request, scope="signup", limit=2, window_seconds=60)
        assert is_rate_limited(request, scope="signup", limit=2, window_seconds=60)
    assert "theoria:ratelimit:signup:203.0.113.9" in client.store


def test_rate_limit_fails_open_when_redis_is_down():
    backend = redis_shaped_cache(FakeRedisClient(fail=True))
    request = RequestFactory().post("/signup/", REMOTE_ADDR="203.0.113.9")
    with patch("accounts.ratelimit.cache", backend):
        for _ in range(5):
            assert not is_rate_limited(request, scope="signup", limit=2, window_seconds=60)


# --- Server-Timing -------------------------------------------------------------------


def test_server_timing_header_lists_hits_and_misses():
    def view(request):
        datacache.cached("home", lambda: 1)
        datacache.cached("labels", lambda: 2)
        datacache.cached("home", lambda: 1)
        return HttpResponse("ok")

    response = CacheTimingMiddleware(view)(RequestFactory().get("/"))
    assert response["Server-Timing"] == 'cache;desc="home=miss,labels=miss,home=hit"'


def test_no_server_timing_header_when_nothing_was_cached():
    response = CacheTimingMiddleware(lambda request: HttpResponse("ok"))(RequestFactory().get("/"))
    assert "Server-Timing" not in response


def test_data_version_is_read_once_per_request():
    cache.set("data_version", "v1")
    real_get = cache.get
    seen = []

    def spy(key, default=None, **kw):
        seen.append(key)
        return real_get(key, default, **kw)

    with patch.object(datacache, "cache") as fake:
        fake.get.side_effect = spy
        with datacache.request_scope():
            datacache.cached("a", lambda: 1)
            datacache.cached("b", lambda: 2)
    assert seen.count("data_version") == 1
