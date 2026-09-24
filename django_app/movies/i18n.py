"""Translated text, read at request time (Task 94).

Two kinds of translated text reach a page, and they take different routes:

* Per-entity prose (a film's title/overview/tagline, a person's biography) is
  annotated onto the queryset by `localize_movies()` / `localize_people()`, so
  a search or a sort can run against the translated column in SQL.
* The two fixed vocabularies (genre names, country names) are small enough to
  load whole: `genre_labels()` / `country_labels()` return {English name:
  translated name}, applied wherever a name is rendered — including the
  analytics rows, whose SQL files stay English.

English is the source language and needs none of this: every helper returns
its input untouched for `en`, so the English site issues the same queries it
did before Task 94. A missing translation is never an error — Coalesce falls
back to the English column, and the model's `display_*` properties fall back
to it when nothing was annotated at all.
"""

from django.db.models import F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils.translation import get_language

from movies.models import (
    CountryTranslation, GenreTranslation, MovieTranslation, PersonTranslation,
)

DEFAULT_LANG = "en"


def current_lang():
    """The bare language code of this request (`ru`, not `ru-ru`)."""
    return (get_language() or DEFAULT_LANG).split("-")[0]


def is_translated_request():
    return current_lang() != DEFAULT_LANG


def _column(model, fk_name, column, lang):
    """A correlated subquery: this row's translated `column`, or NULL.

    TMDB sends "" (not null) for a field it has no translation of, so blanks
    are excluded here — otherwise Coalesce would happily pick an empty string
    over the English text.
    """
    return Subquery(
        model.objects.using("warehouse")
        .filter(**{fk_name: OuterRef("pk"), "lang": lang})
        .exclude(**{f"{column}__isnull": True})
        .exclude(**{column: ""})
        .values(column)[:1]
    )


def localize_movies(queryset):
    """Annotate title/overview/tagline in the reader's language.

    Adds `title_i18n`, `overview_i18n`, `tagline_i18n` (translated, else
    English) and the raw `title_tr`, `overview_tr`, `tagline_tr` (translated,
    else NULL) — the raw ones are how a template learns a fallback happened.
    """
    lang = current_lang()
    if lang == DEFAULT_LANG:
        return queryset
    return queryset.annotate(
        title_tr=_column(MovieTranslation, "movie_id", "title", lang),
        overview_tr=_column(MovieTranslation, "movie_id", "overview", lang),
        tagline_tr=_column(MovieTranslation, "movie_id", "tagline", lang),
    ).annotate(
        title_i18n=Coalesce("title_tr", "title"),
        overview_i18n=Coalesce("overview_tr", "overview"),
        tagline_i18n=Coalesce("tagline_tr", "tagline"),
    )


def localize_people(queryset):
    """Annotate `biography_i18n` and the raw `biography_tr` (see above)."""
    lang = current_lang()
    if lang == DEFAULT_LANG:
        return queryset
    return queryset.annotate(
        biography_tr=_column(PersonTranslation, "person_id", "biography", lang),
    ).annotate(biography_i18n=Coalesce("biography_tr", "biography"))


def attach_movie_titles(movies):
    """Set `title_i18n` on already-loaded Movie objects (in place).

    For movies that arrive through a join (a person's filmography reads them
    off fact_credit) where there is no movie queryset to annotate.
    """
    lang = current_lang()
    movies = list(movies)
    if lang == DEFAULT_LANG or not movies:
        return
    titles = dict(
        MovieTranslation.objects.using("warehouse")
        .filter(movie_id__in={m.movie_id for m in movies}, lang=lang)
        .exclude(title__isnull=True)
        .exclude(title="")
        .values_list("movie_id", "title")
    )
    for movie in movies:
        if movie.movie_id in titles:
            movie.title_i18n = titles[movie.movie_id]


def title_order(sort_key, english_order):
    """`english_order` for English; the translated-title ordering otherwise.

    Only the "title" sort has a translated column to order by — every other
    sort key is a number or a date and is language-independent.
    """
    if sort_key == "title" and is_translated_request():
        return F("title_i18n").asc()
    return english_order


def filter_by_title(queryset, term):
    """Narrow a movie queryset to titles containing `term`.

    On a translated request this matches the translated title *or* the
    English one (a reader on /ru/ may type "Начало" or "Inception" — the
    English title is still printed on the page as the original), which needs
    the queryset to have been through localize_movies().
    """
    if is_translated_request():
        return queryset.filter(Q(title_i18n__icontains=term) | Q(title__icontains=term))
    return queryset.filter(title__icontains=term)


def genre_labels():
    """{English genre name: name in the reader's language}; {} for English."""
    lang = current_lang()
    if lang == DEFAULT_LANG:
        return {}
    return dict(
        GenreTranslation.objects.using("warehouse")
        .filter(lang=lang)
        .values_list("genre__genre_name", "genre_name")
    )


def country_labels():
    """{English country name: name in the reader's language}; {} for English."""
    lang = current_lang()
    if lang == DEFAULT_LANG:
        return {}
    return dict(
        CountryTranslation.objects.using("warehouse")
        .filter(lang=lang)
        .values_list("country__name", "name")
    )


def localize_genres(genres):
    """Set `name_i18n` on Genre objects and, when translated, return them
    ordered by that name (an English alphabetical order is meaningless in
    Cyrillic). English input is returned untouched."""
    labels = genre_labels()
    if not labels:
        return genres
    genres = list(genres)
    for genre in genres:
        genre.name_i18n = labels.get(genre.genre_name)
    return sorted(genres, key=lambda g: g.display_name.lower())


def localize_rows(rows, key, labels):
    """Replace `row[key]` with its translation in a list of dict rows (the
    analytics queries). Names with no translation keep their English text."""
    if not labels:
        return rows
    for row in rows:
        row[key] = labels.get(row[key], row[key])
    return rows
