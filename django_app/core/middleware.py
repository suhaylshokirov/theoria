from __future__ import annotations

from django.conf import settings
from django.utils import translation
from django.utils.cache import patch_cache_control, patch_vary_headers


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


class LanguageCookieMiddleware:
    """Pick the request's language from the language cookie, and nothing else.

    A page has one URL in every language, so the reader's choice has to live
    somewhere that is not the address: the `django_language` cookie that
    set_language writes. No cookie, or a value that is not a configured
    language, means English. `Accept-Language` is deliberately ignored -- a
    Russian-locale browser would otherwise land on the machine-drafted
    Russian text unasked. Django's LocaleMiddleware can't be told to skip it,
    hence this small replacement.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.supported = {code for code, _name in settings.LANGUAGES}

    def __call__(self, request):
        code = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if code not in self.supported:
            code = settings.LANGUAGE_CODE
        translation.activate(code)
        request.LANGUAGE_CODE = code
        response = self.get_response(request)
        # One URL, three renderings: any cache has to key on the cookie.
        patch_vary_headers(response, ("Cookie",))
        if "Content-Language" not in response:
            response["Content-Language"] = code
        return response
