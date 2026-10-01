"""Small, explainable recommendation rules for the AI Movie Companion.

This is intentionally useful without an AI provider. Grok will later explain
the selected movies in a natural voice, but this module decides which catalogue
movies are safe and relevant enough to offer in the first place.
"""

from __future__ import annotations

import re

from django.db.models import F, Max, Q

from core.models import Collection
from core.services import build_taste_summary

MAX_RESULTS = 3
CATALOGUE_POOL_SIZE = 30

ASSESSMENT_PHRASES = (
    "my taste",
    "assess",
    "opinion",
    "what do you think",
    "how would you describe",
    "review my",
    "analyze my",
    "analyse my",
    "describe my",
    "understand my",
)

GENRE_WORDS = {
    "action": "Action",
    "animated": "Animation",
    "animation": "Animation",
    "comedy": "Comedy",
    "crime": "Crime",
    "documentary": "Documentary",
    "drama": "Drama",
    "family": "Family",
    "fantasy": "Fantasy",
    "funny": "Comedy",
    "horror": "Horror",
    "romance": "Romance",
    "romantic": "Romance",
    "scary": "Horror",
    "sci-fi": "Science Fiction",
    "science fiction": "Science Fiction",
    "thriller": "Thriller",
}


def is_taste_assessment(request_text):
    """True when the person is asking about their taste, not for new picks."""
    message = request_text.lower()
    return any(phrase in message for phrase in ASSESSMENT_PHRASES)


def read_request_constraints(request_text):
    """Turn a few common user phrases into clear catalogue filters."""
    message = request_text.lower()
    excluded_genres = set()
    genres = set()
    for word, genre in GENRE_WORDS.items():
        if word not in message:
            continue
        escaped_word = re.escape(word)
        if re.search(
            rf"\b(?:no|not|without|avoid)\s+{escaped_word}\b|"
            rf"\b(?:do not|don't)\s+(?:want|recommend|show).*?\b{escaped_word}\b|"
            rf"\bnot in (?:the )?mood for\s+{escaped_word}\b",
            message,
        ):
            excluded_genres.add(genre)
        else:
            genres.add(genre)
    duration = re.search(r"(?:under|less than|within)\s+(\d{2,3})\s*(?:minutes?|mins?)", message)
    if duration is None:
        duration = re.search(r"(\d{2,3})\s*(?:minutes?|mins?)", message)
    max_runtime = int(duration.group(1)) if duration else None
    if max_runtime is None and "short" in message:
        max_runtime = 100

    return {
        "genres": genres - excluded_genres,
        "excluded_genres": excluded_genres,
        "max_runtime": max_runtime,
    }


def _catalogue_movie_candidates(constraints):
    """Get a small, highly rated pool of movies matching the clear filters."""
    from movies.models import Movie

    movies = Movie.objects.using("warehouse").filter(runtime__isnull=False)
    if constraints["max_runtime"] is not None:
        movies = movies.filter(runtime__lte=constraints["max_runtime"])
    if constraints["genres"]:
        movies = movies.filter(
            moviemetrics__genre__genre_name__in=constraints["genres"]
        ).distinct()
    if constraints["excluded_genres"]:
        movies = movies.exclude(
            moviemetrics__genre__genre_name__in=constraints["excluded_genres"]
        )
    movies = (
        movies.annotate(
            imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb"))
        )
        .order_by(F("imdb_rating").desc(nulls_last=True), "title")[:CATALOGUE_POOL_SIZE]
    )
    return [
        {
            "content_id": movie.movie_id,
            "content_type": "movie",
            "title": movie.title,
            "slug": movie.slug,
            "runtime": movie.runtime,
            "year": movie.release_date.year if movie.release_date else None,
            "imdb_rating": movie.imdb_rating,
        }
        for movie in movies
    ]


def _known_movie_ids(taste_summary, list_kind):
    return {
        title["content_id"]
        for title in taste_summary.get(list_kind, [])
        if title["content_type"] == "movie"
    }


def _reason_for(candidate, constraints, saved_for_later):
    if candidate["content_id"] in saved_for_later:
        return "It is already on your Watch later list."

    details = []
    if constraints["genres"]:
        details.append("matches your requested genre")
    if constraints["excluded_genres"]:
        details.append("avoids the genres you ruled out")
    if constraints["max_runtime"] is not None:
        details.append(f"fits your {constraints['max_runtime']}-minute limit")
    if details:
        return "It " + " and ".join(details) + "."
    return "It is a highly rated option from the Theoria catalogue."


def rank_movie_candidates(taste_summary, candidates, constraints, *, exclude_movie_ids=()):
    """Exclude known or rejected movies and put Watch later films first."""
    excluded_movie_ids = _known_movie_ids(taste_summary, Collection.LIKED)
    excluded_movie_ids.update(_known_movie_ids(taste_summary, Collection.TOP))
    excluded_movie_ids.update(_known_movie_ids(taste_summary, "watched"))
    excluded_movie_ids.update(_known_movie_ids(taste_summary, "disliked"))
    excluded_movie_ids.update(_known_movie_ids(taste_summary, "not_interested"))
    excluded_movie_ids.update(exclude_movie_ids)
    saved_for_later = _known_movie_ids(taste_summary, Collection.WATCH_LATER)

    ranked = []
    for candidate in candidates:
        if candidate["content_id"] in excluded_movie_ids:
            continue
        ranked.append(
            {
                **candidate,
                "status": "on_list" if candidate["content_id"] in saved_for_later else "new",
                "reason": _reason_for(candidate, constraints, saved_for_later),
            }
        )
    ranked.sort(key=lambda candidate: candidate["status"] != "on_list")
    return ranked[:MAX_RESULTS]


def select_movie_candidates(user, request_text, *, exclude_movie_ids=(), chat_memory=None):
    """Return up to three explainable movie choices, excluding prior chat picks."""
    constraints = read_request_constraints(request_text)
    chat_memory = chat_memory if isinstance(chat_memory, dict) else {}
    remembered_exclusions = chat_memory.get("excluded_genres", [])
    if isinstance(remembered_exclusions, list):
        constraints["excluded_genres"].update(remembered_exclusions)
    remembered_runtime = chat_memory.get("max_runtime")
    if isinstance(remembered_runtime, int) and remembered_runtime > 0:
        constraints["max_runtime"] = remembered_runtime
    taste_summary = build_taste_summary(user)
    candidates = _catalogue_movie_candidates(constraints)
    return rank_movie_candidates(
        taste_summary, candidates, constraints, exclude_movie_ids=exclude_movie_ids
    )
