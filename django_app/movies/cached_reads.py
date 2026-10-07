"""Cached warehouse reads (Tasks 115+): builders, and the readers views call.

Each builder runs the real queries and returns *plain, fully evaluated* data --
numbers, dicts, or lists of model instances for card shelves. Never a lazy
queryset: it would pickle its SQL, not its rows, and run the query again on
every hit. The readers wrap a builder in core.datacache.cached(), which adds
the data version to the key, serves hits without touching the warehouse, and
falls back to the builder if the cache is down.

The language goes in the key exactly when the value holds translated text.
"""

from __future__ import annotations

import itertools

from django.db.models import Avg, F, Max, Q

from core.datacache import cached
from movies.i18n import current_lang, localize_movies, localize_series
from movies.models import Movie, MovieRating, Person, Series


def _approx(n):
    """Round a count down to a rounder, approximate figure for the landing page:
    1217 -> 1200, 122685 -> 120000. The homepage shows scale, not exact
    inventory, and the number is rendered with a trailing "+"."""
    if n < 100:
        return n
    if n < 10_000:
        step = 100
    elif n < 100_000:
        step = 1_000
    else:
        step = 10_000
    return n - (n % step)


def _interleave_mosaic(primary, secondary, ratio):
    """Merge two home-mosaic tile lists so `secondary` tiles are spaced
    roughly one every `ratio` positions among `primary`'s, rather than
    clumped at one end of the (decorative, aria-hidden) collage (Task 90)."""
    merged = []
    primary_iter = iter(primary)
    secondary_iter = iter(secondary)
    while True:
        merged.extend(itertools.islice(primary_iter, ratio))
        try:
            merged.append(next(secondary_iter))
        except StopIteration:
            merged.extend(primary_iter)
            break
    return merged


# --- Home page (Task 115) ----------------------------------------------------


def build_home_stats():
    """The three figures under the hero. Plain numbers, language-free."""
    return {
        # Shown as "1,200+" etc. — an approximate figure, not an exact count.
        # Credits (every fact_credit row) was dropped: a raw join-table volume
        # means nothing to a visitor, unlike Movies / People / Avg rating.
        "movie_count": _approx(Movie.objects.using("warehouse").count()),
        "person_count": _approx(Person.objects.using("warehouse").count()),
        # Reads fact_movie_rating instead of fact_movie_metrics (Task 68):
        # the old figure averaged every fact_movie_metrics row, silently
        # over-weighting multi-genre films since a film's rating repeats
        # once per genre there. This is a true per-film average.
        "avg_rating": MovieRating.objects.using("warehouse").filter(
            source="imdb"
        ).aggregate(avg_rating=Avg("rating"))["avg_rating"],
    }


def build_home_mosaic():
    """The poster contact sheet: plain dicts, language-free."""
    # The mosaic mixes movies and shows now (Task 90) — it's meant to read as
    # "the whole catalog at once" (MOSAIC_LIMIT's own docstring), and TV is
    # part of that catalog. A fixed 100:20 split, not the live ~5:3 catalog
    # ratio (1,217 movies : 734 shows) — movies stay the dominant surface of
    # the site (nav order, the "world of movies" hero copy) while TV still
    # gets a real, visibly-present slice rather than a token single tile.
    # Only films/shows with a poster (a missing image would punch a hole in
    # the sheet); neither list renders through a card partial, so neither
    # needs its imdb_rating annotated.
    movie_tiles = [
        {"kind": "movie", "slug": slug, "poster_path": poster_path}
        for slug, poster_path in
        Movie.objects.using("warehouse")
        .filter(poster_path__isnull=False)
        .order_by(F("release_date").desc(nulls_last=True), "movie_id")
        .values_list("slug", "poster_path")[:100]
    ]
    series_tiles = [
        {"kind": "series", "slug": slug, "poster_path": poster_path}
        for slug, poster_path in
        Series.objects.using("warehouse")
        .filter(poster_path__isnull=False)
        .order_by(F("first_air_date").desc(nulls_last=True), "series_id")
        .values_list("slug", "poster_path")[:20]
    ]
    return _interleave_mosaic(movie_tiles, series_tiles, ratio=5)


def build_home_shelves():
    """The three card shelves, as evaluated lists in the active language."""
    # Imported here: views imports this module at load time, so a top-level
    # import back would be circular. (The helper is shared with the list pages.)
    from movies.views import _series_year_span

    top_rated = list(
        localize_movies(Movie.objects.using("warehouse"))
        .annotate(imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb")))
        .order_by(F("imdb_rating").desc(nulls_last=True), "movie_id")[:12]
    )
    newest = list(
        localize_movies(Movie.objects.using("warehouse"))
        .annotate(imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb")))
        .order_by(F("release_date").desc(nulls_last=True), "movie_id")[:12]
    )
    # Task 90: TV's "what's new" analogue orders by *last* air date, not
    # first — a long-running show that just aired a new episode is genuinely
    # "recently aired" in a way a canceled show that merely premiered the
    # same year is not. A movie has one date that means both things at once;
    # a show doesn't, so this can't just reuse newest's ordering.
    recently_aired = list(
        localize_series(Series.objects.using("warehouse"))
        .annotate(imdb_rating=Max("seriesrating__rating", filter=Q(seriesrating__source="imdb")))
        .order_by(F("last_air_date").desc(nulls_last=True), "series_id")[:12]
    )
    for show in recently_aired:
        show.year_span = _series_year_span(show)
    return {"top_rated": top_rated, "newest": newest, "recently_aired": recently_aired}


def home_stats():
    return cached("home_stats", build_home_stats)


def home_mosaic():
    return cached("home_mosaic", build_home_mosaic)


def home_shelves():
    # Titles are translated, so the language is part of the key; without it the
    # first Russian reader's titles would be served to everyone.
    return cached("home_shelves", build_home_shelves, lang=current_lang())
