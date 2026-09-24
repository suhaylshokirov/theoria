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
