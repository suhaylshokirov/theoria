"""Tests for the first AI companion building block: saved movie taste."""

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
from django.contrib.auth.models import AnonymousUser  # noqa: E402

from core.models import Collection, CollectionItem  # noqa: E402
from core.services import build_taste_summary  # noqa: E402

User = get_user_model()
_TEST_EMAIL = "taste-summary-test@example.com"


def test_build_taste_summary_groups_titles_by_existing_list():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="taste-summary-reader")
    try:
        liked = Collection.objects.create(user=user, kind=Collection.LIKED, name="Liked")
        top = Collection.objects.create(user=user, kind=Collection.TOP, name="Top")
        watch_later = Collection.objects.create(
            user=user, kind=Collection.WATCH_LATER, name="Watch later"
        )
        CollectionItem.objects.create(
            collection=liked,
            content_type=CollectionItem.MOVIE,
            content_id=101,
        )
        CollectionItem.objects.create(
            collection=top,
            content_type=CollectionItem.SERIES,
            content_id=202,
            position=1,
        )
        CollectionItem.objects.create(
            collection=watch_later,
            content_type=CollectionItem.MOVIE,
            content_id=303,
        )

        catalogue_titles = {
            (CollectionItem.MOVIE, 101): {
                "content_id": 101,
                "content_type": CollectionItem.MOVIE,
                "title": "Arrival",
                "runtime": 116,
                "year": 2016,
            },
            (CollectionItem.SERIES, 202): {
                "content_id": 202,
                "content_type": CollectionItem.SERIES,
                "title": "Severance",
                "runtime": None,
                "year": 2022,
            },
            (CollectionItem.MOVIE, 303): {
                "content_id": 303,
                "content_type": CollectionItem.MOVIE,
                "title": "Past Lives",
                "runtime": 106,
                "year": 2023,
            },
        }
        with patch("core.services._catalogue_title_lookup", return_value=catalogue_titles):
            summary = build_taste_summary(user)

        assert summary[Collection.LIKED] == [{**catalogue_titles[(CollectionItem.MOVIE, 101)], "position": 0}]
        assert summary[Collection.TOP] == [{**catalogue_titles[(CollectionItem.SERIES, 202)], "position": 1}]
        assert summary[Collection.WATCH_LATER] == [
            {**catalogue_titles[(CollectionItem.MOVIE, 303)], "position": 0}
        ]
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


def test_build_taste_summary_returns_no_private_data_for_anonymous_user():
    assert build_taste_summary(AnonymousUser()) == {
        Collection.LIKED: [],
        Collection.WATCH_LATER: [],
        Collection.TOP: [],
        "watched": [],
        "disliked": [],
        "not_interested": [],
    }
