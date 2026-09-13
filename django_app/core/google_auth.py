"""Google OAuth sign-in endpoints linked to the local Theoria user model.

The OAuth access token is exchanged and discarded. Only the authenticated
Django user ID is retained in the application session.
"""

from __future__ import annotations

import json
import logging
import secrets
from hmac import compare_digest
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import login, logout
from django.db import IntegrityError
from django.http import HttpResponseBadRequest, HttpResponseServerError
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_GET

from core.models import User
from core.services import ensure_default_collections, safe_next

logger = logging.getLogger(__name__)

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

STATE_SESSION_KEY = "google_oauth_state"
NEXT_SESSION_KEY = "google_oauth_next"


def _redirect_uri(request):
    """Return the registered callback URI, deriving it for local development."""
    return settings.GOOGLE_REDIRECT_URI or request.build_absolute_uri(
        reverse("core:google_callback")
    )


def _configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def _json_request(request, *, url, method="GET", data=None, headers=None):
    """Make a small JSON OAuth request using Python's standard library."""
    request_headers = {"Accept": "application/json", **(headers or {})}
    body = None
    if data is not None:
        body = urlencode(data).encode("utf-8")
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"

    outgoing = Request(url, data=body, headers=request_headers, method=method)
    with urlopen(outgoing, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


@require_GET
def google_login(request):
    """Start Google's authorization-code flow."""
    if not _configured():
        return HttpResponseServerError(
            "Google sign-in is not configured. Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET."
        )

    state = secrets.token_urlsafe(32)
    request.session[STATE_SESSION_KEY] = state
    request.session[NEXT_SESSION_KEY] = safe_next(request.GET.get("next"))

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "include_granted_scopes": "true",
    }
    return redirect(f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}")


@require_GET
def google_callback(request):
    """Validate Google's response and establish a signed-session identity."""
    expected_state = request.session.pop(STATE_SESSION_KEY, None)
    next_url = request.session.pop(NEXT_SESSION_KEY, "/")
    received_state = request.GET.get("state", "")

    if not expected_state or not compare_digest(expected_state, received_state):
        return HttpResponseBadRequest("Invalid Google OAuth state.")

    if request.GET.get("error"):
        return HttpResponseBadRequest("Google sign-in was cancelled or denied.")

    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Google did not return an authorization code.")

    if not _configured():
        return HttpResponseServerError(
            "Google sign-in is not configured. Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET."
        )

    try:
        token = _json_request(
            request,
            url=GOOGLE_TOKEN_ENDPOINT,
            method="POST",
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(request),
                "grant_type": "authorization_code",
            },
        )
        access_token = token.get("access_token")
        if not access_token:
            raise ValueError("Google token response did not contain access_token")

        profile = _json_request(
            request,
            url=GOOGLE_USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except (HTTPError, URLError, ValueError, json.JSONDecodeError):
        logger.exception("Google OAuth exchange failed")
        return HttpResponseServerError("Google sign-in could not be completed.")

    if (
        not profile.get("sub")
        or not profile.get("email")
        or profile.get("email_verified") is not True
    ):
        return HttpResponseBadRequest("Google account verification failed.")

    # Link Google to the same local account used by email-code auth. The
    # verified email is the stable identity; the display name is only used to
    # suggest a first username for a new account.
    email = profile["email"].strip().casefold()
    user = User.objects.filter(email=email, is_active=True).first()
    if user is None:
        base = slugify(profile.get("name") or email.split("@", 1)[0])[:30] or "reader"
        if len(base) < 3:
            base = "reader"
        username = base
        suffix = 2
        while User.objects.filter(username__iexact=username).exists():
            tail = f"-{suffix}"
            username = f"{base[:30 - len(tail)]}{tail}"
            suffix += 1
        try:
            user = User.objects.create_user(email=email, username=username)
        except IntegrityError:
            user = User.objects.get(email=email, is_active=True)
        ensure_default_collections(user)

    request.session.flush()
    login(request, user, backend="core.auth_backends.EmailBackend")
    return redirect(safe_next(next_url))


@require_GET
def google_logout(request):
    """Clear the local Google session."""
    logout(request)
    return redirect("/")
