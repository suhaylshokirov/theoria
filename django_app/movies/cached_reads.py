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

from django.db.models import Avg, Count, F, IntegerField, Max, Q
from django.db.models.functions import Cast, ExtractYear

from core.datacache import cached
from movies.i18n import current_lang, genre_labels, localize_movies, localize_series
from movies.models import Credit, Genre, Movie, MovieMetrics, MovieRating, Person, Series


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
    """The figures under the hero, plus the decade chart. Plain numbers,
    language-free."""
    return {
        # Shown as "1,200+" etc. — an approximate figure, not an exact count.
        # Credits (every fact_credit row) was dropped: a raw join-table volume
        # means nothing to a visitor, unlike Movies / People / Avg rating.
        "movie_count": _approx(Movie.objects.using("warehouse").count()),
        # int() before _approx(): the count is already an int in production;
        # the home view's tests mock Series without configuring a count.
        "series_count": _approx(int(Series.objects.using("warehouse").count())),
        "person_count": _approx(Person.objects.using("warehouse").count()),
        # Reads fact_movie_rating instead of fact_movie_metrics (Task 68):
        # the old figure averaged every fact_movie_metrics row, silently
        # over-weighting multi-genre films since a film's rating repeats
        # once per genre there. This is a true per-film average.
        "avg_rating": MovieRating.objects.using("warehouse").filter(
            source="imdb"
        ).aggregate(avg_rating=Avg("rating"))["avg_rating"],
        "decades": build_home_decades(),
    }


def build_home_decades():
    """Films released and their average IMDb rating, per decade of release.

    The same figures as the dashboard's movies_by_decade panel, kept here as an
    ORM read so the home page doesn't pay for all eight dashboard queries on a
    cold cache. Each bar's height is a share of the tallest decade, worked out
    here so the template only has to print it.
    """
    rows = list(
        Movie.objects.using("warehouse")
        .filter(release_date__isnull=False)
        # Cast first: Postgres EXTRACT returns numeric, and numeric / 10 keeps
        # the remainder (1994 / 10 * 10 = 1994) where integer division drops it.
        .annotate(decade=Cast(ExtractYear("release_date"), IntegerField()) / 10 * 10)
        .values("decade")
        .annotate(
            film_count=Count("movie_id", distinct=True),
            avg_rating=Avg("movierating__rating", filter=Q(movierating__source="imdb")),
        )
        .order_by("decade")
    )
    tallest = max((row["film_count"] for row in rows), default=0)
    best = max((row["avg_rating"] for row in rows if row["avg_rating"]), default=None)
    return [
        {
            "decade": row["decade"],
            "film_count": row["film_count"],
            "avg_rating": round(float(row["avg_rating"]), 2) if row["avg_rating"] else None,
            "height_pct": round(row["film_count"] / tallest * 100, 1) if tallest else 0,
            "is_best": best is not None and row["avg_rating"] == best,
        }
        for row in rows
    ]


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
    return {
        "top_rated": top_rated,
        "newest": newest,
        "recently_aired": recently_aired,
        "featured": _featured_films(top_rated),
    }


# How many films the home page's "Now showing" reel cycles through.
FEATURED_LIMIT = 6


def _featured_films(top_rated):
    """The hero reel: the highest-rated films that have a backdrop to show.

    Drawn from the top_rated shelf already in hand, so the reel and the shelf
    can never disagree. Each film gets `reel_genres` (display names, at most
    two) and `reel_director` set in place -- two queries for the whole reel,
    none at all when no film has a backdrop.
    """
    featured = [m for m in top_rated if m.backdrop_path][:FEATURED_LIMIT]
    if not featured:
        return []
    ids = [m.movie_id for m in featured]

    labels = genre_labels()
    genres = {}
    for movie_id, name in (
        MovieMetrics.objects.using("warehouse")
        .filter(movie_id__in=ids)
        .values_list("movie_id", "genre__genre_name")
        .distinct()
        .order_by("movie_id", "genre__genre_name")
    ):
        genres.setdefault(movie_id, []).append(labels.get(name, name))

    directors = {}
    for movie_id, name in (
        Credit.objects.using("warehouse")
        .filter(movie_id__in=ids, job="Director")
        .order_by(F("ordering").asc(nulls_last=True), "person_id")
        .values_list("movie_id", "person__name")
    ):
        directors.setdefault(movie_id, name)

    for movie in featured:
        movie.reel_genres = genres.get(movie.movie_id, [])[:2]
        movie.reel_director = directors.get(movie.movie_id, "")
    return featured


def home_stats():
    return cached("home_stats", build_home_stats)


def home_mosaic():
    return cached("home_mosaic", build_home_mosaic)


def home_shelves():
    # Titles are translated, so the language is part of the key; without it the
    # first Russian reader's titles would be served to everyone.
    return cached("home_shelves", build_home_shelves, lang=current_lang())


# --- Genre choice lists (Task 117) -------------------------------------------


def build_genre_rows(kind):
    """`[(genre_id, english_name)]` for the genres that have at least one title.

    `kind` is "movie" or "series". Language-free: the views build the
    ?genre= slugs from the English name and translate only the visible label,
    so one entry per kind serves every language.
    """
    if kind == "movie":
        # Only genres that actually have a film in the catalog are offered as a
        # choice — Documentary currently has 0, and a choice that can never
        # return anything is worse than not offering it. distinct=True matters
        # because fact_movie_metrics' PK is (movie_id, date_id, genre_id), and a
        # film whose release date moved between ingestions holds two date_id
        # rows per genre, which would otherwise double-count its film_count.
        queryset = Genre.objects.using("warehouse").annotate(
            title_count=Count("moviemetrics__movie", distinct=True)
        )
    elif kind == "series":
        # A plain count on bridge_series_genre: each (series, genre) pair is
        # already unique there, unlike fact_movie_metrics' grain.
        queryset = Genre.objects.using("warehouse").annotate(
            title_count=Count("series_genres")
        )
    else:
        raise ValueError(f"unknown genre kind: {kind!r}")
    return list(
        queryset.filter(title_count__gt=0)
        .order_by("genre_name")
        .values_list("genre_id", "genre_name")
    )


def genre_rows(kind):
    return cached("genre_rows", lambda: build_genre_rows(kind), kind)
