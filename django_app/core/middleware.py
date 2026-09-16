from __future__ import annotations

from django.utils.cache import patch_cache_control


class PrivatePagesMiddleware:
    """Keep rendered pages out of every cache that could serve them stale.

    A page's nav depends on who's signed in, but Django sends no
    Cache-Control by default -- leaving it to each browser (and any cache
    between) whether to reuse a copy rendered for a different sign-in state.
    `private` keeps shared caches out; `no-cache` makes the browser ask again
    on every normal navigation. Responses that already chose a policy (e.g.
    never_cache on the sign-in views) keep it. The back/forward cache ignores
    this header; theoria.js handles that case.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not response.has_header("Cache-Control") and response.get(
            "Content-Type", ""
        ).startswith("text/html"):
            patch_cache_control(response, private=True, no_cache=True)
        return response
