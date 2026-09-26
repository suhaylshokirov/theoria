"""Tests for explicit watched/disliked feedback used by recommendations."""

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

from core.models import Collection, CollectionItem, TitleFeedback  # noqa: E402
from core.services import build_taste_summary, record_title_feedback  # noqa: E402

User = get_user_model()
_TEST_EMAIL = "title-feedback-test@example.com"


def _title(content_id):
    return {
        "content_id": content_id,
        "content_type": CollectionItem.MOVIE,
        "title": f"Title {content_id}",
        "runtime": 100,
        "year": 2024,
    }


def test_record_title_feedback_saves_independent_title_signals():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="feedback-reader")
    try:
        with patch("core.services._content_exists", return_value=True):
            feedback = record_title_feedback(
                user,
                TitleFeedback.MOVIE,
                101,
                watched=True,
                disliked=True,
                personal_rating=1,
            )
            record_title_feedback(
                user,
                TitleFeedback.MOVIE,
                101,
                not_interested=True,
            )

        feedback.refresh_from_db()
        assert feedback.watched is True
        assert feedback.disliked is True
        assert feedback.not_interested is True
        assert feedback.personal_rating == 1
        assert TitleFeedback.objects.filter(user=user).count() == 1
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


def test_build_taste_summary_includes_explicit_feedback():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="feedback-reader")
    try:
        with patch("core.services._content_exists", return_value=True):
            record_title_feedback(
                user,
                TitleFeedback.MOVIE,
                101,
                watched=True,
                disliked=True,
                personal_rating=2,
            )

        with patch("core.services._catalogue_title_lookup", return_value={("movie", 101): _title(101)}):
            summary = build_taste_summary(user)

        expected = {**_title(101), "personal_rating": 2}
        assert summary["watched"] == [expected]
        assert summary["disliked"] == [expected]
        assert summary["not_interested"] == []
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()
