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
    Collection, Credit, Genre, Movie, MovieMetrics, Person,
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


# ---------------------------------------------------------------------------
# genre_list
# ---------------------------------------------------------------------------


def test_genre_list_returns_200():
    genre = Genre(genre_id=1, genre_name="Action")

    with patch.object(Genre, "objects", new=MagicMock()) as genre_mgr:
        # The view annotates a per-genre film count before ordering, so the
        # mock has to mirror .using().annotate().order_by().
        genre_mgr.using.return_value.annotate.return_value.order_by.return_value = [
            genre
        ]

        response = client.get("/genres/")

    assert response.status_code == 200
    assert list(response.context["genres"]) == [genre]
    # A Genre built by hand has no movie_count annotation; the view's getattr
    # guard means max_count still resolves rather than raising.
    assert response.context["max_count"] == 0


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
    ) as metrics_mgr:
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = [genre]
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = [
            cast_credit, crew_credit
        ]
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = {
            "rating": Decimal("8.50"),
            "vote_count": 1200,
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


def test_movie_detail_renders_rating_and_synopsis():
    """Rating, vote count and overview must reach the rendered page.

    The rating lives in fact_movie_metrics and the synopsis in dim_movie.overview
    — both existed in the pipeline long before any page displayed them.
    """
    movie = _movie()
    movie.overview = "A test synopsis describing the film."

    with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch.object(Credit, "objects", new=MagicMock()) as credit_mgr, patch.object(
        MovieMetrics, "objects", new=MagicMock()
    ) as metrics_mgr:
        genre_mgr.using.return_value.filter.return_value.distinct.return_value = []
        credit_mgr.using.return_value.filter.return_value.select_related.return_value.order_by.return_value = []
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value.first.return_value = {
            "rating": Decimal("8.50"),
            "vote_count": 1200,
        }

        response = client.get(f"/movies/{movie.movie_id}/")

    body = response.content.decode()
    assert response.status_code == 200
    assert "8.5" in body
    assert "A test synopsis describing the film." in body


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
    ) as metrics_mgr:
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
    ) as metrics_mgr:
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
    ) as metrics_mgr:
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
    ) as metrics_mgr:
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
    ) as metrics_mgr:
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
    ) as metrics_mgr:
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
# genre_detail
# ---------------------------------------------------------------------------


def test_genre_detail_returns_200_with_expected_context():
    genre = Genre(genre_id=1, genre_name="Action")
    movie = _movie()
    # A real (unsaved) fact row rather than a MagicMock: the poster-card
    # include reverses a URL from m.movie.movie_id, and Django's template
    # variable resolution tries dict-style lookup first, which a MagicMock
    # happily (and wrongly) answers via __getitem__.
    top_row = MovieMetrics(movie=movie, rating=Decimal("9.00"))
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
        "signature_partnerships.sql": [
            {"director_name": "D", "collaborator_name": "C", "craft": "Editor",
             "films_together": 11, "first_year": 1980, "last_year": 2023}
        ],
        "department_reach.sql": [
            {"department": "Acting", "credits": 62713, "people": 43138, "films": 1215}
        ],
        "franchise_revenue.sql": [
            {"franchise": "James Bond Collection", "entries": 17,
             "total_revenue": Decimal("6082635670")}
        ],
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


# ---------------------------------------------------------------------------
# tmdb_images template filter
# ---------------------------------------------------------------------------


def test_tmdb_image_filter_builds_url_and_handles_empty():
    from movies.templatetags.tmdb_images import tmdb_image

    assert tmdb_image("/abc.jpg", "w342").endswith("/w342/abc.jpg")
    assert tmdb_image("", "w342") == ""
    assert tmdb_image(None) == ""


# ---------------------------------------------------------------------------
# collection_list / collection_detail
# ---------------------------------------------------------------------------

def test_collection_list_returns_200():
    collection = Collection(collection_id=1, name="James Bond Collection",
                            slug="james-bond-collection")

    with patch.object(Collection, "objects", new=MagicMock()) as coll_mgr:
        # The view annotates a film count, drops empty franchises, then orders.
        coll_mgr.using.return_value.annotate.return_value.filter.return_value.order_by.return_value = [
            collection
        ]

        response = client.get("/franchises/")

    assert response.status_code == 200
    assert list(response.context["collections"]) == [collection]


def test_collection_detail_returns_200_with_series_totals():
    collection = Collection(collection_id=1, name="James Bond Collection",
                            slug="james-bond-collection")
    film = _movie(movie_id=1, title="Dr. No")
    film.slug = "dr-no"

    films = MagicMock()
    films.__iter__.return_value = iter([film])
    films.count.return_value = 1
    films.aggregate.side_effect = [
        {"total_revenue": 100, "total_budget": 10},
        {"first": date(1962, 10, 5), "last": date(1971, 12, 14)},
    ]

    with patch("movies.views.get_object_or_404", return_value=collection), \
            patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, \
            patch.object(MovieMetrics, "objects", new=MagicMock()) as metrics_mgr:
        movie_mgr.using.return_value.filter.return_value.order_by.return_value = films
        metrics_mgr.using.return_value.filter.return_value.values.return_value.distinct.return_value = [
            {"movie_id": 1, "rating": Decimal("7.00")}
        ]

        response = client.get("/franchises/james-bond-collection/")

    assert response.status_code == 200
    assert response.context["collection"] is collection
    assert response.context["film_count"] == 1
    assert response.context["total_revenue"] == 100
    assert response.context["avg_rating"] == Decimal("7.00")
    assert response.context["span"] == "1962–1971"


def test_collection_detail_404_for_unknown_slug():
    from django.http import Http404

    with patch("movies.views.get_object_or_404", side_effect=Http404()):
        response = client.get("/franchises/nope/")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# connect (path finder)
# ---------------------------------------------------------------------------

from movies import graph as graph_module  # noqa: E402


def _fake_graph(adjacency, names=None):
    """Patch the graph module's loader so no warehouse connection is opened."""
    return patch.object(
        graph_module, "get_graph", return_value=(adjacency, names or {})
    )


def test_find_path_returns_the_films_that_connect_two_people():
    """A 2-hop path names both intermediate films, not just the distance."""
    adjacency = {
        1: {2: 100},
        2: {1: 100, 3: 200},
        3: {2: 200},
    }

    with _fake_graph(adjacency):
        steps = graph_module.find_path(1, 3)

    assert steps == [(1, 100, 2), (2, 200, 3)]


def test_find_path_returns_empty_list_for_the_same_person():
    with _fake_graph({1: {}}):
        assert graph_module.find_path(1, 1) == []


def test_find_path_returns_none_when_components_are_disjoint():
    """The graph is not one piece — 23 components in the live data."""
    adjacency = {1: {2: 100}, 2: {1: 100}, 9: {}}

    with _fake_graph(adjacency):
        assert graph_module.find_path(1, 9) is None


def test_find_path_returns_none_for_an_uncredited_person():
    with _fake_graph({1: {2: 100}, 2: {1: 100}}):
        assert graph_module.find_path(1, 4242) is None


def test_component_stats_reports_the_giant_component_share():
    adjacency = {1: {2: 100}, 2: {1: 100}, 3: {4: 200}, 4: {3: 200}, 5: {}}

    with _fake_graph(adjacency):
        stats = graph_module.component_stats()

    assert stats["people"] == 5
    assert stats["components"] == 3
    assert stats["largest"] == 2
    assert stats["largest_share"] == 40.0


def test_connect_without_input_shows_the_shape_of_the_graph():
    with patch.object(graph_module, "component_stats", return_value={
        "people": 49276, "components": 23, "largest": 48853, "largest_share": 99.1,
    }):
        response = client.get("/connect/")

    assert response.status_code == 200
    assert response.context["stats"]["largest_share"] == 99.1
    assert "result" not in response.context


def test_connect_renders_the_chain_between_two_people():
    hanks = _person(person_id=1, name="Tom Hanks", slug="tom-hanks")
    editor = _person(person_id=3, name="Thelma Schoonmaker", slug="thelma-schoonmaker")
    movie = _movie(movie_id=100, title="Catch Me If You Can")
    movie.slug = "catch-me-if-you-can"

    with patch("movies.views._find_person", side_effect=[hanks, editor]), \
            patch.object(graph_module, "find_path", return_value=[(1, 100, 3)]), \
            patch("movies.views._describe_path", return_value=[
                {"person": hanks, "movie": movie, "next_person": editor}
            ]):
        response = client.get("/connect/", {"from": "Tom Hanks", "to": "Thelma"})

    assert response.status_code == 200
    assert response.context["result"] == "path"
    assert response.context["degrees"] == 1
    assert response.context["chain"][0]["movie"] is movie


def test_connect_reports_an_unconnected_pair_rather_than_failing():
    a = _person(person_id=1, name="A", slug="a")
    b = _person(person_id=2, name="B", slug="b")

    with patch("movies.views._find_person", side_effect=[a, b]), \
            patch.object(graph_module, "find_path", return_value=None):
        response = client.get("/connect/", {"from": "A", "to": "B"})

    assert response.status_code == 200
    assert response.context["result"] == "unconnected"


def test_connect_reports_an_unknown_name():
    with patch("movies.views._find_person", side_effect=[None, None]):
        response = client.get("/connect/", {"from": "Nobody", "to": "Tom Hanks"})

    assert response.status_code == 200
    assert response.context["result"] == "not_found"
    assert response.context["missing"] == "Nobody"
