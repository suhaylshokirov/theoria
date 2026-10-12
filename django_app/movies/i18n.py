"""Translated text, read at request time (Task 94).

Two kinds of translated text reach a page, and they take different routes:

* Per-entity prose (a film's title/overview/tagline, a show's name/overview/
  tagline, a person's biography) is annotated onto the queryset by
  `localize_movies()` / `localize_series()` / `localize_people()`, so a sort
  can run against the translated column in SQL. A title *search* does not
  depend on the reader's language: it always looks through English and
  Russian (`SEARCH_LANGS`), see `filter_by_title()`.
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

from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils.translation import get_language

from core.datacache import cached

from movies.models import (
    CountryTranslation, GenreTranslation, Movie, MovieTranslation,
    PersonTranslation, Series, SeriesTranslation,
)

DEFAULT_LANG = "en"

# Languages whose titles are searched on *every* request, whatever language the
# reader has picked. English is always searched (it is the column on the title
# itself) and Russian is added: a reader on the English site can type "Начало"
# and one on the Russian site can type "Inception". Uzbek has no translated
# titles (TMDB sends none), so it adds nothing and is left out. A 4th language
# that ships real titles belongs here.
SEARCH_LANGS = ("ru",)


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


def localize_series(queryset):
    """Annotate name/overview/tagline in the reader's language (Task 109).

    The TV twin of localize_movies(): adds `name_i18n`, `overview_i18n`,
    `tagline_i18n` (translated, else English) and the raw `name_tr`,
    `overview_tr`, `tagline_tr` (translated, else NULL).
    """
    lang = current_lang()
    if lang == DEFAULT_LANG:
        return queryset
    return queryset.annotate(
        name_tr=_column(SeriesTranslation, "series_id", "name", lang),
        overview_tr=_column(SeriesTranslation, "series_id", "overview", lang),
        tagline_tr=_column(SeriesTranslation, "series_id", "tagline", lang),
    ).annotate(
        name_i18n=Coalesce("name_tr", "name"),
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


def movie_titles_by_slug(slugs):
    """{slug: translated title} for the given movie slugs; {} for English.

    For the analytics callouts, which arrive as plain dict rows keyed by slug
    rather than as Movie objects. Slugs with no translation are simply absent,
    so the caller keeps the English title.
    """
    lang = current_lang()
    slugs = {slug for slug in slugs if slug}
    if lang == DEFAULT_LANG or not slugs:
        return {}
    return dict(
        MovieTranslation.objects.using("warehouse")
        .filter(movie__slug__in=slugs, lang=lang)
        .exclude(title__isnull=True)
        .exclude(title="")
        .values_list("movie__slug", "title")
    )


def attach_series_titles(series):
    """Set `name_i18n` on already-loaded Series objects (in place).

    The show-side twin of attach_movie_titles(), for a person's filmography
    where shows arrive through fact_series_credit.
    """
    lang = current_lang()
    series = list(series)
    if lang == DEFAULT_LANG or not series:
        return
    names = dict(
        SeriesTranslation.objects.using("warehouse")
        .filter(series_id__in={s.series_id for s in series}, lang=lang)
        .exclude(name__isnull=True)
        .exclude(name="")
        .values_list("series_id", "name")
    )
    for show in series:
        if show.series_id in names:
            show.name_i18n = names[show.series_id]


def title_order(sort_key, english_order):
    """`english_order` for English; the translated-title ordering otherwise.

    Only the "title" sort has a translated column to order by — every other
    sort key is a number or a date and is language-independent.
    """
    if sort_key == "title" and is_translated_request():
        return F("title_i18n").asc()
    return english_order


def _search_langs():
    """The translation languages a title search looks through: the fixed
    SEARCH_LANGS plus the reader's own (a no-op today — every language that has
    titles is already in SEARCH_LANGS — but it keeps a newly added language
    searchable for its own readers without touching this list)."""
    return {*SEARCH_LANGS, current_lang()}


def filter_by_title(queryset, term):
    """Narrow a movie queryset to titles containing `term`.

    Matches the English title or the title in any search language (Russian
    always, plus the reader's own), whichever language the page is in — the
    English title is printed on every page as the original, and a reader may
    type either spelling. The translated side is an EXISTS against
    movie_translation rather than the localize_movies() annotation, so the
    queryset needn't have been through it (and English requests, which skip
    that annotation, search Russian too).
    """
    return queryset.filter(
        Q(title__icontains=term)
        | Exists(
            MovieTranslation.objects.using("warehouse").filter(
                movie_id=OuterRef("pk"),
                lang__in=_search_langs(),
                title__icontains=term,
            )
        )
    )


def series_name_order(sort_key, english_order):
    """`english_order` for English; the translated-name ordering otherwise.

    The show-side twin of title_order(): only the "name" sort has a translated
    column; the rest are numbers or dates.
    """
    if sort_key == "name" and is_translated_request():
        return F("name_i18n").asc()
    return english_order


def filter_by_series_name(queryset, term):
    """Narrow a show queryset to names containing `term` — the English name or
    one in any search language, as filter_by_title() does for films."""
    return queryset.filter(
        Q(name__icontains=term)
        | Exists(
            SeriesTranslation.objects.using("warehouse").filter(
                series_id=OuterRef("pk"),
                lang__in=_search_langs(),
                name__icontains=term,
            )
        )
    )


def search_aliases(objects):
    """{movie/show: [its titles in the search languages]} for loaded objects.

    For the one search that runs in Python rather than SQL (a person's
    filmography, already merged and in memory). `objects` is any mix of Movie
    and Series; an object with no translated title is absent from the result.
    Model equality is (concrete model, pk), so a film and a show sharing an id
    stay distinct keys.
    """
    objects = list(objects)
    langs = _search_langs()
    aliases = {}
    for model, translation, fk, column in (
        (Movie, MovieTranslation, "movie_id", "title"),
        (Series, SeriesTranslation, "series_id", "name"),
    ):
        by_id = {o.pk: o for o in objects if isinstance(o, model)}
        if not by_id:
            continue
        rows = (
            translation.objects.using("warehouse")
            .filter(**{f"{fk}__in": list(by_id), "lang__in": langs})
            .exclude(**{f"{column}__isnull": True})
            .exclude(**{column: ""})
            .values_list(fk, column)
        )
        for obj_id, text in rows:
            aliases.setdefault(by_id[obj_id], []).append(text)
    return aliases


def genre_labels():
    """{English genre name: name in the reader's language}; {} for English."""
    lang = current_lang()
    if lang == DEFAULT_LANG:
        return {}
    # Cached per language: every translated page that shows a genre asks, and
    # the table only changes when the pipeline loads it. English returned above,
    # before the cache is touched, so the common case costs nothing.
    return cached(
        "genre_labels",
        lambda: dict(
            GenreTranslation.objects.using("warehouse")
            .filter(lang=lang)
            .values_list("genre__genre_name", "genre_name")
        ),
        lang=lang,
    )


def country_labels():
    """{English country name: name in the reader's language}; {} for English."""
    lang = current_lang()
    if lang == DEFAULT_LANG:
        return {}
    return cached(
        "country_labels",
        lambda: dict(
            CountryTranslation.objects.using("warehouse")
            .filter(lang=lang)
            .values_list("country__name", "name")
        ),
        lang=lang,
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
