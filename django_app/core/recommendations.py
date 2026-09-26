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


def read_request_constraints(request_text):
    """Turn a few common user phrases into clear catalogue filters."""
    message = request_text.lower()
    genres = {genre for word, genre in GENRE_WORDS.items() if word in message}
    duration = re.search(r"(?:under|less than|within)\s+(\d{2,3})\s*(?:minutes?|mins?)", message)
    if duration is None:
        duration = re.search(r"(\d{2,3})\s*(?:minutes?|mins?)", message)
    max_runtime = int(duration.group(1)) if duration else None
    if max_runtime is None and "short" in message:
        max_runtime = 100

    return {"genres": genres, "max_runtime": max_runtime}


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
    if constraints["max_runtime"] is not None:
        details.append(f"fits your {constraints['max_runtime']}-minute limit")
    if details:
        return "It " + " and ".join(details) + "."
    return "It is a highly rated option from the Theoria catalogue."


def rank_movie_candidates(taste_summary, candidates, constraints):
    """Exclude known or rejected movies and put Watch later films first."""
    excluded_movie_ids = _known_movie_ids(taste_summary, Collection.LIKED)
    excluded_movie_ids.update(_known_movie_ids(taste_summary, Collection.TOP))
    excluded_movie_ids.update(_known_movie_ids(taste_summary, "watched"))
    excluded_movie_ids.update(_known_movie_ids(taste_summary, "disliked"))
    excluded_movie_ids.update(_known_movie_ids(taste_summary, "not_interested"))
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


def select_movie_candidates(user, request_text):
    """Return up to three explainable movie choices for one user request."""
    constraints = read_request_constraints(request_text)
    taste_summary = build_taste_summary(user)
    candidates = _catalogue_movie_candidates(constraints)
    return rank_movie_candidates(taste_summary, candidates, constraints)
