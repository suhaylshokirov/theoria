"""IP-scoped request throttling for auth views.

Separate from codes.py's per-email/purpose cooldown, which stops one address
from being issued too many codes -- this stops one IP from hammering the
signup endpoint itself with many different usernames/emails (enumeration,
scripted abuse). Backed by Django's cache (LocMemCache, this project's
unconfigured default), so a count resets on process restart and isn't shared
across serverless instances on Vercel -- an accepted trade-off at this
project's scale, the same posture codes.py already takes for its own,
lower-stakes limits.
"""

from __future__ import annotations

from django.core.cache import cache


def _client_ip(request) -> str:
    # Vercel (and any proxy) puts the real address first in X-Forwarded-For;
    # REMOTE_ADDR alone would just be the proxy.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def is_rate_limited(request, *, scope: str, limit: int, window_seconds: int) -> bool:
    """True if this IP has already made `limit` requests for `scope` in this window.

    Every call counts, including the one that trips the limit -- callers
    should call this once per request they want throttled, whether or not
    that request turns out to be valid.
    """
    key = f"ratelimit:{scope}:{_client_ip(request)}"
    count = cache.get(key)
    if count is None:
        cache.set(key, 1, timeout=window_seconds)
        return False
    if count >= limit:
        return True
    try:
        cache.incr(key)
    except ValueError:
        # Key expired between the get() and the incr() -- treat as fresh.
        cache.set(key, 1, timeout=window_seconds)
    return False
