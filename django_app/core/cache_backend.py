"""The site's cache backend: Redis that can never take a page down.

The cache is an optimisation, so a Redis that is slow, unreachable or
misconfigured must read as "nothing cached" -- never as a 500. Django's own
RedisCache raises on every connection error; this subclass swallows them and
answers like an empty cache would.

It also remembers that Redis is down for a short window. Without that, a dead
Redis costs every call its socket timeout (and a page makes several calls);
with it, the first call pays once and the rest skip the network until the
window closes.

`make_cache_key` is the key contract the pipeline depends on: Django's default
key function inserts a version number (`theoria:1:data_version`), but the
nightly job writes the raw key `theoria:data_version` with a plain redis client
and no Django. Both sides have to agree on exactly `<prefix>:<key>`.
"""

from __future__ import annotations

import functools
import logging
import time

from django.core.cache.backends.redis import RedisCache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

KEY_PREFIX = "theoria"
# Connect and read timeouts, in seconds. Redis is in the same region as the
# function, so a healthy round trip is a few milliseconds; anything beyond
# this is an outage, not latency.
SOCKET_TIMEOUT = 0.3
# How long one failure suppresses further attempts.
DOWN_SECONDS = 30


def make_cache_key(key: str, key_prefix: str, version: int) -> str:
    """`<prefix>:<key>` -- no version segment, see the module docstring."""
    return f"{key_prefix}:{key}" if key_prefix else key


def build_caches(redis_url: str, *, on_vercel: bool = False) -> dict:
    """The CACHES setting: Redis when REDIS_URL is set, else in-process.

    A missing URL is never fatal -- the site just caches per process, which is
    what it did before Redis existed. Deployed, though, per-process caches are
    close to useless (each serverless instance has its own), so that case
    logs one warning instead of staying silent.
    """
    common = {"KEY_PREFIX": KEY_PREFIX, "KEY_FUNCTION": "core.cache_backend.make_cache_key"}
    if redis_url:
        return {
            "default": {
                "BACKEND": "core.cache_backend.ResilientRedisCache",
                "LOCATION": redis_url,
                "OPTIONS": {
                    "socket_connect_timeout": SOCKET_TIMEOUT,
                    "socket_timeout": SOCKET_TIMEOUT,
                },
                **common,
            }
        }
    if on_vercel:
        logger.warning("REDIS_URL is not set: the cache is per-process and will rarely be shared.")
    return {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            **common,
        }
    }


def _fail_open(miss):
    """Wrap a cache method so connection errors answer `miss(self, *args, **kwargs)`."""

    def decorate(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            if self._is_down():
                return miss(self, *args, **kwargs)
            try:
                return method(self, *args, **kwargs)
            except (RedisError, OSError) as exc:
                # ValueError (incr on a missing key) is deliberately not
                # caught: accounts/ratelimit.py depends on it.
                self._mark_down(exc)
                return miss(self, *args, **kwargs)

        return wrapper

    return decorate


def _no_such_key(self, key, *args, **kwargs):
    # An unreachable cache is an empty one, and incr on an empty cache is
    # Django's "key not found" -- which every caller already handles.
    raise ValueError(f"Key '{key}' not found (cache unavailable).")


class ResilientRedisCache(RedisCache):
    """RedisCache whose failures read as misses. See the module docstring."""

    _down_until = 0.0

    def _is_down(self) -> bool:
        return time.monotonic() < self._down_until

    def _mark_down(self, exc: Exception) -> None:
        self._down_until = time.monotonic() + DOWN_SECONDS
        logger.warning("Redis cache unavailable, skipping it for %ds: %s", DOWN_SECONDS, exc)

    @_fail_open(lambda self, key, default=None, *a, **kw: default)
    def get(self, key, default=None, *args, **kwargs):
        return super().get(key, default, *args, **kwargs)

    @_fail_open(lambda self, *a, **kw: False)
    def add(self, key, value, *args, **kwargs):
        return super().add(key, value, *args, **kwargs)

    @_fail_open(lambda self, *a, **kw: None)
    def set(self, key, value, *args, **kwargs):
        return super().set(key, value, *args, **kwargs)

    @_fail_open(lambda self, *a, **kw: False)
    def touch(self, key, *args, **kwargs):
        return super().touch(key, *args, **kwargs)

    @_fail_open(lambda self, *a, **kw: False)
    def delete(self, key, *args, **kwargs):
        return super().delete(key, *args, **kwargs)

    @_fail_open(lambda self, *a, **kw: {})
    def get_many(self, keys, *args, **kwargs):
        return super().get_many(keys, *args, **kwargs)

    @_fail_open(lambda self, *a, **kw: False)
    def has_key(self, key, *args, **kwargs):
        return super().has_key(key, *args, **kwargs)

    @_fail_open(_no_such_key)
    def incr(self, key, *args, **kwargs):
        return super().incr(key, *args, **kwargs)

    @_fail_open(lambda self, data, *a, **kw: list(data))
    def set_many(self, data, *args, **kwargs):
        return super().set_many(data, *args, **kwargs)

    @_fail_open(lambda self, *a, **kw: None)
    def delete_many(self, keys, *args, **kwargs):
        return super().delete_many(keys, *args, **kwargs)

    @_fail_open(lambda self, *a, **kw: None)
    def clear(self):
        return super().clear()
