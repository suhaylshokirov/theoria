"""End-to-end tests for email-code auth and personal collections."""

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

from django.core import mail  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.auth.hashers import check_password  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402

from core.models import Collection, CollectionItem, EmailCode  # noqa: E402


TEST_EMAILS = {"otp-new@example.com", "otp-existing@example.com"}


def _clear_outbox():
    getattr(mail, "outbox", []).clear()


@pytest.fixture(autouse=True)
def clean_test_accounts():
    _clear_outbox()
    get_user_model().objects.filter(email__in=TEST_EMAILS).delete()
    EmailCode.objects.filter(email__in=TEST_EMAILS).delete()
    yield
    _clear_outbox()
    get_user_model().objects.filter(email__in=TEST_EMAILS).delete()
    EmailCode.objects.filter(email__in=TEST_EMAILS).delete()


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
def test_signup_sends_hashed_code_and_logs_user_in():
    client = Client()
    with patch("core.services.secrets.randbelow", return_value=123456):
        response = client.post(
            reverse("core:signup"),
            {"username": "new-reader", "email": "OTP-New@Example.com", "next": "/movies/"},
        )

    assert response.status_code == 302
    assert response["Location"] == reverse("core:verify_code")
    assert len(getattr(mail, "outbox", [])) == 1
    assert "123456" in mail.outbox[0].body
    challenge = EmailCode.objects.get(email="otp-new@example.com")
    assert challenge.code_hash != "123456"
    assert check_password("123456", challenge.code_hash)

    response = client.post(reverse("core:verify_code"), {"code": "123456"})

    assert response.status_code == 302
    assert response["Location"] == "/movies/"
    user = get_user_model().objects.get(email="otp-new@example.com")
    assert user.username == "new-reader"
    assert user.has_usable_password() is False
    assert set(user.collections.values_list("kind", flat=True)) == {
        Collection.LIKED,
        Collection.WATCH_LATER,
        Collection.TOP,
    }
    assert client.session["_auth_user_id"] == str(user.pk)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
def test_login_requires_email_only_and_verifies_code():
    user = get_user_model().objects.create_user(
        email="otp-existing@example.com", username="existing-reader"
    )
    client = Client()
    with patch("core.services.secrets.randbelow", return_value=654321):
        response = client.post(
            reverse("core:login"),
            {"email": "OTP-Existing@Example.com", "next": "/analytics/"},
        )

    assert response.status_code == 302
    assert len(getattr(mail, "outbox", [])) == 1
    challenge = EmailCode.objects.get(email="otp-existing@example.com")
    assert challenge.purpose == EmailCode.LOGIN

    response = client.post(reverse("core:verify_code"), {"code": "654321"})

    assert response.status_code == 302
    assert response["Location"] == "/analytics/"
    assert client.session["_auth_user_id"] == str(user.pk)


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
        email="otp-existing@example.com", username="existing-reader"
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
