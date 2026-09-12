"""End-to-end tests for email-and-password auth and personal collections."""

from __future__ import annotations

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
from django.contrib.auth.hashers import check_password  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402

from core.models import Collection, CollectionItem  # noqa: E402


TEST_EMAILS = {"password-new@example.com", "password-existing@example.com"}


@pytest.fixture(autouse=True)
def clean_test_accounts():
    get_user_model().objects.filter(email__in=TEST_EMAILS).delete()
    yield
    get_user_model().objects.filter(email__in=TEST_EMAILS).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_signup_stores_password_and_logs_user_in():
    client = Client()
    response = client.post(
        reverse("core:signup"),
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "Password-New@Example.com",
            "password": "AnalyticalEngine2026!",
            "next": "/movies/",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == "/movies/"
    user = get_user_model().objects.get(email="password-new@example.com")
    assert user.first_name == "Ada"
    assert user.last_name == "Lovelace"
    assert user.username == "ada-lovelace"
    assert user.password != "AnalyticalEngine2026!"
    assert check_password("AnalyticalEngine2026!", user.password)
    assert set(user.collections.values_list("kind", flat=True)) == {
        Collection.LIKED,
        Collection.DISLIKED,
        Collection.WATCH_LATER,
        Collection.TOP,
    }
    assert client.session["_auth_user_id"] == str(user.pk)


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_login_requires_correct_email_and_password():
    user = get_user_model().objects.create_user(
        email="password-existing@example.com",
        username="existing-reader",
        password="PrivateScreening2026!",
    )
    client = Client()
    response = client.post(
        reverse("core:login"),
        {
            "email": "PASSWORD-EXISTING@Example.com",
            "password": "PrivateScreening2026!",
            "next": "/analytics/",
        },
    )

    assert response.status_code == 302
    assert response["Location"] == "/analytics/"
    assert client.session["_auth_user_id"] == str(user.pk)

    client.logout()
    response = client.post(
        reverse("core:login"),
        {"email": "password-existing@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 400
    assert b"Email or password is not correct." in response.content


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_protected_routes_redirect_and_public_routes_remain_open():
    client = Client()
    for path in ("/", "/movies/", "/tv/", "/people/", "/studios/"):
        assert client.get(path).status_code == 200
    for path in ("/movies/example/", "/tv/example/", "/people/example/", "/analytics/"):
        response = client.get(path)
        assert response.status_code == 302
        assert response["Location"] == "/auth/login/?next=" + path


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_authenticated_user_can_toggle_warehouse_content_in_collections():
    user = get_user_model().objects.create_user(
        email="password-existing@example.com",
        username="existing-reader",
        password="PrivateScreening2026!",
    )
    client = Client()
    client.force_login(user)
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


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_disliking_a_title_removes_its_like():
    user = get_user_model().objects.create_user(
        email="password-existing@example.com",
        username="existing-reader",
        password="PrivateScreening2026!",
    )
    client = Client()
    client.force_login(user)
    like_url = reverse(
        "account:toggle_collection",
        kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
    )
    dislike_url = reverse(
        "account:toggle_collection",
        kwargs={"kind": "disliked", "content_type": "movie", "content_id": 550},
    )

    with patch("core.services._content_exists", return_value=True):
        client.post(like_url, {"next": "/movies/example/"})
        client.post(dislike_url, {"next": "/movies/example/"})

    assert not CollectionItem.objects.filter(
        collection__user=user,
        collection__kind=Collection.LIKED,
        content_id=550,
    ).exists()
    assert CollectionItem.objects.filter(
        collection__user=user,
        collection__kind=Collection.DISLIKED,
        content_id=550,
    ).exists()
