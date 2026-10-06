"""The one API views use to cache warehouse reads.

    rows = cached("home_shelves", build_shelves, lang=get_language())

The warehouse only changes when the nightly refresh or the weekly discovery
loads it, and the site can never write to it. So instead of invalidating
entries, every key embeds a *data version* the pipeline publishes after each
committed load (`theoria:data_version`, Task 114). A new load makes every old
key unreachable; they simply expire. Nothing is ever deleted by hand.

    home_shelves:s1:2026-10-07.1760000000:ru

When no version is published (local development, a flushed Redis, no Redis at
all) the key carries `ttl` instead and entries live 15 minutes, so a missing
marker costs freshness, never correctness.

Everything here fails open: a cache that is down, slow, or holding a payload
that will not unpickle reads as a miss and the value is built from the
warehouse, exactly as it was before there was a cache.

What goes in a cache entry (see tasks.md, Caching rules):
  * the language is part of the key whenever the value holds translated text;
  * plain data, or lists of model instances for card shelves -- never a lazy
    queryset (evaluate it inside the builder), never a request, user or token.
"""

from __future__ import annotations

import contextvars
import logging
import pickle
from contextlib import contextmanager

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Bump whenever the *shape* a builder returns changes (an entry cached by the
# old code would be read by the new), and whenever Django is upgraded: pickled
# model instances are stamped with the Django version that wrote them.
CACHE_SCHEMA = 1

DATA_VERSION_KEY = "data_version"

# A versioned key is unreachable after the next load anyway, so its TTL only
# bounds how long a stale entry can occupy memory; 48 h outlasts a missed night.
TTL_VERSIONED = 48 * 60 * 60
# Without a version nothing says when the data changed, so keep entries short.
TTL_UNVERSIONED = 15 * 60

# Serialized-size ceiling for one entry. Upstash limits a single request, and
# an oversize set would fail every time -- better to say so once per miss.
MAX_VALUE_BYTES = 900_000

_MISS = object()


class _RequestScope:
    """Per-request memo of the data version, plus the hit/miss log."""

    def __init__(self) -> None:
        self.version = _MISS
        self.outcomes: list[tuple[str, str]] = []

    def server_timing(self) -> str:
        """`cache;desc="home=hit,labels=miss"`, or "" if nothing was read."""
        if not self.outcomes:
            return ""
        described = ",".join(f"{name}={outcome}" for name, outcome in self.outcomes)
        return f'cache;desc="{described}"'


_scope: contextvars.ContextVar[_RequestScope | None] = contextvars.ContextVar(
    "datacache_scope", default=None
)


@contextmanager
def request_scope():
    """Collect this request's cache outcomes and read the data version once.

    Outside a request (a management command, a test) there is no scope and
    each `cached()` call reads the version for itself.
    """
    scope = _RequestScope()
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def data_version() -> str | None:
    """The version the pipeline last published, or None."""
    scope = _scope.get()
    if scope is not None and scope.version is not _MISS:
        return scope.version
    try:
        version = cache.get(DATA_VERSION_KEY)
    except Exception:
        logger.warning("Could not read the cache data version", exc_info=True)
        version = None
    version = str(version) if version else None
    if scope is not None:
        scope.version = version
    return version


def _build_key(name: str, parts: tuple, lang: str | None, version: str | None) -> str:
    segments = [name, f"s{CACHE_SCHEMA}", version or "ttl", *(str(p) for p in parts)]
    if lang:
        segments.append(lang)
    return ":".join(segments)


def make_key(name: str, *parts, lang: str | None = None) -> str:
    """The key `cached()` would use right now (the current data version included)."""
    return _build_key(name, parts, lang, data_version())


def _record(name: str, outcome: str) -> None:
    scope = _scope.get()
    if scope is not None:
        scope.outcomes.append((name, outcome))


def cached(name: str, builder, *parts, lang: str | None = None, ttl: int | None = None):
    """Return the cached value for this key, building and storing it on a miss.

    `builder` is a zero-argument callable and must return a fully evaluated,
    picklable value. `parts` and `lang` identify which value this is; anything
    that changes what the builder returns has to be in one of them.
    """
    version = data_version()
    key = _build_key(name, parts, lang, version)

    try:
        value = cache.get(key, _MISS)
    except Exception:
        # A payload that will not unpickle (written by other code, truncated)
        # lands here too: it is a miss, and the set below overwrites it.
        logger.warning("Cache read failed for %s; rebuilding", key, exc_info=True)
        value = _MISS
    if value is not _MISS:
        _record(name, "hit")
        return value

    _record(name, "miss")
    value = builder()

    try:
        size = len(pickle.dumps(value, pickle.HIGHEST_PROTOCOL))
    except Exception:
        logger.warning("Not caching %s: value is not picklable", key, exc_info=True)
        return value
    if size > MAX_VALUE_BYTES:
        logger.warning("Not caching %s: %d bytes exceeds the %d ceiling", key, size, MAX_VALUE_BYTES)
        return value

    if ttl is None:
        ttl = TTL_VERSIONED if version else TTL_UNVERSIONED
    try:
        cache.set(key, value, timeout=ttl)
    except Exception:
        logger.warning("Cache write failed for %s", key, exc_info=True)
    return value
