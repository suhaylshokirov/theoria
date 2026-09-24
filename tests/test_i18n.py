"""Task 92 — the trilingual interface (en / ru / uz).

Same "mock the boundary" pattern as test_django_views.py: every warehouse
manager is mocked, the views are exercised through Django's test Client.
These tests guard the failure modes that give no error of their own: a
missing .mo (the site silently serves English), a translated string used as
logic, a Russian plural collapsing to one form, and a localized decimal
comma landing in a machine-read attribute.
"""

import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.test_django_views import (  # noqa: F401  (also runs django.setup())
    client, setup_module, teardown_module, _movie, _series,
)
from django.test import override_settings
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext, ngettext

from movies.models import Genre, Movie, MovieRating, Person, Series

LOCALE_DIR = Path(__file__).resolve().parent.parent / "django_app" / "locale"
PREFIXES = {"en": "", "ru": "/ru", "uz": "/uz"}


def _home_mocks():
    """Patch every manager the home page reads, returning the context stack."""
    import contextlib

    movie = _movie()
    show = _series()
    stack = contextlib.ExitStack()
    movie_mgr = stack.enter_context(patch.object(Movie, "objects", new=MagicMock()))
    series_mgr = stack.enter_context(patch.object(Series, "objects", new=MagicMock()))
    person_mgr = stack.enter_context(patch.object(Person, "objects", new=MagicMock()))
    rating_mgr = stack.enter_context(patch.object(MovieRating, "objects", new=MagicMock()))
    using = movie_mgr.using.return_value
    using.count.return_value = 1217
    using.annotate.return_value.order_by.return_value.__getitem__.return_value = [movie]
    using.filter.return_value.order_by.return_value.values_list.return_value \
        .__getitem__.return_value = [(movie.slug, movie.poster_path)]
    series_using = series_mgr.using.return_value
    series_using.annotate.return_value.order_by.return_value.__getitem__.return_value = [show]
    series_using.filter.return_value.order_by.return_value.values_list.return_value \
        .__getitem__.return_value = [(show.slug, show.poster_path)]
    person_mgr.using.return_value.count.return_value = 122685
    rating_mgr.using.return_value.filter.return_value.aggregate.return_value = {
        "avg_rating": Decimal("6.84")
    }
    return stack


def _movie_list_mocks():
    import contextlib

    stack = contextlib.ExitStack()
    movie_mgr = stack.enter_context(patch.object(Movie, "objects", new=MagicMock()))
    genre_mgr = stack.enter_context(patch.object(Genre, "objects", new=MagicMock()))
    genre_mgr.using.return_value.annotate.return_value.filter.return_value \
        .order_by.return_value.values_list.return_value = []
    qs = movie_mgr.using.return_value.all.return_value
    qs.filter.return_value = qs
    qs.annotate.return_value = qs
    qs.order_by.return_value = [_movie(movie_id=i, title=f"Movie {i}") for i in range(1, 4)]
    return stack


# --- URL space -------------------------------------------------------------


def test_english_urls_are_unprefixed_and_unchanged():
    with translation.override("en"):
        assert reverse("movies:home") == "/"
        assert reverse("movies:movie_list") == "/movies/"
        assert reverse("movies:movie_detail", args=["fight-club"]) == "/movies/fight-club/"
        assert reverse("analytics:dashboard") == "/analytics/"
        assert reverse("profile") == "/me/"


@pytest.mark.parametrize("lang", ["ru", "uz"])
def test_other_languages_get_a_prefix_and_keep_the_english_slug(lang):
    with translation.override(lang):
        assert reverse("movies:movie_list") == f"/{lang}/movies/"
        # Slugs are the URL identity and never translate.
        assert reverse("movies:movie_detail", args=["fight-club"]) == \
            f"/{lang}/movies/fight-club/"


# --- Pages render under all three prefixes ----------------------------------


@pytest.mark.parametrize("lang", ["en", "ru", "uz"])
def test_home_renders_in_every_language(lang):
    with _home_mocks():
        response = client.get(f"{PREFIXES[lang]}/")
    assert response.status_code == 200
    assert f'<html lang="{lang}">' in response.content.decode()


@pytest.mark.parametrize("lang", ["en", "ru", "uz"])
def test_movie_list_renders_in_every_language(lang):
    with _movie_list_mocks():
        response = client.get(f"{PREFIXES[lang]}/movies/")
    assert response.status_code == 200


@pytest.mark.parametrize("lang", ["en", "ru", "uz"])
def test_auth_pages_render_in_every_language(lang):
    anon = type(client)()
    for path in ("/accounts/login/", "/accounts/signup/"):
        assert anon.get(f"{PREFIXES[lang]}{path}").status_code == 200


@pytest.mark.parametrize("lang", ["en", "ru", "uz"])
def test_404_renders_in_every_language(lang):
    with override_settings(DEBUG=False):
        response = client.get(f"{PREFIXES[lang]}/no/such/page/")
    assert response.status_code == 404


def test_interface_is_actually_translated_not_silently_english():
    """A missing .mo fails silently — the site would just serve English."""
    with _movie_list_mocks():
        ru = client.get("/ru/movies/").content.decode()
    with _movie_list_mocks():
        uz = client.get("/uz/movies/").content.decode()
    assert "Сериалы" in ru and "TV Shows" not in ru
    assert "Seriallar" in uz and "TV Shows" not in uz


def test_compiled_catalogs_are_committed():
    for lang in ("ru", "uz"):
        assert (LOCALE_DIR / lang / "LC_MESSAGES" / "django.mo").stat().st_size > 1000


# --- The switcher -----------------------------------------------------------


def test_switcher_marks_the_current_language_and_offers_all_three():
    with _movie_list_mocks():
        body = client.get("/ru/movies/").content.decode()
    assert body.count('class="lang-switch__opt"') == 3
    assert re.search(r'value="ru"[^>]*aria-current="true"', body)
    assert 'value="en"' in body and 'value="uz"' in body


@pytest.mark.parametrize("source", ["en", "ru", "uz"])
@pytest.mark.parametrize("target", ["en", "ru", "uz"])
def test_switcher_round_trips_to_the_same_page(source, target):
    """The form posts to the page's own language endpoint; the reader lands on
    the same page in the new language, query string kept -- not the homepage.
    Runs from every source language, because set_language only re-prefixes
    `next` correctly when the request itself is in the source language."""
    client.cookies.pop("django_language", None)
    with _movie_list_mocks():
        page = client.get(f"{PREFIXES[source]}/movies/?q=fight").content.decode()
    action = re.search(r'<form class="lang-switch" action="([^"]+)"', page).group(1)
    assert action == f"{PREFIXES[source]}/i18n/setlang/"
    next_url = re.search(r'name="next" value="([^"]+)"', page).group(1)
    response = client.post(action, {"language": target, "next": next_url})
    assert response.status_code == 302
    assert response["Location"] == f"{PREFIXES[target]}/movies/?q=fight"
    assert response.cookies["django_language"].value == target
    client.cookies.pop("django_language", None)


# --- Plurals ----------------------------------------------------------------


@pytest.mark.parametrize(
    "n, expected",
    [
        (1, "1 фильм"),
        (2, "2 фильма"),
        (5, "5 фильмов"),
        (11, "11 фильмов"),
        (21, "21 фильм"),
        (22, "22 фильма"),
        (100, "100 фильмов"),
    ],
)
def test_russian_plural_uses_three_distinct_forms(n, expected):
    """Django's |pluralize is the English rule only; Russian needs one/few/many."""
    with translation.override("ru"):
        text = ngettext("%(n)s movie", "%(n)s movies", n) % {"n": n}
    assert text == expected


def test_uzbek_has_a_single_plural_form():
    with translation.override("uz"):
        one = ngettext("%(n)s movie", "%(n)s movies", 1) % {"n": 1}
        many = ngettext("%(n)s movie", "%(n)s movies", 5) % {"n": 5}
    assert (one, many) == ("1 ta film", "5 ta film")


# --- Vocabulary: translated for display, never for logic --------------------


def test_vocabulary_translates_for_display_and_passes_the_long_tail_through():
    from movies.vocab import display_department, display_job

    with translation.override("ru"):
        assert display_job("Director") == "Режиссёр"
        assert display_department("Camera") == "Операторская работа"
        # Untranslated job titles fall back to English (gettext's msgid passthrough).
        assert display_job("Assistant Key Widget Polisher") == "Assistant Key Widget Polisher"
        assert display_job(None) is None


def test_department_logic_keys_stay_raw_english_in_every_language():
    from movies import views

    for lang in ("en", "ru", "uz"):
        with translation.override(lang):
            keys = [k if isinstance(k, str) else str(k) for k in views.DEPARTMENT_ORDER]
            assert "Acting" in keys and "Directing" in keys
            assert all(k.isascii() for k in keys)


# --- JS strings -------------------------------------------------------------


@pytest.mark.parametrize("lang", ["ru", "uz"])
def test_every_js_string_has_a_translation(lang):
    from core.context_processors import js_strings

    # Same word in another language is legitimate, but every other string
    # equal to its English key means a missing catalog entry.
    same_as_english_ok = {"Video", "Email"}
    with translation.override(lang):
        strings = js_strings(None)["js_strings"]
    untranslated = [
        key for key, value in strings.items()
        if value == key and key not in same_as_english_ok
    ]
    assert untranslated == []


# --- Number formatting ------------------------------------------------------


@pytest.mark.parametrize("lang", ["ru", "uz"])
def test_machine_read_numbers_never_get_a_decimal_comma(lang):
    """Django localizes floats in ru/uz (7,5). Displayed text may; an attribute
    the script parses with parseFloat may not."""
    with _home_mocks():
        body = client.get(f"/{lang}/").content.decode()
    counts = re.findall(r'data-count="([^"]*)"', body)
    assert counts, "home page should carry count-up figures"
    assert all("," not in c and " " not in c for c in counts)


# ===========================================================================
# Task 94 -- the site reads the translated data
# ===========================================================================

import contextlib  # noqa: E402

from movies import i18n  # noqa: E402
from movies.models import (  # noqa: E402
    Country, CountryTranslation, GenreTranslation, MovieTranslation,
)
from tests.test_django_views import _movie_detail_video_mocks  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_vocabulary_reads():
    """Under ru/uz the views read genre/country vocabularies. Keep the tests
    off the real warehouse: empty by default, overridden per test."""
    with patch.object(GenreTranslation, "objects", new=MagicMock()) as genres, \
            patch.object(CountryTranslation, "objects", new=MagicMock()) as countries:
        genres.using.return_value.filter.return_value.values_list.return_value = []
        countries.using.return_value.filter.return_value.values_list.return_value = []
        yield genres, countries


def _sql(queryset):
    sql, params = queryset.query.sql_with_params()
    return sql, params


def _translated_movie(**overrides):
    """A Movie as localize_movies() leaves it on a translated request."""
    movie = _movie(title="Inception")
    movie.slug = "inception"
    movie.original_title = "Inception"
    movie.overview = "A thief steals secrets."
    movie.tagline = "Your mind is the scene of the crime."
    attrs = {
        "title_tr": "Начало", "title_i18n": "Начало",
        "overview_tr": "Вор крадёт секреты.", "overview_i18n": "Вор крадёт секреты.",
        "tagline_tr": "Ваш разум — место преступления.",
        "tagline_i18n": "Ваш разум — место преступления.",
    }
    attrs.update(overrides)
    for name, value in attrs.items():
        setattr(movie, name, value)
    return movie


# --- localize_movies / localize_people --------------------------------------


def test_english_requests_are_not_touched():
    queryset = MagicMock()
    with translation.override("en"):
        assert i18n.localize_movies(queryset) is queryset
        assert i18n.localize_people(queryset) is queryset
        assert i18n.genre_labels() == {} and i18n.country_labels() == {}
    queryset.annotate.assert_not_called()


def test_localize_movies_builds_a_per_language_subquery_with_english_fallback():
    with translation.override("ru"):
        sql, params = _sql(i18n.localize_movies(Movie.objects.using("warehouse")))
    assert "movie_translation" in sql
    assert "COALESCE" in sql
    assert "ru" in params
    # A blank translation must not beat the English text.
    assert '"movie_translation"."title" IS NOT NULL' in sql or "IS NOT NULL" in sql


def test_localize_movies_uses_the_bare_language_code():
    with translation.override("ru"):
        assert i18n.current_lang() == "ru"
    with translation.override("uz"):
        assert i18n.current_lang() == "uz"


def test_localize_people_annotates_the_biography():
    with translation.override("ru"):
        sql, params = _sql(i18n.localize_people(Person.objects.using("warehouse")))
    assert "person_translation" in sql and "COALESCE" in sql and "ru" in params


# --- Sorting and searching --------------------------------------------------


def test_title_sort_reads_the_translated_column_only_when_translated():
    english = object()
    with translation.override("en"):
        assert i18n.title_order("title", english) is english
    with translation.override("ru"):
        assert i18n.title_order("title", english) is not english
        # Numeric/date sorts never depend on language.
        assert i18n.title_order("rating", english) is english


def test_search_on_english_is_the_plain_title_filter():
    queryset = MagicMock()
    with translation.override("en"):
        i18n.filter_by_title(queryset, "fight")
    queryset.filter.assert_called_once_with(title__icontains="fight")


def test_search_on_ru_matches_translated_or_english_title():
    queryset = MagicMock()
    with translation.override("ru"):
        i18n.filter_by_title(queryset, "Начало")
    (q,), _ = queryset.filter.call_args
    text = str(q)
    assert "title_i18n__icontains" in text and "title__icontains" in text
    assert "OR" in text


def test_russian_title_search_finds_the_film_on_the_list_page():
    with _movie_list_mocks():
        with patch("movies.views.filter_by_title") as search:
            search.side_effect = lambda qs, term: qs
            response = client.get("/ru/movies/?q=Начало")
    assert response.status_code == 200
    search.assert_called_once()
    assert search.call_args.args[1] == "Начало"


# --- The movie page ---------------------------------------------------------


def _detail(url_prefix, movie):
    with _movie_detail_video_mocks(movie, []):
        response = client.get(f"{url_prefix}/movies/{movie.slug}/")
    assert response.status_code == 200
    return response.content.decode()


def test_russian_movie_page_shows_the_russian_text():
    body = _detail("/ru", _translated_movie())
    assert "Начало" in body
    assert "Вор крадёт секреты." in body
    assert "Ваш разум — место преступления." in body
    assert "A thief steals secrets." not in body
    assert 'class="prose-note"' not in body
    # The English title is kept, as the "originally" line.
    assert "Inception" in body


def test_english_movie_page_is_unchanged_and_has_no_marker():
    movie = _movie(title="Inception")
    movie.slug = "inception"
    movie.overview = "A thief steals secrets."
    body = _detail("", movie)
    assert "A thief steals secrets." in body
    assert 'class="prose-note"' not in body


def test_uzbek_page_falls_back_to_english_prose_with_a_quiet_marker():
    movie = _translated_movie(
        title_tr=None, title_i18n="Inception",
        overview_tr=None, overview_i18n="A thief steals secrets.",
        tagline_tr=None, tagline_i18n="Your mind is the scene of the crime.",
    )
    body = _detail("/uz", movie)
    assert "A thief steals secrets." in body
    # The English paragraph is marked as English for screen readers.
    assert 'class="specimen-synopsis" lang="en"' in body
    assert 'class="prose-note"' in body
    assert "Inglizcha" in body


def test_marker_never_names_a_pipeline_internal():
    body = _detail("/uz", _translated_movie(overview_tr=None, overview_i18n="x"))
    note = re.search(r'<p class="prose-note">(.*?)</p>', body).group(1)
    for internal in ("translation", "_tr", "dim_", "movie_id", ".sql"):
        assert internal not in note


def test_translated_prose_shows_no_marker():
    body = _detail("/uz", _translated_movie())
    assert 'class="prose-note"' not in body


def test_display_properties_fall_back_to_english_when_not_annotated():
    movie = _movie(title="Inception")
    movie.overview = "English."
    assert movie.display_title == "Inception"
    assert movie.display_overview == "English."
    assert movie.prose_is_fallback is False


@pytest.mark.parametrize("lang", ["en", "ru", "uz"])
def test_movie_slug_is_the_same_in_every_language(lang):
    """A translated title never leaks into the URL: the slug is the identity."""
    with translation.override(lang):
        assert reverse("movies:movie_detail", args=["inception"]) == \
            f"{PREFIXES[lang]}/movies/inception/"


def test_genre_chip_keeps_the_english_slug_and_shows_the_translated_label():
    genre = Genre(genre_id=878, genre_name="Science Fiction")
    genre.name_i18n = "фантастика"
    movie = _translated_movie()
    with _movie_detail_video_mocks(movie, []) as _:
        with patch("movies.views.localize_genres", side_effect=lambda g: g):
            with patch.object(Genre, "objects", new=MagicMock()) as mgr:
                mgr.using.return_value.filter.return_value.distinct.return_value = [genre]
                body = client.get("/ru/movies/inception/").content.decode()
    assert "?genre=science-fiction" in body
    assert ">фантастика</a>" in body


# --- Vocabularies -----------------------------------------------------------


def test_localize_genres_sets_the_label_and_sorts_by_it():
    a = Genre(genre_id=1, genre_name="Action")
    b = Genre(genre_id=2, genre_name="Western")
    c = Genre(genre_id=3, genre_name="Comedy")
    with patch("movies.i18n.genre_labels", return_value={
        "Action": "боевик", "Western": "вестерн", "Comedy": "комедия",
    }):
        result = i18n.localize_genres([a, b, c])
    assert [g.display_name for g in result] == ["боевик", "вестерн", "комедия"]
    # The English name is still there for slugs.
    assert [g.genre_name for g in result] == ["Action", "Western", "Comedy"]


def test_genre_without_a_translation_keeps_its_english_name():
    a = Genre(genre_id=1, genre_name="Action")
    with patch("movies.i18n.genre_labels", return_value={"Comedy": "комедия"}):
        result = i18n.localize_genres([a])
    assert result[0].display_name == "Action"


def test_country_provenance_uses_translated_names():
    from movies.models import MovieCountry
    from movies.views import _country_provenance

    def row(name, relation):
        return MovieCountry(
            relation=relation, country=Country(country_code=name[:2], name=name)
        )

    rows = [row("Japan", "production"), row("France", "production")]
    with patch("movies.views.country_labels", return_value={"Japan": "Япония", "France": "Франция"}):
        result = _country_provenance(rows)
    assert result["countries"] == ["Франция", "Япония"]


def test_localize_rows_maps_names_and_leaves_the_rest():
    rows = [{"genre_name": "Action", "n": 1}, {"genre_name": "Odd", "n": 2}]
    result = i18n.localize_rows(rows, "genre_name", {"Action": "боевик"})
    assert [r["genre_name"] for r in result] == ["боевик", "Odd"]
    assert i18n.localize_rows([{"genre_name": "Action"}], "genre_name", {}) == [
        {"genre_name": "Action"}
    ]


def test_analytics_dashboard_translates_genre_and_country_names():
    fake = {
        "revenue_by_genre.sql": [
            {"genre_name": "Action", "movie_count": 3, "total_revenue": Decimal("1000")}
        ],
        "movies_by_decade.sql": [],
        "top_studios_by_revenue.sql": [],
        "films_by_production_country.sql": [
            {"country_name": "Japan", "film_count": 7, "avg_rating": Decimal("7.4")}
        ],
        "series_by_decade.sql": [], "episode_rating_by_season.sql": [],
        "longest_running_series.sql": [], "top_networks_by_series.sql": [],
    }
    with patch("analytics.views._run_query", side_effect=lambda f: fake[f]), \
            patch("analytics.views.genre_labels", return_value={"Action": "боевик"}), \
            patch("analytics.views.country_labels", return_value={"Japan": "Япония"}):
        response = client.get("/ru/analytics/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "боевик" in body and "Япония" in body
    # The chart labels come from the same translated rows.
    assert response.context["genre_labels"] == ["боевик"]


# --- Filmography ------------------------------------------------------------


def test_attach_movie_titles_sets_translated_titles_in_one_query():
    a, b = _movie(movie_id=1, title="Inception"), _movie(movie_id=2, title="Untranslated")
    with translation.override("ru"), patch.object(
        MovieTranslation, "objects", new=MagicMock()
    ) as mgr:
        mgr.using.return_value.filter.return_value.exclude.return_value \
            .exclude.return_value.values_list.return_value = [(1, "Начало")]
        i18n.attach_movie_titles([a, b])
    assert a.display_title == "Начало"
    assert b.display_title == "Untranslated"
    mgr.using.return_value.filter.assert_called_once()


def test_attach_movie_titles_does_nothing_in_english():
    a = _movie(movie_id=1, title="Inception")
    with translation.override("en"), patch.object(
        MovieTranslation, "objects", new=MagicMock()
    ) as mgr:
        i18n.attach_movie_titles([a])
    mgr.using.assert_not_called()
    assert a.display_title == "Inception"
