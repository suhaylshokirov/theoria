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
    Company, Credit, Genre, Movie, MovieCompany, MovieMetrics, Person,
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
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr:
        using = movie_mgr.using.return_value
        using.count.return_value = 99
        # top_rated: .annotate(...).order_by(...)[:12]
        using.annotate.return_value.order_by.return_value.__getitem__.return_value = [movie]
        # newest: .order_by(...)[:12]
        using.order_by.return_value.__getitem__.return_value = [movie]
        # mosaic: .filter(poster_path__isnull=False).order_by(...)[:120]
        using.filter.return_value.order_by.return_value.__getitem__.return_value = [movie]
        person_mgr.using.return_value.count.return_value = 122685
        credit_mgr.using.return_value.count.return_value = 237454
        metrics_mgr.using.return_value.aggregate.return_value = {
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

    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr:
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

    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr:
        qs = movie_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = [movie]

        response = client.get("/movies/", {"q": "test", "sort": "rating"})

    assert response.status_code == 200
    qs.filter.assert_called_once_with(title__icontains="test")
    qs.annotate.assert_called_once()  # rating sort needs the Max annotation
    assert response.context["q"] == "test"
    assert response.context["sort"] == "rating"


def test_movie_list_invalid_sort_falls_back_to_release():
    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr:
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
    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr:
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
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = [genre]
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = [
            cast_credit, crew_credit
        ]
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = {
            "rating": Decimal("8.50"),
        }

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
    assert response.context["metrics"]["rating"] == Decimal("8.50")


def test_movie_detail_renders_studios_as_links():
    """A film's studios (bridge_movie_company, Task 58) render as links to
    their /studios/<slug>/ pages in the movie's record list."""
    movie = _movie()
    studio = Company(company_id=900, name="Warner Bros. Pictures", slug="warner-bros-pictures")
    link = MovieCompany(movie=movie, company=studio)

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = [link]
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    assert list(response.context["studios"]) == [studio]
    body = response.content.decode()
    assert 'href="/studios/warner-bros-pictures/"' in body
    assert "Warner Bros. Pictures" in body


def test_movie_detail_renders_rating_and_synopsis():
    """Rating and overview must reach the rendered page, with no vote count.

    The rating lives in fact_movie_metrics and the synopsis in dim_movie.overview
    — both existed in the pipeline long before any page displayed them. The vote
    count is deliberately not shown to readers.
    """
    movie = _movie()
    movie.overview = "A test synopsis describing the film."

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = {
            "rating": Decimal("8.50"),
        }

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert response.status_code == 200
    assert "8.5" in body
    assert "A test synopsis describing the film." in body
    assert "vote" not in body.lower()


def test_movie_detail_renders_identifiers_and_original_title_when_differs():
    """imdb_id/homepage render as outbound links; original_title only shows
    when it differs from title — printing the same string twice is noise."""
    movie = _movie(title="Seven Samurai")
    movie.original_title = "Shichinin no Samurai"
    movie.imdb_id = "tt0047478"
    movie.homepage = "https://example.com/seven-samurai"

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = None

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert response.status_code == 200
    assert "Shichinin no Samurai" in body
    assert 'href="https://www.imdb.com/title/tt0047478/"' in body
    assert 'href="https://example.com/seven-samurai"' in body
    assert "tt0047478" not in body.replace('href="https://www.imdb.com/title/tt0047478/"', "")
    assert 'rel="noopener noreferrer"' in body


def test_movie_detail_hides_original_title_when_same_as_title():
    movie = _movie(title="Inception")
    movie.original_title = "Inception"
    movie.imdb_id = None
    movie.homepage = None

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = None

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
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = [
            cast_credit
        ]
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = None

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
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = None

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
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = None

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
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = (
            cast_credits + crew_credits
        )
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = None

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
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = None

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
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr, patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr:
        company_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = None

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
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr:
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = credits
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.aggregate.return_value = {
            "avg_rating": Decimal("7.50")
        }
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


def test_studio_detail_returns_200_with_expected_stats():
    studio = _company(name="Warner Bros. Pictures", slug="warner-bros-pictures")
    movie = _movie()

    with patch("movies.views.get_object_or_404", return_value=studio), patch.object(
        MovieCompany, "objects", new=MagicMock()
    ) as company_mgr, patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr:
        company_mgr.using.return_value.filter.return_value.values_list.return_value = [movie.movie_id]

        movies_qs = movie_mgr.using.return_value.filter.return_value.order_by.return_value
        movies_qs.__iter__.return_value = iter([movie])
        movies_qs.aggregate.side_effect = [
            {"film_count": 1, "total_revenue": 5000},
            {"start": date(2020, 1, 1), "end": date(2020, 1, 1)},
        ]

        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.aggregate.return_value = {
            "avg_rating": Decimal("7.24")
        }

        response = client.get("/studios/warner-bros-pictures/")

    assert response.status_code == 200
    assert response.context["company"] == studio
    assert response.context["film_count"] == 1
    assert response.context["total_revenue"] == 5000
    assert response.context["avg_rating"] == Decimal("7.24")
    assert response.context["period"] == "2020"
    body = response.content.decode()
    assert "Warner Bros. Pictures" in body


def test_studio_detail_404_when_missing():
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/studios/nobody/")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# analytics dashboard
# ---------------------------------------------------------------------------


def test_analytics_dashboard_returns_200_with_expected_context():
    fake_rows = {
        "revenue_by_genre.sql": [
            {"genre_name": "Action", "movie_count": 3, "total_revenue": Decimal("1000")}
        ],
        "movies_by_decade.sql": [{"decade": 2020, "avg_rating": Decimal("7.5")}],
        "studio_output_by_decade.sql": [
            {"decade": 2020, "studio_name": "Test Studio", "movie_count": 5}
        ],
        "top_studios_by_revenue.sql": [
            {"studio_slug": "test-studio", "studio_name": "Test Studio",
             "movie_count": 5, "total_revenue": Decimal("500"), "avg_rating": Decimal("7.1")}
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
        "studio_output_by_decade",
        "top_studios_by_revenue",
    ):
        assert key in response.context

    assert response.context["decade_labels"] == [2020]
    assert response.context["decade_avg_ratings"] == [7.5]
    assert response.context["genre_labels"] == ["Action"]
    assert response.context["genre_revenue"] == [1000.0]
    assert response.context["studio_output_by_decade"][0]["studio_name"] == "Test Studio"
    assert response.context["top_studios_by_revenue"][0]["studio_slug"] == "test-studio"

    body = response.content.decode()
    assert "Studio output by decade" in body
    assert "Top studios by revenue" in body
    assert 'href="/studios/test-studio/"' in body


# ---------------------------------------------------------------------------
# tmdb_images template filter
# ---------------------------------------------------------------------------


def test_tmdb_image_filter_builds_url_and_handles_empty():
    from movies.templatetags.tmdb_images import tmdb_image

    assert tmdb_image("/abc.jpg", "w342").endswith("/w342/abc.jpg")
    assert tmdb_image("", "w342") == ""
    assert tmdb_image(None) == ""

