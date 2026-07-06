"""Unit tests for the movies/analytics Django views.

Follows the same "mock the boundary" pattern as the rest of the suite
(tests/test_etl.py, tests/test_data_quality.py mock S3/DB engines rather
than hitting real infra): here the boundary is the `warehouse` database, so
every `Model.objects` manager is mocked and each view is exercised through
Django's test Client. Model instances themselves (Movie, Actor, ...) are
real, unsaved ORM objects — constructing one never touches the database, so
they're used as plain fixtures rather than mocked.
"""

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402

from movies.models import Actor, Casting, Director, Genre, Movie, MovieMetrics  # noqa: E402

client = Client()


def setup_module(module):
    # Enables response.context capture on the test Client (normally wired up
    # by Django's own test runner / pytest-django, neither of which is in
    # play for these plain-pytest tests).
    setup_test_environment()


def teardown_module(module):
    teardown_test_environment()


# ---------------------------------------------------------------------------
# home
# ---------------------------------------------------------------------------


def test_home_returns_200_with_expected_context():
    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Actor, "objects", new=MagicMock()
    ) as actor_mgr, patch.object(Director, "objects", new=MagicMock()) as director_mgr, patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr:
        movie_mgr.using.return_value.count.return_value = 99
        actor_mgr.using.return_value.count.return_value = 3291
        director_mgr.using.return_value.count.return_value = 108
        metrics_mgr.using.return_value.aggregate.return_value = {
            "avg_rating": Decimal("6.84")
        }

        response = client.get("/")

    assert response.status_code == 200
    assert response.context["movie_count"] == 99
    assert response.context["actor_count"] == 3291
    assert response.context["director_count"] == 108
    assert response.context["avg_rating"] == Decimal("6.84")


# ---------------------------------------------------------------------------
# movie_detail
# ---------------------------------------------------------------------------


def test_movie_detail_returns_200_with_expected_context():
    movie = Movie(
        movie_id=1,
        title="Test Movie",
        release_date=date(2020, 1, 1),
        runtime=120,
        budget=1000,
        revenue=5000,
        original_language="en",
        status="Released",
    )
    genre = Genre(genre_id=1, genre_name="Action")
    actor = Actor(actor_id=1, name="Test Actor")
    director = Director(director_id=1, name="Test Director")
    casting = Casting(movie=movie, actor=actor, director=director, role="Actor", ordering=1)

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Casting, "objects", new=MagicMock()) as casting_mgr:
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = [genre]
        casting_mgr.using.return_value.filter.return_value.select_related.return_value = [
            casting
        ]

        response = client.get(f"/movies/{movie.movie_id}/")

    assert response.status_code == 200
    assert response.context["movie"] == movie
    assert list(response.context["genres"]) == [genre]
    assert list(response.context["cast"]) == [casting]


def test_movie_detail_404_when_missing():
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/movies/999999/")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# actor_detail
# ---------------------------------------------------------------------------


def test_actor_detail_returns_200_with_expected_context():
    actor = Actor(actor_id=1, name="Test Actor")
    movie = Movie(movie_id=1, title="Test Movie", release_date=date(2020, 1, 1))

    filmography = MagicMock()
    filmography.__iter__.return_value = iter([movie])
    filmography.count.return_value = 1
    filmography.aggregate.return_value = {
        "earliest": date(2020, 1, 1),
        "latest": date(2020, 1, 1),
    }

    with patch("movies.views.get_object_or_404", return_value=actor), patch.object(
        Casting, "objects", new=MagicMock()
    ) as casting_mgr, patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr:
        casting_mgr.using.return_value.filter.return_value.values_list.return_value.distinct.return_value = [
            1
        ]
        movie_mgr.using.return_value.filter.return_value.order_by.return_value = filmography
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.aggregate.return_value = {
            "avg_rating": Decimal("7.50")
        }

        response = client.get(f"/actors/{actor.actor_id}/")

    assert response.status_code == 200
    assert response.context["actor"] == actor
    assert list(response.context["filmography"]) == [movie]
    assert response.context["film_count"] == 1
    assert response.context["avg_rating"] == Decimal("7.50")
    assert response.context["career_start"] == date(2020, 1, 1)
    assert response.context["career_end"] == date(2020, 1, 1)


def test_actor_detail_404_when_missing():
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/actors/999999/")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# director_detail
# ---------------------------------------------------------------------------


def test_director_detail_returns_200_with_expected_context():
    director = Director(director_id=1, name="Test Director")
    movie = Movie(movie_id=1, title="Test Movie", release_date=date(2020, 1, 1))

    filmography = MagicMock()
    filmography.__iter__.return_value = iter([movie])
    filmography.count.return_value = 1
    filmography.aggregate.return_value = {
        "earliest": date(2020, 1, 1),
        "latest": date(2020, 1, 1),
    }

    with patch("movies.views.get_object_or_404", return_value=director), patch.object(
        Casting, "objects", new=MagicMock()
    ) as casting_mgr, patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr:
        casting_mgr.using.return_value.filter.return_value.values_list.return_value.distinct.return_value = [
            1
        ]
        movie_mgr.using.return_value.filter.return_value.order_by.return_value = filmography
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.aggregate.return_value = {
            "avg_rating": Decimal("8.10")
        }

        response = client.get(f"/directors/{director.director_id}/")

    assert response.status_code == 200
    assert response.context["director"] == director
    assert list(response.context["filmography"]) == [movie]
    assert response.context["film_count"] == 1
    assert response.context["avg_rating"] == Decimal("8.10")
    assert response.context["career_start"] == date(2020, 1, 1)
    assert response.context["career_end"] == date(2020, 1, 1)


def test_director_detail_404_when_missing():
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/directors/999999/")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# genre_detail
# ---------------------------------------------------------------------------


def test_genre_detail_returns_200_with_expected_context():
    genre = Genre(genre_id=1, genre_name="Action")
    movie = Movie(movie_id=1, title="Test Movie", release_date=date(2020, 1, 1))
    top_row = MagicMock()
    top_row.movie = movie
    top_row.rating = Decimal("9.00")
    revenue_row = {"year": 2020, "total_revenue": Decimal("5000")}

    metrics = MagicMock()
    metrics.order_by.return_value.__getitem__.return_value = [top_row]
    metrics.filter.return_value.annotate.return_value.values.return_value.annotate.return_value.order_by.return_value = [
        revenue_row
    ]
    metrics.values.return_value.distinct.return_value.count.return_value = 1
    metrics.aggregate.return_value = {"avg_rating": Decimal("9.00")}

    with patch("movies.views.get_object_or_404", return_value=genre), patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr:
        metrics_mgr.using.return_value.filter.return_value.select_related.return_value = metrics

        response = client.get(f"/genres/{genre.genre_id}/")

    assert response.status_code == 200
    assert response.context["genre"] == genre
    assert list(response.context["top_movies"]) == [top_row]
    assert list(response.context["revenue_by_year"]) == [revenue_row]
    assert response.context["movie_count"] == 1
    assert response.context["avg_rating"] == Decimal("9.00")


def test_genre_detail_404_when_missing():
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/genres/999999/")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# analytics dashboard
# ---------------------------------------------------------------------------


def test_analytics_dashboard_returns_200_with_expected_context():
    fake_rows = {
        "top_rated_directors.sql": [{"name": "Test Director", "avg_rating": Decimal("9.0")}],
        "most_productive_actors.sql": [{"name": "Test Actor", "film_count": 5}],
        "revenue_by_genre.sql": [
            {"genre_name": "Action", "total_revenue": Decimal("1000")}
        ],
        "movies_by_decade.sql": [{"decade": 2020, "avg_rating": Decimal("7.5")}],
        "director_trend_over_time.sql": [{"year": 2020, "avg_rating": Decimal("7.5")}],
        "actor_collaboration_frequency.sql": [
            {"actor_a": "A", "actor_b": "B", "collaborations": 2}
        ],
        "genre_growth_over_time.sql": [{"year": 2020, "genre_name": "Action", "count": 3}],
    }

    with patch("analytics.views._run_query", side_effect=lambda fname: fake_rows[fname]):
        response = client.get("/analytics/")

    assert response.status_code == 200
    for key in (
        "top_rated_directors",
        "most_productive_actors",
        "revenue_by_genre",
        "movies_by_decade",
        "director_trend_over_time",
        "actor_collaboration_frequency",
        "genre_growth_over_time",
        "decade_labels",
        "decade_avg_ratings",
        "genre_labels",
        "genre_revenue",
    ):
        assert key in response.context

    assert response.context["decade_labels"] == [2020]
    assert response.context["decade_avg_ratings"] == [7.5]
    assert response.context["genre_labels"] == ["Action"]
    assert response.context["genre_revenue"] == [1000.0]
