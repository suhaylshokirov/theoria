"""Tests for the Google OAuth redirect flow."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import django
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()


class _FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


def _request_with_session(path="/"):
    request = RequestFactory().get(path)
    SessionMiddleware(lambda _: None).process_request(request)
    return request


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    GOOGLE_CLIENT_ID="client-id",
    GOOGLE_CLIENT_SECRET="client-secret",
    GOOGLE_REDIRECT_URI="http://localhost:8000/auth/google/callback/",
)
def test_google_login_redirects_to_google_and_stores_state():
    client = Client()

    response = client.get(reverse("core:google_login"), {"next": "/movies/"})

    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert location.scheme == "https"
    assert location.netloc == "accounts.google.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["http://localhost:8000/auth/google/callback/"]
    assert query["scope"] == ["openid email profile"]
    assert query["state"]
    assert client.session["google_oauth_state"] == query["state"][0]
    assert client.session["google_oauth_next"] == "/movies/"


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    GOOGLE_CLIENT_ID="client-id",
    GOOGLE_CLIENT_SECRET="client-secret",
    GOOGLE_REDIRECT_URI="http://localhost:8000/auth/google/callback/",
)
def test_google_callback_exchanges_code_and_stores_verified_profile():
    get_user_model().objects.filter(email="reader@example.com").delete()
    client = Client()
    client.get(reverse("core:google_login"), {"next": "/movies/"})
    state = client.session["google_oauth_state"]

    with patch(
        "core.google_auth.urlopen",
        side_effect=[
            _FakeResponse({"access_token": "access-token"}),
            _FakeResponse(
                {
                    "sub": "google-sub",
                    "email": "reader@example.com",
                    "email_verified": True,
                    "name": "Example Reader",
                    "picture": "https://example.com/profile.jpg",
                }
            ),
        ],
    ) as urlopen:
        response = client.get(
            reverse("core:google_callback")
            + "?"
            + urlencode({"code": "authorization-code", "state": state})
        )

    assert response.status_code == 302
    assert response["Location"] == "/movies/"
    assert urlopen.call_count == 2
    user = get_user_model().objects.get(email="reader@example.com")
    assert user.username == "example-reader"
    assert user.email == "reader@example.com"
    assert client.session["_auth_user_id"] == str(user.pk)
    assert "google_oauth_state" not in client.session
    user.delete()


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    GOOGLE_CLIENT_ID="client-id",
    GOOGLE_CLIENT_SECRET="client-secret",
    GOOGLE_REDIRECT_URI="http://localhost:8000/auth/google/callback/",
)
def test_google_callback_rejects_invalid_state():
    client = Client()
    client.get(reverse("core:google_login"))

    with patch("core.google_auth.urlopen") as urlopen:
        response = client.get(
            reverse("core:google_callback")
            + "?"
            + urlencode({"code": "authorization-code", "state": "wrong-state"})
        )

    assert response.status_code == 400
    urlopen.assert_not_called()


def test_auth_dialog_has_email_actions():
    html = render_to_string(
        "base.html",
        {"request": _request_with_session()},
    )

    assert 'id="account-trigger"' in html
    assert "Sign in with email" in html
    assert "Create an account" in html
    assert "No password required" in html


def test_signed_in_dialog_shows_only_login_and_email():
    request = _request_with_session()
    request.user = get_user_model()(
        username="Example Reader",
        email="reader@example.com",
        is_active=True,
    )

    html = render_to_string("base.html", {"request": request})

    assert "Example Reader" in html
    assert "reader@example.com" in html
    assert "Sign in with email" not in html
    assert "Create an account" not in html
