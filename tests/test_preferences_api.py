"""Tests for private account data the future recommender will consume."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import django
import pytest

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


TEST_EMAIL = "preferences-api@example.com"


@pytest.fixture(autouse=True)
def clean_api_user():
    get_user_model().objects.filter(email=TEST_EMAIL).delete()
    yield
    get_user_model().objects.filter(email=TEST_EMAIL).delete()


def _client_for_user():
    user = get_user_model().objects.create_user(
        email=TEST_EMAIL,
        username="preference-api-reader",
        password="PrivateScreening2026!",
    )
    client = Client()
    client.force_login(user)
    return client, user


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_preference_api_reads_and_updates_one_title():
    client, user = _client_for_user()
    url = reverse(
        "account_api:preference_detail",
        kwargs={"content_type": "movie", "content_id": 550},
    )

    response = client.get(url)
    assert response.status_code == 200
    assert response.json()["preferences"] == {
        Collection.LIKED: False,
        Collection.DISLIKED: False,
        Collection.WATCH_LATER: False,
        Collection.TOP: False,
    }

    with patch("core.services._content_exists", return_value=True):
        response = client.generic(
            "PATCH",
            url,
            data=json.dumps({Collection.LIKED: True, Collection.WATCH_LATER: True}),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert response.json()["preferences"][Collection.LIKED] is True
    assert response.json()["preferences"][Collection.WATCH_LATER] is True
    assert CollectionItem.objects.filter(collection__user=user, content_id=550).count() == 2


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_dislike_api_clears_the_opposite_preference():
    client, _ = _client_for_user()
    url = reverse(
        "account_api:preference_detail",
        kwargs={"content_type": "series", "content_id": 1396},
    )

    with patch("core.services._content_exists", return_value=True):
        client.generic(
            "PATCH", url, data=json.dumps({Collection.LIKED: True}), content_type="application/json"
        )
        response = client.generic(
            "PATCH", url, data=json.dumps({Collection.DISLIKED: True}), content_type="application/json"
        )

    assert response.status_code == 200
    assert response.json()["preferences"][Collection.LIKED] is False
    assert response.json()["preferences"][Collection.DISLIKED] is True


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_preference_lists_are_private_and_include_taste_profile():
    anonymous = Client()
    url = reverse("account_api:preference_lists")
    response = anonymous.get(url)
    assert response.status_code == 302
    assert response["Location"] == "/auth/login/?next=/api/account/preferences/"

    client, _ = _client_for_user()
    response = client.get(url)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["collections"]) == {
        Collection.LIKED,
        Collection.DISLIKED,
        Collection.WATCH_LATER,
        Collection.TOP,
    }
    assert payload["taste_profile"] == {
        "counts": {"liked": 0, "disliked": 0, "watch_later": 0, "top": 0},
        "preferred_genres": [],
        "avoided_genres": [],
        "watch_later_genres": [],
    }
