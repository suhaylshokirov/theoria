"""End-to-end tests for personal collections (Liked / Watch later / Top).

Identity now lives entirely in the `accounts` app (see tests/test_accounts.py
and, once Task 96 lands, its planned sign-up/sign-in/gating suite) — this
file only covers what `core` still owns: a signed-in user's Collection /
CollectionItem rows.
"""

from __future__ import annotations

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

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402

from core.models import Collection, CollectionItem  # noqa: E402

User = get_user_model()

_TEST_EMAIL = "collections-test@example.com"


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_authenticated_user_can_toggle_warehouse_content_in_collections():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("core.services._content_exists", return_value=True):
            response = client.post(
                reverse(
                    "account:toggle_collection",
                    kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
                ),
                {"next": "/movies/example/"},
            )
        assert response.status_code == 302
        assert response["Location"] == "/movies/example/"
        item = CollectionItem.objects.get(
            collection__user=user,
            collection__kind=Collection.LIKED,
            content_type=CollectionItem.MOVIE,
            content_id=550,
        )

        with patch("core.services._content_exists", return_value=True):
            client.post(
                reverse(
                    "account:toggle_collection",
                    kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
                ),
                {"next": "/movies/example/"},
            )
        assert not CollectionItem.objects.filter(pk=item.pk).exists()
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_json_toggle_returns_selected_state_without_redirecting():
    """The icon-fill bloom (theoria.js's initCollectionActions) only has
    something to animate if the pill doesn't reload out from under it -- a
    request that says it can read JSON gets the new state back instead of
    the redirect a plain form POST gets."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        url = reverse(
            "account:toggle_collection",
            kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
        )
        with patch("core.services._content_exists", return_value=True):
            response = client.post(url, {"next": "/movies/example/"}, HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        assert response.json() == {"selected": True}
        assert CollectionItem.objects.filter(
            collection__user=user,
            collection__kind=Collection.LIKED,
            content_type=CollectionItem.MOVIE,
            content_id=550,
        ).exists()

        with patch("core.services._content_exists", return_value=True):
            response = client.post(url, {"next": "/movies/example/"}, HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert response.json() == {"selected": False}
        assert not CollectionItem.objects.filter(
            collection__user=user,
            collection__kind=Collection.LIKED,
            content_type=CollectionItem.MOVIE,
            content_id=550,
        ).exists()
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_non_json_toggle_still_redirects():
    """The no-JS / no-Accept-header path is unchanged -- a plain form POST
    (no JS, or theoria.js's fetch fallback) still gets the old redirect
    behaviour, not JSON."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("core.services._content_exists", return_value=True):
            response = client.post(
                reverse(
                    "account:toggle_collection",
                    kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
                ),
                {"next": "/movies/example/"},
            )
        assert response.status_code == 302
        assert response["Location"] == "/movies/example/"
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_remove_item_with_no_next_falls_back_to_profile():
    """account.html's remove forms send no `next` -- the reader should land
    back on /me/, not the homepage safe_next used to fall back to."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("core.services._content_exists", return_value=True):
            client.post(
                reverse(
                    "account:toggle_collection",
                    kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
                ),
                {"next": "/movies/example/"},
            )
        item = CollectionItem.objects.get(
            collection__user=user,
            collection__kind=Collection.LIKED,
            content_type=CollectionItem.MOVIE,
            content_id=550,
        )

        response = client.post(
            reverse("account:remove_item", kwargs={"kind": "liked", "item_id": item.pk})
        )

        assert response.status_code == 302
        assert response["Location"] == "/me/"
        assert not CollectionItem.objects.filter(pk=item.pk).exists()
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()
