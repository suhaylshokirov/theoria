"""Tests for the AI companion's non-AI movie-selection rules."""

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

from core.models import Collection  # noqa: E402
from core.recommendations import (  # noqa: E402
    is_taste_assessment,
    rank_movie_candidates,
    read_request_constraints,
    select_movie_candidates,
)


def test_is_taste_assessment_detects_opinion_requests():
    assert is_taste_assessment("What do you think of my taste in movies?")
    assert is_taste_assessment("Can you assess my taste?")


def test_is_taste_assessment_ignores_recommendation_requests():
    assert not is_taste_assessment("Something funny under 100 minutes")


def _title(content_id, content_type="movie"):
    return {
        "content_id": content_id,
        "content_type": content_type,
        "title": f"Title {content_id}",
        "runtime": 100,
        "year": 2024,
        "position": 0,
    }


def _candidate(content_id):
    return {
        "content_id": content_id,
        "content_type": "movie",
        "title": f"Candidate {content_id}",
        "runtime": 95,
        "year": 2024,
        "imdb_rating": 8.0,
    }


def test_read_request_constraints_understands_common_genre_and_time_words():
    constraints = read_request_constraints("I want a funny sci-fi movie under 90 minutes")

    assert constraints == {
        "genres": {"Comedy", "Science Fiction"},
        "excluded_genres": set(),
        "max_runtime": 90,
    }


def test_read_request_constraints_turns_no_horror_into_an_exclusion():
    constraints = read_request_constraints("Something fun, but no horror tonight")

    assert constraints["genres"] == set()
    assert constraints["excluded_genres"] == {"Horror"}


def test_rank_movie_candidates_skips_known_favourites_and_promotes_watch_later():
    summary = {
        Collection.LIKED: [_title(10)],
        Collection.TOP: [_title(20)],
        Collection.WATCH_LATER: [_title(30)],
    }
    constraints = {"genres": {"Comedy"}, "excluded_genres": set(), "max_runtime": 100}

    ranked = rank_movie_candidates(
        summary,
        [_candidate(10), _candidate(40), _candidate(30), _candidate(20)],
        constraints,
    )

    assert [candidate["content_id"] for candidate in ranked] == [30, 40]
    assert ranked[0]["status"] == "on_list"
    assert ranked[0]["reason"] == "It is already on your Watch later list."
    assert ranked[1]["status"] == "new"
    assert "matches your requested genre" in ranked[1]["reason"]
    assert "fits your 100-minute limit" in ranked[1]["reason"]


def test_rank_movie_candidates_excludes_watched_and_rejected_movies():
    summary = {
        Collection.LIKED: [],
        Collection.TOP: [],
        Collection.WATCH_LATER: [],
        "watched": [_title(10)],
        "disliked": [_title(20)],
        "not_interested": [_title(30)],
    }

    ranked = rank_movie_candidates(
        summary,
        [_candidate(10), _candidate(20), _candidate(30), _candidate(40)],
        {"genres": set(), "excluded_genres": set(), "max_runtime": None},
    )

    assert [candidate["content_id"] for candidate in ranked] == [40]


def test_select_movie_candidates_combines_taste_catalogue_and_request_rules():
    summary = {
        Collection.LIKED: [_title(10)],
        Collection.TOP: [],
        Collection.WATCH_LATER: [_title(30)],
    }
    with patch("core.recommendations.build_taste_summary", return_value=summary), patch(
        "core.recommendations._catalogue_movie_candidates",
        return_value=[_candidate(10), _candidate(30), _candidate(40)],
    ):
        selected = select_movie_candidates(object(), "Show me a short comedy")

    assert [candidate["content_id"] for candidate in selected] == [30, 40]
    assert all(candidate["runtime"] <= 100 for candidate in selected)


def test_select_movie_candidates_applies_a_chat_genre_exclusion():
    with patch("core.recommendations.build_taste_summary", return_value={}), patch(
        "core.recommendations._catalogue_movie_candidates", return_value=[]
    ) as catalogue:
        select_movie_candidates(
            object(),
            "Something fun",
            chat_memory={"excluded_genres": ["Horror"], "max_runtime": None},
        )

    assert catalogue.call_args.args[0]["excluded_genres"] == {"Horror"}
