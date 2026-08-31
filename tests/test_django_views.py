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

from movies.models import (  # noqa: E402
    Company, Country, Credit, Genre, Language, Movie, MovieCompany,
    MovieCountry, MovieLanguage, MovieRating, Person,
)

client = Client()


def setup_module(module):
    # Enables response.context capture on the test Client (normally wired up
    # by Django's own test runner / pytest-django, neither of which is in
    # play for these plain-pytest tests).
    setup_test_environment()


def teardown_module(module):
    teardown_test_environment()


def _movie(movie_id=1, title="Test Movie"):
    return Movie(
        movie_id=movie_id,
        title=title,
        release_date=date(2020, 1, 1),
        runtime=120,
        budget=1000,
        revenue=5000,
        original_language="en",
        status="Released",
    )


# ---------------------------------------------------------------------------
# home
# ---------------------------------------------------------------------------


def test_home_returns_200_with_expected_context():
    movie = _movie()

    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Person, "objects", new=MagicMock()
    ) as person_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr:
        using = movie_mgr.using.return_value
        using.count.return_value = 99
        # top_rated and newest both now annotate(imdb_rating=...).order_by(...)[:12]
        # (Task 68), so both hit this same chain regardless of which order_by
        # expression each actually orders by.
        using.annotate.return_value.order_by.return_value.__getitem__.return_value = [movie]
        # mosaic: .filter(poster_path__isnull=False).order_by(...)[:120]
        using.filter.return_value.order_by.return_value.__getitem__.return_value = [movie]
        person_mgr.using.return_value.count.return_value = 122685
        credit_mgr.using.return_value.count.return_value = 237454
        # avg_rating now reads fact_movie_rating filtered to source="imdb"
        # instead of averaging every fact_movie_metrics row (Task 68).
        rating_mgr.using.return_value.filter.return_value.aggregate.return_value = {
            "avg_rating": Decimal("6.84")
        }

        response = client.get("/")

    assert response.status_code == 200
    assert response.context["movie_count"] == 99
    assert response.context["person_count"] == 122685
    assert response.context["credit_count"] == 237454
    assert response.context["avg_rating"] == Decimal("6.84")
    assert list(response.context["top_rated"]) == [movie]
    assert list(response.context["newest"]) == [movie]
    assert list(response.context["mosaic"]) == [movie]


# ---------------------------------------------------------------------------
# movie_list
# ---------------------------------------------------------------------------


def test_movie_list_returns_200_with_pagination():
    movies = [_movie(movie_id=i, title=f"Movie {i}") for i in range(1, 4)]

    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = []
        qs = movie_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = movies

        response = client.get("/movies/")

    assert response.status_code == 200
    assert list(response.context["page_obj"]) == movies
    assert response.context["q"] == ""
    assert response.context["sort"] == "release"


def test_movie_list_search_and_sort():
    movie = _movie()

    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = []
        qs = movie_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = [movie]

        response = client.get("/movies/", {"q": "test", "sort": "rating"})

    assert response.status_code == 200
    qs.filter.assert_called_once_with(title__icontains="test")
    # Annotated unconditionally now (Task 68) — always called once, whether
    # or not this request happens to sort by rating.
    qs.annotate.assert_called_once()
    assert response.context["q"] == "test"
    assert response.context["sort"] == "rating"


def test_movie_list_sort_by_rating_uses_the_imdb_annotation():
    """MOVIE_SORTS["rating"] and the card display both read the same
    Max("movierating__rating", filter=source="imdb") annotation (Task 68) —
    sorting and what each card shows can never disagree."""
    from django.db.models import Max, Q

    movie = _movie()

    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = []
        qs = movie_mgr.using.return_value.all.return_value
        qs.annotate.return_value = qs
        qs.order_by.return_value = [movie]

        response = client.get("/movies/", {"sort": "rating"})

    assert response.status_code == 200
    (_, kwargs), = qs.annotate.call_args_list
    imdb_rating = kwargs["imdb_rating"]
    assert isinstance(imdb_rating, Max)
    assert imdb_rating.source_expressions[0].name == "movierating__rating"
    assert imdb_rating.filter == Q(movierating__source="imdb")
    # order_by() was handed the same F("imdb_rating") this annotation defines.
    (order_expr,), _ = qs.order_by.call_args
    assert order_expr.expression.name == "imdb_rating"


def test_movie_list_invalid_sort_falls_back_to_release():
    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = []
        qs = movie_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = []

        response = client.get("/movies/", {"sort": "bogus"})

    assert response.status_code == 200
    assert response.context["sort"] == "release"


def test_movie_list_ajax_request_renders_results_fragment_only():
    """initLiveFilter()'s fetch() sets this header and wants just the results,
    not the full page — see _is_ajax() in views.py."""
    movie = _movie()
    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = []
        qs = movie_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = [movie]

        response = client.get("/movies/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert response.status_code == 200
    content = response.content.decode()
    assert "movies-grid" in content
    assert "<html" not in content
    assert "<!DOCTYPE" not in content


def test_movie_list_filters_by_genre():
    """?genre= narrows the catalog via fact_movie_metrics (Task 70) — there is
    no bridge table for genre, so the filter joins the fact table directly and
    the URL carries a slugified genre name rather than the raw genre_id."""
    movie = _movie()

    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = [(27, "Horror")]
        qs = movie_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.distinct.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = [movie]

        response = client.get("/movies/", {"genre": "horror"})

    assert response.status_code == 200
    qs.filter.assert_called_once_with(moviemetrics__genre_id=27)
    qs.distinct.assert_called_once()
    assert response.context["genre"] == "horror"
    assert response.context["genre_choices"] == [("horror", "Horror")]


def test_movie_list_unknown_genre_falls_back_to_unfiltered():
    """An unknown slug is silently ignored, the same posture sort/gender/
    known_for already take — not a 404, and not an empty grid."""
    movie = _movie()

    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = [(27, "Horror")]
        qs = movie_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.distinct.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = [movie]

        response = client.get("/movies/", {"genre": "nonsense"})

    assert response.status_code == 200
    for _, kwargs in qs.filter.call_args_list:
        assert "moviemetrics__genre_id" not in kwargs
    qs.distinct.assert_not_called()
    assert response.context["genre"] == ""


def test_movie_list_genre_composes_with_revenue_sort():
    """The genre filter and a non-default sort must both take effect at once —
    the actual feature, not just that neither breaks alone."""
    from django.db.models import Max, Q

    movie = _movie()

    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = [(28, "Action")]
        qs = movie_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.distinct.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = [movie]

        response = client.get("/movies/", {"genre": "action", "sort": "revenue"})

    assert response.status_code == 200
    qs.filter.assert_called_once_with(moviemetrics__genre_id=28)
    qs.distinct.assert_called_once()
    (_, kwargs), = qs.annotate.call_args_list
    assert isinstance(kwargs["imdb_rating"], Max)
    assert kwargs["imdb_rating"].filter == Q(movierating__source="imdb")
    (order_expr,), _ = qs.order_by.call_args
    assert order_expr.expression.name == "revenue"
    assert response.context["sort"] == "revenue"


def test_movie_list_genre_survives_pagination():
    """base_query (fed to the shared _pager.html) must carry genre forward,
    the same way it already carries q/sort — see _pager.html's docstring on
    why this is built in the view rather than the template."""
    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = [(27, "Horror")]
        qs = movie_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.distinct.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = []

        response = client.get("/movies/", {"genre": "horror"})

    base_query = response.context["base_query"]
    assert "genre=horror" in base_query


# ---------------------------------------------------------------------------
# actor_list / director_list
# ---------------------------------------------------------------------------


def _person(person_id=1, name="Test Person", slug="test-person"):
    return Person(person_id=person_id, name=name, slug=slug,
                  popularity=Decimal("9.5"))


def test_person_list_returns_200_with_search():
    person = _person()

    with patch.object(Person, "objects", new=MagicMock()) as person_mgr:
        qs = person_mgr.using.return_value
        qs.filter.return_value = qs
        qs.order_by.return_value = [person]

        response = client.get("/people/", {"q": "test"})

    assert response.status_code == 200
    qs.filter.assert_called_once_with(name__icontains="test")
    assert list(response.context["page_obj"]) == [person]
    assert response.context["scope"] == "all"


def test_actor_list_filters_people_by_acting_credit():
    """"Actors" is now a question about fact_credit, not a separate table."""
    person = _person()

    with patch.object(Person, "objects", new=MagicMock()) as person_mgr:
        using = person_mgr.using.return_value
        scoped = using.filter.return_value.distinct.return_value
        scoped.order_by.return_value = [person]

        response = client.get("/actors/")

    assert response.status_code == 200
    using.filter.assert_called_once_with(credits__department="Acting")
    assert response.context["scope"] == "acting"


def test_director_list_filters_people_by_directing_credit():
    person = _person()

    with patch.object(Person, "objects", new=MagicMock()) as person_mgr:
        using = person_mgr.using.return_value
        scoped = using.filter.return_value.distinct.return_value
        scoped.order_by.return_value = [person]

        response = client.get("/directors/")

    assert response.status_code == 200
    using.filter.assert_called_once_with(credits__department="Directing")
    assert response.context["scope"] == "directing"


def test_person_list_filters_by_gender_and_known_for_department():
    person = _person()

    with patch.object(Person, "objects", new=MagicMock()) as person_mgr:
        qs = person_mgr.using.return_value
        qs.filter.return_value = qs
        qs.order_by.return_value = [person]

        response = client.get("/people/", {"gender": "1", "known_for": "Directing"})

    assert response.status_code == 200
    qs.filter.assert_any_call(gender=1)
    qs.filter.assert_any_call(known_for_department="Directing")
    assert response.context["gender"] == "1"
    assert response.context["known_for"] == "Directing"


def test_person_list_rejects_unknown_gender_and_known_for_values():
    """An unrecognised value falls back to no filter, rather than a bad query."""
    person = _person()

    with patch.object(Person, "objects", new=MagicMock()) as person_mgr:
        qs = person_mgr.using.return_value
        qs.order_by.return_value = [person]

        response = client.get("/people/", {"gender": "9", "known_for": "Not A Craft"})

    assert response.status_code == 200
    qs.filter.assert_not_called()
    assert response.context["gender"] == ""
    assert response.context["known_for"] == ""


def test_person_list_sorts_by_name_when_requested():
    person = _person()

    with patch.object(Person, "objects", new=MagicMock()) as person_mgr:
        qs = person_mgr.using.return_value
        qs.order_by.return_value = [person]

        response = client.get("/people/", {"sort": "name"})

    assert response.status_code == 200
    assert response.context["sort"] == "name"
    qs.order_by.assert_called_once()


def test_person_list_ajax_request_renders_results_fragment_only():
    """initLiveFilter()'s fetch() sets this header and wants just the results,
    not the full page — see _is_ajax() in views.py."""
    person = _person()

    with patch.object(Person, "objects", new=MagicMock()) as person_mgr:
        qs = person_mgr.using.return_value
        qs.order_by.return_value = [person]

        response = client.get(
            "/people/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )

    assert response.status_code == 200
    content = response.content.decode()
    assert "people-toolbar-meta" in content
    assert "people-grid" in content
    # No page shell — this is a fragment, not the full document.
    assert "<html" not in content
    assert "<!DOCTYPE" not in content


# ---------------------------------------------------------------------------
# movie_detail
# ---------------------------------------------------------------------------


def test_movie_detail_returns_200_with_expected_context():
    movie = _movie()
    genre = Genre(genre_id=1, genre_name="Action")
    actor = _person(person_id=1, name="Test Actor", slug="test-actor")
    director = _person(person_id=2, name="Test Director", slug="test-director")
    cast_credit = Credit(movie=movie, person=actor, department="Acting",
                         job="Actor", character_name="Hero", ordering=1)
    crew_credit = Credit(movie=movie, person=director, department="Directing",
                         job="Director")

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = [genre]
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = [
            cast_credit, crew_credit
        ]
        rating_mgr.using.return_value.filter.return_value.first.return_value = MovieRating(
            movie=movie, source="imdb", rating=Decimal("8.50"), vote_count=12345,
        )

        response = client.get(f"/movies/{movie.movie_id}/")

    assert response.status_code == 200
    assert response.context["movie"] == movie
    assert list(response.context["genres"]) == [genre]
    assert list(response.context["cast"]) == [cast_credit]
    assert list(response.context["directors"]) == [director]
    # Non-acting credits are merged per person and grouped by department. The
    # whole crew is always sent; the browser pages it (Task 54).
    assert [d["name"] for d in response.context["crew"]] == ["Directing"]
    assert response.context["crew"][0]["people"][0]["person"] == director
    assert response.context["movie_rating"].rating == Decimal("8.50")


def test_movie_detail_renders_studios_as_links():
    """A film's studios (bridge_movie_company, Task 58) render as links to
    their /studios/<slug>/ pages in the movie's record list."""
    movie = _movie()
    studio = Company(company_id=900, name="Warner Bros. Pictures", slug="warner-bros-pictures")
    link = MovieCompany(movie=movie, company=studio)

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = [link]
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    assert list(response.context["studios"]) == [studio]
    body = response.content.decode()
    assert 'href="/studios/warner-bros-pictures/"' in body
    assert "Warner Bros. Pictures" in body


def test_movie_detail_renders_genres_as_links_to_the_filtered_index():
    """A film's genre chips link back to /movies/?genre=<slug> (Task 71).

    They were plain text from 2026-08-14, when the /genres/ index was removed
    and left them pointing nowhere. The slug is the slugified genre *name*,
    matching the {slug: genre_id} map movie_list() builds — dim_genre has no
    slug column, so both sides derive it the same way or they drift. No
    ?sort= is carried: /movies/ already defaults to newest-first.
    """
    movie = _movie()
    genre = Genre(genre_id=878, genre_name="Science Fiction")

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = [genre]
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert 'href="/movies/?genre=science-fiction"' in body
    assert "Science Fiction" in body
    # The chip is a link now, not the <span> it was left as.
    assert '<span class="chip">' not in body


def test_movie_detail_renders_rating_badge_with_vote_count_and_synopsis():
    """The IMDb badge (Task 68) and the overview must both reach the page.

    The rating now lives in fact_movie_rating and the synopsis in
    dim_movie.overview — the synopsis existed in the pipeline long before any
    page displayed it. Unlike the old fact_movie_metrics-backed row, the
    badge does show a vote count, and links out to IMDb when imdb_id is set.
    """
    movie = _movie()
    movie.overview = "A test synopsis describing the film."
    movie.imdb_id = "tt1234567"

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = MovieRating(
            movie=movie, source="imdb", rating=Decimal("8.50"), vote_count=2103445,
        )

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert response.status_code == 200
    assert "8.5" in body
    assert "A test synopsis describing the film." in body
    assert "2,103,445 votes" in body
    assert 'href="https://www.imdb.com/title/tt1234567/"' in body
    assert "IMDb rating 8.5 out of 10" in body
    assert "8.5 / 10" in body  # the visible figure carries the "/ 10" scale


def test_movie_detail_renders_no_badge_when_no_imdb_rating():
    """No fact_movie_rating row for this film → no badge, no em dash, no
    empty row — the same restraint Task 56 applied to original_title. Real:
    only 1,139 of the partition's 1,215 films have an IMDb rating."""
    movie = _movie()

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert response.status_code == 200
    assert "rating-badge" not in body
    assert "rating-badge-mark" not in body
    assert response.context["movie_rating"] is None


def test_movie_detail_renders_original_title_when_differs():
    """original_title only shows when it differs from title — printing the
    same string twice is noise."""
    movie = _movie(title="Seven Samurai")
    movie.original_title = "Shichinin no Samurai"
    movie.imdb_id = "tt0047478"
    movie.homepage = "https://example.com/seven-samurai"

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert response.status_code == 200
    assert "Shichinin no Samurai" in body
    assert "Elsewhere" not in body


def test_movie_detail_hides_original_title_when_same_as_title():
    movie = _movie(title="Inception")
    movie.original_title = "Inception"
    movie.imdb_id = None
    movie.homepage = None

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert response.status_code == 200
    assert "originally" not in body.lower()
    assert "Elsewhere" not in body


def test_movie_detail_cast_present_when_no_director_credited():
    """Regression test for the fact_casting cross-join bug (Task 35): a movie
    with zero fact_crew rows must still render its cast, since fact_cast has
    no dependency on fact_crew at all."""
    movie = _movie()
    actor = _person(person_id=1, name="Test Actor", slug="test-actor")
    cast_credit = Credit(movie=movie, person=actor, department="Acting",
                         job="Actor", character_name="Hero", ordering=0)

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = [
            cast_credit
        ]
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    assert response.status_code == 200
    assert list(response.context["cast"]) == [cast_credit]
    assert list(response.context["directors"]) == []


def test_movie_detail_merges_multi_job_crew_person():
    """A person holding several jobs on one film collapses to one row, filed
    under their most senior department, instead of one card per job spread
    across as many department sections (Task 54: 8,361 of 227,623 (movie,
    person) pairs hold more than one job)."""
    movie = _movie()
    director = _person(person_id=2, name="Test Director", slug="test-director")
    credits = [
        Credit(movie=movie, person=director, department="Directing", job="Director"),
        Credit(movie=movie, person=director, department="Writing", job="Screenplay"),
        Credit(movie=movie, person=director, department="Production", job="Producer"),
    ]

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    groups = response.context["crew"]
    assert [g["name"] for g in groups] == ["Directing"]
    merged = groups[0]["people"]
    assert len(merged) == 1
    # Ordered by department seniority, not alphabetically — alphabetical
    # would read "Director / Producer / Screenplay" and bury the job that
    # matters.
    assert merged[0]["job_display"] == "Director / Screenplay / Producer"
    # The record's separate "Directed by" line still names them once too —
    # this asserts the *crew section* itself renders the merged person only
    # once, not that the whole page mentions their name only once.
    assert response.content.decode().count("Director / Screenplay / Producer") == 1


def test_movie_detail_person_appears_in_cast_and_crew():
    """A person can legitimately hold both an Acting credit and a crew credit
    on the same film — merging crew must not pull them out of the cast."""
    movie = _movie()
    person = _person(person_id=3, name="Double Credit", slug="double-credit")
    credits = [
        Credit(movie=movie, person=person, department="Acting", job="Actor",
               character_name="Extra", ordering=5),
        Credit(movie=movie, person=person, department="Directing", job="Director"),
    ]

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    assert [c.person for c in response.context["cast"]] == [person]
    assert [
        m["person"] for g in response.context["crew"] for m in g["people"]
    ] == [person]


def test_movie_detail_sends_every_credit_for_client_side_paging():
    """Cast and crew are paged in the browser, so the view must send all of
    them — the regression this guards against is a server-side limit creeping
    back in and silently truncating a film's credits.
    """
    movie = _movie()
    cast_credits = [
        Credit(
            movie=movie,
            person=_person(person_id=i, name=f"Actor {i}", slug=f"actor-{i}"),
            department="Acting", job="Actor", ordering=i,
        )
        for i in range(1, 31)
    ]
    crew_credits = [
        Credit(
            movie=movie,
            person=_person(person_id=100 + i, name=f"Crew {i:02d}",
                           slug=f"crew-{i}"),
            department="Editing", job="Assistant Editor",
        )
        for i in range(25)
    ]

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = (
            cast_credits + crew_credits
        )
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    assert len(response.context["cast"]) == 30
    assert response.context["cast_count"] == 30
    assert sum(g["count"] for g in response.context["crew"]) == 25
    assert response.context["crew_person_count"] == 25

    body = response.content.decode()
    # Every person is actually in the markup, not just the context.
    assert body.count('class="poster-card"') == 30
    assert body.count('class="credit-row"') == 25
    # And both sections are wired for the browser pager.
    assert body.count("data-paged") == 2
    assert 'data-page-items=".poster-card"' in body
    assert 'data-page-items=".credit-row"' in body


def test_movie_detail_crew_grouped_in_department_order():
    """Crew groups are ordered by DEPARTMENT_ORDER, not alphabetically, so the
    page reads down a call sheet: Directing before Camera before Sound.
    Alphabetical would open on Art.
    """
    movie = _movie()
    credits = [
        Credit(movie=movie,
               person=_person(person_id=1, name="A Composer", slug="a-composer"),
               department="Sound", job="Original Music Composer"),
        Credit(movie=movie,
               person=_person(person_id=2, name="A Designer", slug="a-designer"),
               department="Art", job="Production Design"),
        Credit(movie=movie,
               person=_person(person_id=3, name="A Director", slug="a-director"),
               department="Directing", job="Director"),
        Credit(movie=movie,
               person=_person(person_id=4, name="A Shooter", slug="a-shooter"),
               department="Camera", job="Director of Photography"),
    ]

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    assert [g["name"] for g in response.context["crew"]] == [
        "Directing", "Camera", "Sound", "Art",
    ]
    # Each department is a group the pager can hide when its people are all on
    # another page.
    assert response.content.decode().count("data-page-group") == 4


def test_movie_detail_crew_rows_carry_a_face_or_silhouette():
    """Crew rows lead with a headshot where one exists and the same person
    silhouette the cast cards use where it doesn't — one placeholder
    vocabulary across the page. Only 23.8% of credited crew have a photo, so
    the fallback is the common case.
    """
    movie = _movie()
    with_photo = _person(person_id=1, name="Wally Pfister", slug="wally-pfister")
    with_photo.profile_path = "/abc123.jpg"
    without = _person(person_id=2, name="Michelle Gonsiorek",
                      slug="michelle-gonsiorek")
    credits = [
        Credit(movie=movie, person=with_photo, department="Camera",
               job="Director of Photography"),
        Credit(movie=movie, person=without, department="Directing",
               job="Second Assistant Director"),
    ]

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    # One real headshot, one silhouette — and the silhouette is the shared
    # .placeholder-person component, not a second invention.
    assert body.count('class="credit-avatar" src=') == 1
    assert "/abc123.jpg" in body
    assert body.count("credit-avatar placeholder-person") == 1
    assert body.count('class="person-icon"') == 1


def test_movie_detail_404_when_missing():
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/movies/999999/")

    assert response.status_code == 404


def test_movie_detail_shows_one_countries_row_when_origin_and_production_agree():
    """~77% of films: origin and production name the same country, so this
    renders as one plain "Countries" row rather than two identical lists
    (Task 62, same judgment as original_title in Task 56)."""
    movie = _movie()
    usa = Country(country_code="US", name="United States of America")
    rows = [
        MovieCountry(movie=movie, country=usa, relation="origin"),
        MovieCountry(movie=movie, country=usa, relation="production"),
    ]

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = rows
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert '<dt class="label">Countries</dt>' in body
    assert "United States of America" in body
    assert "Country of origin" not in body
    assert "Production countries" not in body


def test_movie_detail_splits_origin_and_production_when_they_disagree():
    """The ~23% case: origin and production name different countries, so
    both render as their own labeled row rather than picking one silently."""
    movie = _movie()
    japan = Country(country_code="JP", name="Japan")
    usa = Country(country_code="US", name="United States of America")
    rows = [
        MovieCountry(movie=movie, country=japan, relation="origin"),
        MovieCountry(movie=movie, country=usa, relation="production"),
    ]

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = rows
        language_mgr.using.return_value.filter.return_value.select_related.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert '<dt class="label">Country of origin</dt>' in body
    assert '<dt class="label">Production countries</dt>' in body
    assert '<dt class="label">Countries</dt>' not in body
    assert "Japan" in body
    assert "United States of America" in body


def test_movie_detail_reconciles_original_language_with_spoken_languages():
    """original_language and bridge_movie_language are one merged, ordered
    fact (Task 62): the original leads, followed by any other spoken
    language, each named once — never two disconnected language facts."""
    movie = _movie()
    movie.original_language = "en"
    english = Language(language_code="en", name="English", english_name="English")
    italian = Language(language_code="it", name="Italiano", english_name="Italian")
    latin = Language(language_code="la", name="Latin", english_name="Latin")
    rows = [
        MovieLanguage(movie=movie, language=latin),
        MovieLanguage(movie=movie, language=english),
        MovieLanguage(movie=movie, language=italian),
    ]

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = rows
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    assert response.context["languages"] == ["English", "Italiano", "Latin"]
    body = response.content.decode()
    assert '<dt class="label">Languages</dt>' in body
    assert "English, Italiano, Latin" in body


def test_movie_detail_singular_language_label_for_one_language():
    """A film with exactly one spoken language keeps the singular "Language"
    label rather than always saying "Languages"."""
    movie = _movie()
    movie.original_language = "en"
    english = Language(language_code="en", name="English", english_name="English")
    rows = [MovieLanguage(movie=movie, language=english)]

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(
        MovieCountry, "objects", new=MagicMock()
    ) as country_mgr, patch.object(
        MovieLanguage, "objects", new=MagicMock()
    ) as language_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        country_mgr.using.return_value.filter.return_value.select_related.return_value = []
        language_mgr.using.return_value.filter.return_value.select_related.return_value = rows
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        rating_mgr.using.return_value.filter.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert '<dt class="label">Language</dt>' in body
    assert '<dt class="label">Languages</dt>' not in body


# ---------------------------------------------------------------------------
# _career_period
# ---------------------------------------------------------------------------


def test_career_period_no_start_is_em_dash():
    from movies.views import _career_period

    assert _career_period(None, None) == "—"


def test_career_period_single_past_year_shown_once():
    from movies.views import _career_period

    # A one-film career shouldn't render as "2015–2015" — that reads as a
    # typo, not a fact.
    assert _career_period(date(2015, 6, 1), date(2015, 6, 1)) == "2015"


def test_career_period_single_current_year_reads_active():
    from movies.views import _career_period

    today = date.today()
    # A single film released this year isn't a closed range ending "now" —
    # it's a career that just started.
    assert _career_period(today, today) == "Active"


def test_career_period_multi_year_past_is_a_closed_range():
    from movies.views import _career_period

    assert _career_period(date(1997, 1, 1), date(2015, 1, 1)) == "1997–2015"


def test_career_period_ongoing_career_reads_start_dash_active():
    from movies.views import _career_period

    today = date.today()
    assert _career_period(date(1997, 1, 1), today) == "1997–Active"


# ---------------------------------------------------------------------------
# person_detail, and the legacy actor/director redirects
# ---------------------------------------------------------------------------


def test_actor_detail_redirects_permanently_to_person_page():
    """Legacy /actors/<slug>/ 301s to /people/<slug>/ when the slug still resolves."""
    person = _person(person_id=7, name="Test Actor", slug="test-actor")

    with patch("movies.views.get_object_or_404", return_value=person):
        response = client.get("/actors/test-actor/")

    assert response.status_code == 301
    assert response["Location"] == "/people/test-actor/"


def test_director_detail_redirects_permanently_to_person_page():
    person = _person(person_id=7, name="Test Director", slug="test-director")

    with patch("movies.views.get_object_or_404", return_value=person):
        response = client.get("/directors/test-director/")

    assert response.status_code == 301
    assert response["Location"] == "/people/test-director/"


def test_legacy_person_url_404s_when_its_slug_was_reassigned():
    """The 376 slugs that moved when the namespaces merged are unrecoverable.

    dim_actor/dim_director are gone (Task 53), and with them the id mapping
    that Task 51 used — so a legacy slug now resolves only if it still names
    the same person.
    """
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/actors/tom-holland/")

    assert response.status_code == 404


def test_person_detail_merges_multi_job_credits_into_one_filmography_row():
    """A person who acted in and also directed one film gets one poster, not two."""
    person = _person()
    movie = _movie()
    credits = [
        Credit(movie=movie, person=person, department="Editing", job="Editor"),
        Credit(movie=movie, person=person, department="Acting", job="Actor",
               character_name="Hero", ordering=0),
        Credit(movie=movie, person=person, department="Directing", job="Director"),
    ]

    with patch("movies.views.get_object_or_404", return_value=person), patch.object(
        Credit, "objects", new=MagicMock()
    ) as credit_mgr, patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr:
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        # No .values(...).distinct() guard any more (Task 68) — fact_movie_rating
        # is one row per (movie, source), so a plain filter+aggregate is correct.
        rating_mgr.using.return_value.filter.return_value.aggregate.return_value = {
            "avg_rating": Decimal("7.50")
        }
        # Same film set powers each poster's own badge — one more query,
        # still constant regardless of filmography size (Task 68).
        rating_mgr.using.return_value.filter.return_value.values_list.return_value = [
            (movie.movie_id, Decimal("7.50")),
        ]
        movie_mgr.using.return_value.filter.return_value.aggregate.return_value = {
            "earliest": date(2020, 1, 1), "latest": date(2020, 1, 1),
        }

        response = client.get("/people/test-person/")

    assert response.status_code == 200
    filmography = response.context["filmography"]
    # One film, one row — not three, despite three underlying credits.
    assert len(filmography) == 1
    # Department order (Acting, Directing, Editing), and Acting shows the
    # character name rather than the literal job title "Actor".
    assert filmography[0]["job_display"] == "Hero / Director / Editor"
    # Three credits, one film — a person holding several jobs on one title.
    assert response.context["credit_count"] == 3
    assert response.context["film_count"] == 1
    assert response.context["career_period"] == "2020"
    assert response.context["avg_rating"] == Decimal("7.50")
    # The poster grid displays exactly the figure the average was computed
    # from — the annotation attached onto the same Movie instance.
    assert filmography[0]["movie"].imdb_rating == Decimal("7.50")


def test_person_detail_filmography_ratings_use_constant_number_of_queries():
    """The per-poster IMDb figure (Task 68) must come from one extra query
    for the whole grid, not one query per film — a filmography of several
    films must issue the same number of MovieRating queries as one of a
    single film, guarding against an N+1 that would only show up at scale."""
    person = _person()
    movies = [_movie(movie_id=i, title=f"Movie {i}") for i in range(1, 4)]
    credits = [
        Credit(movie=m, person=person, department="Acting", job="Actor",
               character_name="Role", ordering=0)
        for m in movies
    ]

    with patch("movies.views.get_object_or_404", return_value=person), patch.object(
        Credit, "objects", new=MagicMock()
    ) as credit_mgr, patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr:
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        rating_mgr.using.return_value.filter.return_value.aggregate.return_value = {
            "avg_rating": Decimal("7.00")
        }
        rating_mgr.using.return_value.filter.return_value.values_list.return_value = [
            (m.movie_id, Decimal("7.00")) for m in movies
        ]
        movie_mgr.using.return_value.filter.return_value.aggregate.return_value = {
            "earliest": date(2020, 1, 1), "latest": date(2020, 1, 1),
        }

        response = client.get("/people/test-person/")

    assert response.status_code == 200
    assert len(response.context["filmography"]) == 3
    # Exactly two MovieRating queries — the avg_rating aggregate and the
    # per-card ratings dict — however many films are in the filmography.
    assert rating_mgr.using.return_value.filter.call_count == 2
    assert all(
        row["movie"].imdb_rating == Decimal("7.00")
        for row in response.context["filmography"]
    )


def test_person_detail_404_when_missing():
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/people/nobody/")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# studio_list, studio_detail
# ---------------------------------------------------------------------------


def _company(company_id=1, name="Test Studio", slug="test-studio"):
    return Company(company_id=company_id, name=name, slug=slug)


def test_studio_list_returns_200_ranked_by_film_count():
    studio = _company()

    with patch.object(Company, "objects", new=MagicMock()) as company_mgr:
        qs = company_mgr.using.return_value
        qs.annotate.return_value = qs
        qs.filter.return_value = qs
        qs.order_by.return_value = [studio]

        response = client.get("/studios/")

    assert response.status_code == 200
    assert list(response.context["page_obj"]) == [studio]
    qs.filter.assert_called_once_with(film_count__gt=0)
    assert response.context["q"] == ""
    assert response.context["sort"] == "film_count"


def test_studio_list_search_and_sort():
    studio = _company()

    with patch.object(Company, "objects", new=MagicMock()) as company_mgr:
        qs = company_mgr.using.return_value
        qs.annotate.return_value = qs
        qs.filter.return_value = qs
        qs.order_by.return_value = [studio]

        response = client.get("/studios/", {"q": "warner", "sort": "name"})

    assert response.status_code == 200
    # First filter() is the HAVING film_count__gt=0, second is the name search.
    qs.filter.assert_any_call(film_count__gt=0)
    qs.filter.assert_any_call(name__icontains="warner")
    assert response.context["q"] == "warner"
    assert response.context["sort"] == "name"


def test_studio_list_sort_by_revenue():
    """?sort=revenue orders by the Sum(...__movie__revenue) annotation."""
    from movies.views import STUDIO_SORTS

    studio = _company()
    with patch.object(Company, "objects", new=MagicMock()) as company_mgr:
        qs = company_mgr.using.return_value
        qs.annotate.return_value = qs
        qs.filter.return_value = qs
        qs.order_by.return_value = [studio]

        response = client.get("/studios/", {"sort": "revenue"})

    assert response.status_code == 200
    assert response.context["sort"] == "revenue"
    qs.order_by.assert_called_once_with(STUDIO_SORTS["revenue"], "name")
    # The annotation the sort reads must be present.
    _, kwargs = qs.annotate.call_args
    assert "total_revenue" in kwargs


def test_studio_list_invalid_sort_falls_back_to_film_count():
    with patch.object(Company, "objects", new=MagicMock()) as company_mgr:
        qs = company_mgr.using.return_value
        qs.annotate.return_value = qs
        qs.filter.return_value = qs
        qs.order_by.return_value = []

        response = client.get("/studios/", {"sort": "bogus"})

    assert response.status_code == 200
    assert response.context["sort"] == "film_count"


def test_studio_list_ajax_request_renders_results_fragment_only():
    """initLiveFilter()'s fetch() sets this header and wants just the results,
    not the full page — see _is_ajax() in views.py."""
    studio = _company()

    with patch.object(Company, "objects", new=MagicMock()) as company_mgr:
        qs = company_mgr.using.return_value
        qs.annotate.return_value = qs
        qs.filter.return_value = qs
        qs.order_by.return_value = [studio]

        response = client.get("/studios/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert response.status_code == 200
    content = response.content.decode()
    assert "studios-grid" in content
    assert "<html" not in content
    assert "<!DOCTYPE" not in content


def test_studio_detail_returns_200_with_expected_stats():
    studio = _company(name="Warner Bros. Pictures", slug="warner-bros-pictures")
    movie = _movie()

    with patch("movies.views.get_object_or_404", return_value=studio), patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr:
        company_mgr.using.return_value.filter.return_value.values_list.return_value = [movie.movie_id]

        all_movies_qs = movie_mgr.using.return_value.filter.return_value
        all_movies_qs.aggregate.side_effect = [
            {"film_count": 1, "total_revenue": 5000},
            {"start": date(2020, 1, 1), "end": date(2020, 1, 1)},
        ]
        # The grid is always annotated with imdb_rating now (Task 68), not
        # only when sorting by it — pass-through so the existing order_by
        # config below still applies to the annotated queryset.
        all_movies_qs.annotate.return_value = all_movies_qs
        all_movies_qs.order_by.return_value = [movie]

        # No .values(...).distinct() guard any more (Task 68) — fact_movie_rating
        # is one row per (movie, source).
        rating_mgr.using.return_value.filter.return_value.aggregate.return_value = {
            "avg_rating": Decimal("7.24")
        }

        response = client.get("/studios/warner-bros-pictures/")

    assert response.status_code == 200
    assert response.context["company"] == studio
    assert response.context["film_count"] == 1
    assert response.context["total_revenue"] == 5000
    assert response.context["avg_rating"] == Decimal("7.24")
    assert response.context["period"] == "2020"
    assert response.context["q"] == ""
    assert response.context["sort"] == "release"
    assert list(response.context["page_obj"]) == [movie]
    body = response.content.decode()
    assert "Warner Bros. Pictures" in body


def test_studio_detail_filters_filmography_by_search(monkeypatch):
    """The header stats always reflect the whole filmography; the grid below
    is the part that narrows when a search is applied."""
    studio = _company(name="Warner Bros. Pictures", slug="warner-bros-pictures")
    movie = _movie()

    with patch("movies.views.get_object_or_404", return_value=studio), patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr:
        company_mgr.using.return_value.filter.return_value.values_list.return_value = [movie.movie_id]

        all_movies_qs = movie_mgr.using.return_value.filter.return_value
        all_movies_qs.aggregate.side_effect = [
            {"film_count": 1, "total_revenue": 5000},
            {"start": date(2020, 1, 1), "end": date(2020, 1, 1)},
        ]
        # Pass-through so the unconditional imdb_rating annotate() (Task 68)
        # still leads to the same filter/order_by mocks configured below.
        all_movies_qs.annotate.return_value = all_movies_qs
        filtered_qs = all_movies_qs.filter.return_value
        filtered_qs.order_by.return_value = [movie]

        rating_mgr.using.return_value.filter.return_value.aggregate.return_value = {
            "avg_rating": Decimal("7.24")
        }

        response = client.get("/studios/warner-bros-pictures/", {"q": "test", "sort": "title"})

    assert response.status_code == 200
    all_movies_qs.filter.assert_called_once_with(title__icontains="test")
    # Stats are computed off the unfiltered aggregate calls above, unaffected
    # by the search term.
    assert response.context["film_count"] == 1
    assert response.context["q"] == "test"
    assert response.context["sort"] == "title"


def test_studio_detail_ajax_request_renders_results_fragment_only():
    studio = _company(name="Warner Bros. Pictures", slug="warner-bros-pictures")
    movie = _movie()

    with patch("movies.views.get_object_or_404", return_value=studio), patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr:
        company_mgr.using.return_value.filter.return_value.values_list.return_value = [movie.movie_id]

        all_movies_qs = movie_mgr.using.return_value.filter.return_value
        all_movies_qs.aggregate.side_effect = [
            {"film_count": 1, "total_revenue": 5000},
            {"start": date(2020, 1, 1), "end": date(2020, 1, 1)},
        ]
        all_movies_qs.annotate.return_value = all_movies_qs
        all_movies_qs.order_by.return_value = [movie]

        rating_mgr.using.return_value.filter.return_value.aggregate.return_value = {
            "avg_rating": Decimal("7.24")
        }

        response = client.get(
            "/studios/warner-bros-pictures/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )

    assert response.status_code == 200
    content = response.content.decode()
    assert "studio-movies-grid" in content
    assert "<html" not in content
    assert "<!DOCTYPE" not in content
    # The stats block isn't part of the AJAX fragment — only the grid+pager is.
    assert "Warner Bros. Pictures" not in content


def test_studio_detail_404_when_missing():
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/studios/nobody/")

    assert response.status_code == 404


def _render_studio_detail(studio, *, parent=None):
    """Drive studio_detail with the standard filmography mocks, returning the
    response. `parent` (a Company or None) is what Company.objects...first()
    yields when the view resolves company.parent_company_id."""
    movie = _movie()
    with patch("movies.views.get_object_or_404", return_value=studio), patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        MovieRating, "objects", new=MagicMock()
    ) as rating_mgr, patch.object(Company, "objects", new=MagicMock()) as parent_mgr:
        company_mgr.using.return_value.filter.return_value.values_list.return_value = [movie.movie_id]
        all_movies_qs = movie_mgr.using.return_value.filter.return_value
        all_movies_qs.aggregate.side_effect = [
            {"film_count": 1, "total_revenue": 5000},
            {"start": date(2020, 1, 1), "end": date(2020, 1, 1)},
        ]
        all_movies_qs.annotate.return_value = all_movies_qs
        all_movies_qs.order_by.return_value = [movie]
        rating_mgr.using.return_value.filter.return_value.aggregate.return_value = {
            "avg_rating": Decimal("7.24")
        }
        parent_mgr.using.return_value.filter.return_value.first.return_value = parent
        return client.get(f"/studios/{studio.slug}/")


def test_studio_detail_renders_provenance_block():
    """Task 65: headquarters as plain text, homepage as an outbound .ext-link,
    description as prose — all above the filmography."""
    studio = _company(name="Warner Bros. Pictures", slug="warner-bros-pictures")
    studio.description = "An American film studio."
    studio.headquarters = "Burbank, California"
    studio.homepage = "https://www.warnerbros.com"

    response = _render_studio_detail(studio)

    assert response.status_code == 200
    body = response.content.decode()
    assert "An American film studio." in body
    assert "Burbank, California" in body
    assert 'class="ext-link"' in body
    assert 'href="https://www.warnerbros.com"' in body
    assert 'rel="noopener noreferrer"' in body


def test_studio_detail_links_parent_when_it_resolves_to_a_studio():
    studio = _company(name="Pixar", slug="pixar")
    studio.parent_company_id = 2
    studio.parent_company_name = "Walt Disney Pictures"
    parent = _company(company_id=2, name="Walt Disney Pictures", slug="walt-disney-pictures")

    response = _render_studio_detail(studio, parent=parent)

    body = response.content.decode()
    assert response.context["parent_company"] == parent
    assert 'href="/studios/walt-disney-pictures/"' in body


def test_studio_detail_parent_is_plain_text_when_unresolvable():
    """A holding-company parent (Warner Bros. Entertainment) often has no
    dim_company row — the name renders as text, not a dead link."""
    studio = _company(name="Warner Bros. Pictures", slug="warner-bros-pictures")
    studio.parent_company_id = 17
    studio.parent_company_name = "Warner Bros. Entertainment"

    response = _render_studio_detail(studio, parent=None)

    body = response.content.decode()
    assert response.context["parent_company"] is None
    assert "Warner Bros. Entertainment" in body
    assert 'href="/studios/warner-bros' not in body.split("Warner Bros. Entertainment")[0][-120:]


def test_studio_detail_no_provenance_block_when_all_fields_empty():
    studio = _company(name="Tiny Films", slug="tiny-films")

    response = _render_studio_detail(studio)

    body = response.content.decode()
    assert "record-list" not in body
    assert "specimen-synopsis" not in body


def test_studio_detail_renders_logo_when_present():
    """The header opens with the studio's logo as its identity plate."""
    studio = _company(name="Warner Bros. Pictures", slug="warner-bros-pictures")
    studio.logo_path = "/wb.png"

    response = _render_studio_detail(studio)

    body = response.content.decode()
    assert 'class="studio-head"' in body
    assert 'class="logo"' in body
    assert "/w300/wb.png" in body
    assert 'alt="Warner Bros. Pictures logo"' in body
    assert "placeholder-studio" not in body


def test_studio_detail_shows_initial_monogram_when_no_logo():
    """~46% of studios have no TMDB logo — the plate falls back to an
    initial-letter monogram, not a broken image."""
    studio = _company(name="Tiny Films", slug="tiny-films")

    response = _render_studio_detail(studio)

    body = response.content.decode()
    assert 'class="studio-head"' in body
    assert "placeholder-studio" in body
    assert ">T</span>" in body
    assert 'class="logo"' not in body


# ---------------------------------------------------------------------------
# analytics dashboard
# ---------------------------------------------------------------------------


def test_analytics_dashboard_returns_200_with_expected_context():
    fake_rows = {
        "revenue_by_genre.sql": [
            {"genre_name": "Action", "movie_count": 3, "total_revenue": Decimal("1000")}
        ],
        "movies_by_decade.sql": [{"decade": 2020, "avg_rating": Decimal("7.5")}],
        "top_studios_by_revenue.sql": [
            {"studio_slug": "test-studio", "studio_name": "Test Studio",
             "movie_count": 5, "total_revenue": Decimal("500"), "avg_rating": Decimal("7.1")}
        ],
        "films_by_production_country.sql": [
            {"country_name": "Japan", "film_count": 7, "avg_rating": Decimal("7.4")}
        ],
    }

    with patch("analytics.views._run_query", side_effect=lambda fname: fake_rows[fname]):
        response = client.get("/analytics/")

    assert response.status_code == 200
    for key in (
        "revenue_by_genre",
        "movies_by_decade",
        "decade_labels",
        "decade_avg_ratings",
        "genre_labels",
        "genre_revenue",
        "top_studios_by_revenue",
        "films_by_production_country",
    ):
        assert key in response.context

    assert response.context["decade_labels"] == [2020]
    assert response.context["decade_avg_ratings"] == [7.5]
    assert response.context["genre_labels"] == ["Action"]
    assert response.context["genre_revenue"] == [1000.0]
    assert response.context["top_studios_by_revenue"][0]["studio_slug"] == "test-studio"
    assert response.context["films_by_production_country"][0]["country_name"] == "Japan"

    body = response.content.decode()
    assert "Top studios by revenue" in body
    assert 'href="/studios/test-studio/"' in body
    assert "Films by production country" in body
    assert "Studio output by decade" not in body
    assert "Non-English cinema over time" not in body


# ---------------------------------------------------------------------------
# tmdb_images template filter
# ---------------------------------------------------------------------------


def test_tmdb_image_filter_builds_url_and_handles_empty():
    from movies.templatetags.tmdb_images import tmdb_image

    assert tmdb_image("/abc.jpg", "w342").endswith("/w342/abc.jpg")
    assert tmdb_image("", "w342") == ""
    assert tmdb_image(None) == ""


# ---------------------------------------------------------------------------
# 404 page
# ---------------------------------------------------------------------------


def test_custom_404_page_renders_message_and_home_link():
    """An unmatched URL serves the custom 404.html (Django picks it up from
    templates/404.html once DEBUG is off) with a plain message and a single
    link back to the homepage."""
    from django.test import override_settings

    with override_settings(DEBUG=False):
        response = client.get("/no/such/page/here/")

    assert response.status_code == 404
    body = response.content.decode()
    assert "This page doesn’t exist." in body
    assert 'href="/"' in body
    assert "Back to homepage" in body

