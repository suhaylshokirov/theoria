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
