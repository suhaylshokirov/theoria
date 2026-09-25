"""Tests for the rule-based assistant HTTP endpoints."""

from __future__ import annotations

import json
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

User = get_user_model()
_TEST_EMAIL = "assistant-view-test@example.com"


def _candidate():
    return {
        "content_id": 101,
        "content_type": "movie",
        "title": "Arrival",
        "slug": "arrival",
        "status": "new",
        "reason": "It matches your requested genre.",
    }


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_chat_requires_a_signed_in_user():
    response = Client().post(
        reverse("assistant:chat"),
        data=json.dumps({"message": "Something funny"}),
        content_type="application/json",
    )

    assert response.status_code == 401
    assert "Sign in" in response.json()["error"]


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_chat_returns_rule_based_recommendations_for_signed_in_user():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("assistant.views.select_movie_candidates", return_value=[_candidate()]):
            response = client.post(
                reverse("assistant:chat"),
                data=json.dumps({"message": "Something funny"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["recommendations"] == [
            {
                "content_id": 101,
                "content_type": "movie",
                "title": "Arrival",
                "url": "/movies/arrival/",
                "status": "new",
                "reason": "It matches your requested genre.",
            }
        ]
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_feedback_records_the_selected_action():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("assistant.views.record_title_feedback") as record_feedback:
            response = client.post(
                reverse("assistant:feedback"),
                data=json.dumps(
                    {"action": "watched", "content_type": "movie", "content_id": 101}
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert "Marked as watched" in response.json()["message"]
        record_feedback.assert_called_once_with(user, "movie", 101, watched=True)
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()
