import itertools
from datetime import date

from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, F, Max, Min, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import urlencode
from django.utils.text import slugify

from core.models import CollectionItem
from core.services import collection_flags

from movies.models import (
    Company, Credit, Episode, Genre, Movie, MovieCompany,
    MovieCountry, MovieLanguage, MovieRating, MovieVideo, Person, Season,
    Series, SeriesCompany, SeriesCountry, SeriesCredit, SeriesLanguage,
    SeriesNetwork, SeriesRating, SeriesVideo,
)

MOVIES_PER_PAGE = 24
PEOPLE_PER_PAGE = 30
STUDIOS_PER_PAGE = 30


def _is_ajax(request):
    """True for the fetch() requests static/js/theoria.js's initLiveFilter()
    makes as a filter form changes — set explicitly in the JS, never sent by
    a plain browser navigation or a no-JS form submit."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"

# The movie page's cast and crew are paged in the browser, not here — see
# static/js/theoria.js. The page size lives in the template's data-page-size
# attribute, since it's a property of the rendered widget rather than of the
# query: this view sends every credit either way.
#
# The list pages (/movies/, /people/) stay server-side paged: those queries
# can return 1,215 and 122,685 rows, which is not a payload to hand a browser.
# One film's ~1,200 credits is.

# How many posters the home contact sheet draws. The mosaic is meant to read
# as "the whole catalog at once", so this is a ceiling, not a page size. Since
# the Task 42 corpus expansion (1,200+ films) it is a genuine sample of the
# most recent films rather than the entire catalog.
MOSAIC_LIMIT = 120

# ?sort= values accepted by movie_list, mapped to an order_by expression.
# Nulls always sort last so movies missing a field don't lead the list.
#
# "rating" points at imdb_rating — the single filtered annotation
# (Max("movierating__rating", filter=Q(movierating__source="imdb"))) that
# every rating-bearing view below also uses for card display, so sorting and
# display can never disagree (Task 68). fact_movie_rating is one row per
# (movie, source) — no genre fan-out — so Max() here is a defensive plain
# lookup, not a fan-out collapse the way the equivalent moviemetrics
# annotation used to be.
MOVIE_SORTS = {
    "release": F("release_date").desc(nulls_last=True),
    "rating": F("imdb_rating").desc(nulls_last=True),
    "revenue": F("revenue").desc(nulls_last=True),
    "title": F("title").asc(),
}


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


def home(request):
    """Landing page: the catalog as a contact sheet, plus warehouse-wide stats."""
    top_rated = (
        Movie.objects.using("warehouse")
        .annotate(imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb")))
        .order_by(F("imdb_rating").desc(nulls_last=True))[:12]
    )
    newest = (
        Movie.objects.using("warehouse")
        .annotate(imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb")))
        .order_by(F("release_date").desc(nulls_last=True))[:12]
    )
    # Task 90: TV's "what's new" analogue orders by *last* air date, not
    # first — a long-running show that just aired a new episode is genuinely
    # "recently aired" in a way a canceled show that merely premiered the
    # same year is not. A movie has one date that means both things at once;
    # a show doesn't, so this can't just reuse newest's ordering.
    recently_aired = (
        Series.objects.using("warehouse")
        .annotate(imdb_rating=Max("seriesrating__rating", filter=Q(seriesrating__source="imdb")))
        .order_by(F("last_air_date").desc(nulls_last=True))[:12]
    )
    for show in recently_aired:
        show.year_span = _series_year_span(show)

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
        .order_by(F("release_date").desc(nulls_last=True))
        .values_list("slug", "poster_path")[:100]
    ]
    series_tiles = [
        {"kind": "series", "slug": slug, "poster_path": poster_path}
        for slug, poster_path in
        Series.objects.using("warehouse")
        .filter(poster_path__isnull=False)
        .order_by(F("first_air_date").desc(nulls_last=True))
        .values_list("slug", "poster_path")[:20]
    ]
    mosaic = _interleave_mosaic(movie_tiles, series_tiles, ratio=5)

    context = {
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
        "top_rated": top_rated,
        "newest": newest,
        "recently_aired": recently_aired,
        "mosaic": mosaic,
    }
    return render(request, "movies/home.html", context)


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


def movie_list(request):
    """Browsable movie catalog: poster grid + title search + sort + pagination.

    Genre is a facet of a film, filtered here rather than given its own
    /genres/ index page (Task 70; a genre-browsing UI existed briefly and was
    removed on 2026-08-14 — see the status block in CLAUDE.md). Genre
    membership lives only in fact_movie_metrics — there is no bridge table
    for it — so the URL carries a slugified genre *name*
    (?genre=science-fiction), never the raw genre_id: dim_genre has no slug
    column of its own, and a surrogate key has no business in a user-facing
    URL.
    """
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "release")
    if sort not in MOVIE_SORTS:
        sort = "release"

    # Only genres that actually have a film in the catalog are offered as a
    # choice — Documentary currently has 0, and a choice that can never
    # return anything is worse than not offering it. distinct=True matters
    # for the same reason the .distinct() below does: fact_movie_metrics'
    # PK is (movie_id, date_id, genre_id), and a film whose release date
    # moved between ingestions holds two date_id rows per genre, which
    # would otherwise double-count its film_count.
    genre_rows = (
        Genre.objects.using("warehouse")
        .annotate(film_count=Count("moviemetrics__movie", distinct=True))
        .filter(film_count__gt=0)
        .order_by("genre_name")
        .values_list("genre_id", "genre_name")
    )
    genre_slugs = {slugify(name): genre_id for genre_id, name in genre_rows}
    genre_choices = [(slugify(name), name) for _, name in genre_rows]

    genre = request.GET.get("genre", "").strip()
    if genre not in genre_slugs:
        # Silent fallback to unfiltered on an unknown slug, the same posture
        # sort/gender/known_for already take elsewhere in this file — not a
        # 404, and not an empty grid.
        genre = ""

    movies = Movie.objects.using("warehouse").all()
    if q:
        movies = movies.filter(title__icontains=q)
    if genre:
        # fact_movie_metrics' PK is (movie_id, date_id, genre_id) and
        # date_id is derived from the *release* date — so a film whose
        # release date moved between ingestions keeps both rows. Two films
        # in the catalog do (Avatar Aang: The Last Airbender, The Odyssey —
        # 7 duplicate (movie, genre) pairs), and without this they would
        # render twice in the grid and be counted twice by the paginator.
        movies = movies.filter(moviemetrics__genre_id=genre_slugs[genre]).distinct()
    # Annotated unconditionally, not only when sort == "rating" — the cards
    # display this figure too, so it must exist whether or not the list is
    # being sorted by it (Task 68). MOVIE_SORTS["rating"] points at the same
    # annotation, so sorting and display can never disagree.
    movies = movies.annotate(
        imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb"))
    )
    movies = movies.order_by(MOVIE_SORTS[sort])

    page_obj = Paginator(movies, MOVIES_PER_PAGE).get_page(request.GET.get("page"))

    # Built here, not in the template, so the shared _pager.html partial
    # doesn't need to know which params any given page carries — see
    # _pager.html's docstring.
    context = {
        "page_obj": page_obj, "q": q, "sort": sort,
        "genre": genre,
        "genre_choices": genre_choices,
        "base_query": urlencode({"q": q, "sort": sort, "genre": genre}),
    }
    # See _person_list()'s identical branch: static/js/theoria.js's
    # initLiveFilter() re-requests this URL with this header on every filter
    # change and only wants the results fragment back, not the page around it.
    if _is_ajax(request):
        return render(request, "movies/_movie_results.html", context)
    return render(request, "movies/movie_list.html", context)


# ?sort= values accepted by series_list, mapped to an order_by expression.
# No "revenue" segment — TV has no money measures (21_series_ratings.sql's
# deliberate omission of fact_series_metrics carries through here too).
SERIES_SORTS = {
    "first_air": F("first_air_date").desc(nulls_last=True),
    "rating": F("imdb_rating").desc(nulls_last=True),
    "name": F("name").asc(),
}


def series_list(request):
    """Browsable TV catalog: the /movies/ shape (search, sort, genre filter,
    pagination), with two simplifications forced by the schema being a
    genuine star rather than movie's fact-embedded genre:

    * the genre filter is a plain join through bridge_series_genre, with no
      .distinct() dedupe guard — each (series, genre) pair is already unique
      in the bridge, unlike fact_movie_metrics' (movie, date, genre) grain;
    * the genre choice list is a plain count on that same bridge.
    """
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "first_air")
    if sort not in SERIES_SORTS:
        sort = "first_air"

    genre_rows = (
        Genre.objects.using("warehouse")
        .annotate(series_count=Count("series_genres"))
        .filter(series_count__gt=0)
        .order_by("genre_name")
        .values_list("genre_id", "genre_name")
    )
    genre_slugs = {slugify(name): genre_id for genre_id, name in genre_rows}
    genre_choices = [(slugify(name), name) for _, name in genre_rows]

    genre = request.GET.get("genre", "").strip()
    if genre not in genre_slugs:
        genre = ""

    series = Series.objects.using("warehouse").all()
    if q:
        series = series.filter(name__icontains=q)
    if genre:
        series = series.filter(series_genres__genre_id=genre_slugs[genre])
    # Annotated unconditionally, same reasoning as movie_list()'s imdb_rating:
    # the cards display this figure too, so it must exist whichever sort is
    # active.
    series = series.annotate(
        imdb_rating=Max("seriesrating__rating", filter=Q(seriesrating__source="imdb"))
    )
    series = series.order_by(SERIES_SORTS[sort])

    page_obj = Paginator(series, MOVIES_PER_PAGE).get_page(request.GET.get("page"))
    for row in page_obj:
        row.year_span = _series_year_span(row)

    context = {
        "page_obj": page_obj, "q": q, "sort": sort,
        "genre": genre,
        "genre_choices": genre_choices,
        "base_query": urlencode({"q": q, "sort": sort, "genre": genre}),
    }
    if _is_ajax(request):
        return render(request, "movies/_series_results.html", context)
    return render(request, "movies/series_list.html", context)


def _series_year_span(series):
    """A show's first–last air year as one card sub-line figure, e.g. "2011",
    "2011–2019" (ended), or "2011–" (still airing — no last_air_date yet).
    Mirrors _career_period()'s single-year collapse, minus the "Active"
    wording — a card sub-line has no room for a word, only figures.
    """
    if not series.first_air_date:
        return None
    start = series.first_air_date.year
    if not series.last_air_date:
        return f"{start}–"
    end = series.last_air_date.year
    return str(start) if start == end else f"{start}–{end}"


# ?sort= values accepted by person_list, mapped to an order_by expression.
PERSON_SORTS = {
    "popularity": F("popularity").desc(nulls_last=True),
    "name": F("name").asc(),
}

# TMDB's gender codes. 0 ("not specified") is deliberately not a filter option
# below — it isn't a fact about the person, it's TMDB having no answer, and
# offering it as a choice would imply otherwise.
GENDER_LABELS = {"1": "Female", "2": "Male", "3": "Non-binary"}

# Filter options for "known for" — the person's own TMDB craft, independent of
# which credits they hold in *this* catalog (that's what the Acting/Directing
# scope switch already does). Reuses DEPARTMENT_ORDER's names rather than a
# fresh DISTINCT query every request; "Creator" (25 people, a TMDB rarity
# outside this list) is the one department it doesn't offer as a choice.


def _person_list(request, people, list_title, scope):
    """Shared list view for every people index: search, filter, sort, pagination.

    Takes a queryset rather than a model, because the three indexes now differ
    by which credits a person holds, not by which table they live in. Every
    person page lives at /people/<slug>/, so there is no per-list URL name.
    """
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "popularity")
    if sort not in PERSON_SORTS:
        sort = "popularity"
    gender = request.GET.get("gender", "")
    if gender not in GENDER_LABELS:
        gender = ""
    known_for = request.GET.get("known_for", "")
    if known_for not in DEPARTMENT_ORDER:
        known_for = ""

    if q:
        people = people.filter(name__icontains=q)
    if gender:
        people = people.filter(gender=int(gender))
    if known_for:
        people = people.filter(known_for_department=known_for)
    people = people.order_by(PERSON_SORTS[sort])

    page_obj = Paginator(people, PEOPLE_PER_PAGE).get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "q": q,
        "sort": sort,
        "gender": gender,
        "known_for": known_for,
        "gender_choices": GENDER_LABELS,
        "known_for_choices": DEPARTMENT_ORDER,
        "list_title": list_title,
        "scope": scope,
        "detail_url_name": "movies:person_detail",
        "base_query": urlencode(
            {"q": q, "sort": sort, "gender": gender, "known_for": known_for}
        ),
    }
    # static/js/theoria.js's initLiveFilter() re-requests this same URL with
    # this header on every filter change, and only wants the results back —
    # not the page around them. Without JS, this header is never sent and the
    # form's plain GET submit renders the full page as always.
    if _is_ajax(request):
        return render(request, "movies/_person_results.html", context)
    return render(request, "movies/person_list.html", context)


def _person_queryset(department=None):
    """dim_person, optionally narrowed to people with a credit in one department.

    "Actors" and "Directors" are no longer separate tables — they're the people
    holding an Acting or Directing credit, which is a question about
    fact_credit, not about which dimension someone landed in.
    """
    people = Person.objects.using("warehouse")
    if department:
        people = people.filter(credits__department=department).distinct()
    return people


def person_list(request):
    return _person_list(request, _person_queryset(), "People", "all")


def actor_list(request):
    return _person_list(request, _person_queryset("Acting"), "Acting", "acting")


def director_list(request):
    return _person_list(request, _person_queryset("Directing"), "Directing", "directing")


@login_required
def movie_detail(request, movie_slug):
    """Single movie: core facts, genres, directors, and cast."""
    movie = get_object_or_404(
        Movie.objects.using("warehouse"), slug=movie_slug
    )
    movie_id = movie.movie_id

    genres = (
        Genre.objects.using("warehouse")
        .filter(moviemetrics__movie_id=movie_id)
        .distinct()
    )

    # One query for every credit on the film, cast and crew alike, then split
    # in Python. Materialized with list() because both the cast Paginator and
    # _merge_crew() below need a concrete sequence — a lazy queryset would
    # otherwise re-hit the database for each. Before fact_credit this page
    # could only show the cast and the director — the ~150 other people who
    # made the film had no warehouse row at all.
    credits = list(
        Credit.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("person")
        .order_by(F("ordering").asc(nulls_last=True), "job")
    )

    # Cast is already one row per person — fact_credit's job is the literal
    # "Actor" for every Acting row, so there's nothing to merge here. Its
    # problem is volume alone: the median film has 47, the worst 210. Every
    # one is sent to the page and paged client-side (static/js/theoria.js) —
    # a server round-trip per ten actors was the smoothness cost the reader
    # actually felt, and the whole list is already in memory here anyway.
    cast = [c for c in credits if c.department == "Acting"]

    # Crew is the opposite problem: duplication, not volume alone. A director
    # who also wrote and produced is three fact_credit rows, rendered before
    # this fix as three names in three department sections. _merge_crew()
    # collapses each person to one row under their most senior department.
    #
    # Grouped by department and sent in full, paged client-side the same way.
    # There is no billed-crew subset and no expand toggle any more: paging ten
    # at a time makes the first page short whatever it contains, so a second
    # definition of "important crew" stopped earning its keep.
    crew_credits = [c for c in credits if c.department != "Acting"]
    merged_crew = _merge_crew(crew_credits)

    by_department = {}
    for m in merged_crew:
        by_department.setdefault(m["department"], []).append(m)
    crew = [
        {
            "name": name,
            "people": sorted(rows, key=lambda m: m["person"].name),
            "count": len(rows),
        }
        for name, rows in sorted(
            by_department.items(), key=lambda kv: _department_rank(kv[0])
        )
    ]

    # Unchanged: the "Directed by" record line reads raw credits, not the
    # merged crew list — it only ever needs the Director job, never the full
    # job_display string the merged rows carry.
    directors = [c.person for c in credits if c.job == "Director"]

    studios = [
        mc.company for mc in
        MovieCompany.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("company")
        .order_by("company__name")
    ]

    country_rows = list(
        MovieCountry.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("country")
    )
    countries = _country_provenance(country_rows)

    language_rows = list(
        MovieLanguage.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("language")
    )
    languages = _reconcile_languages(movie.original_language, language_rows)

    # fact_movie_rating (Task 66-68) is one row per (movie, source) — no
    # genre fan-out — so this is a plain lookup, replacing the old
    # fact_movie_metrics read that needed .values(...).distinct() to
    # collapse a genre-repeated rating before taking one row.
    movie_rating = (
        MovieRating.objects.using("warehouse")
        .filter(movie_id=movie_id, source="imdb")
        .first()
    )

    # dim_movie_video (Task 74-75): one query for every YouTube video on the
    # film (~16 rows), then one trailer is chosen here in Python. Which trailer
    # to *show* is a rendering decision, not a stored one (the Task 56
    # judgment) — freezing it into a column would mean a re-load to change it.
    # The `site='YouTube'` filter is deliberate: the ~3 Vimeo rows in the
    # catalogue are all unofficial and pre-2020, not worth a second embed
    # path; the column stays so a later task can revisit that. Ordered
    # published_at DESC explicitly — nothing in the warehouse preserves TMDB's
    # array order, so "it came back newest-first" is not a guarantee to lean
    # on. (Clips beyond the trailer were removed by user request 2026-09-07 —
    # the other video rows are still stored, just not rendered.)
    videos = list(
        MovieVideo.objects.using("warehouse")
        .filter(movie_id=movie_id, site="YouTube")
        .order_by(F("published_at").desc(nulls_last=True), "video_id")
    )
    trailer = _pick_trailer(videos)

    context = {
        "movie": movie,
        "genres": genres,
        "cast": cast,
        "cast_count": len(cast),
        "crew": crew,
        "crew_person_count": len(merged_crew),
        "credit_count": len(credits),
        "directors": directors,
        "studios": studios,
        "countries": countries,
        "languages": languages,
        "movie_rating": movie_rating,
        "trailer": trailer,
        "collection_flags": collection_flags(
            request.user, CollectionItem.MOVIE, movie_id
        ),
    }
    return render(request, "movies/movie_detail.html", context)


def _pick_trailer(videos):
    """Choose one video to feature, from a list already ordered newest-first.

    A 3-step ladder, each step taking the first (newest) match:
        1. an official YouTube Trailer
        2. any YouTube Trailer
        3. any YouTube Teaser
    Measured to resolve for 148/150 films — the two misses are the films with
    no videos at all. Returns None when nothing matches (a trailer block that
    renders nothing rather than an empty frame — the Task 56/68 rule).
    """
    for match in (
        lambda v: v.type == "Trailer" and v.official,
        lambda v: v.type == "Trailer",
        lambda v: v.type == "Teaser",
    ):
        for video in videos:
            if match(video):
                return video
    return None


def _country_provenance(country_rows):
    """Group a film's bridge_movie_country rows into a display-ready shape.

    Origin and production are two simultaneously-true claims about a film's
    country (Task 57) that agree on ~77% of films. Showing both as separately
    labeled rows only when they actually disagree keeps the common case to
    one row instead of two identical lists — the same judgment Task 56 made
    for original_title. Returns a dict with all three keys always present so
    the template doesn't have to branch on which shape it got.
    """
    origin = sorted({r.country.name for r in country_rows if r.relation == "origin"})
    production = sorted(
        {r.country.name for r in country_rows if r.relation == "production"}
    )
    if origin and production and origin != production:
        return {"origin": origin, "production": production, "countries": []}
    return {"origin": [], "production": [], "countries": origin or production}


def _reconcile_languages(original_language, language_rows):
    """One merged, deduplicated language list, anchored on original_language.

    dim_movie/dim_series.original_language and bridge_{movie,series}_language
    (Task 57/61, extended to TV by Task 79) are two facts about the same thing
    rather than two different things, so this reconciles them into a single
    ordered list instead of shipping both side by side unexplained (Task 62):
    the original language leads if it resolves to a known dim_language row,
    followed by any other language the bridge records for the title, each
    name listed once. Takes the raw language code rather than the film/show
    itself — the two bridges carry the same shape, so one function serves
    both movie_detail() and series_detail().
    """
    names = []
    seen = set()
    original = next(
        (r.language for r in language_rows if r.language_id == original_language),
        None,
    )
    if original:
        names.append(original.name)
        seen.add(original.language_code)
    for r in sorted(language_rows, key=lambda r: r.language.name):
        if r.language_id not in seen:
            names.append(r.language.name)
            seen.add(r.language_id)
    return names


@login_required
def series_detail(request, series_slug):
    """One show: mirrors movie_detail() — one query for every credit, split
    into cast/crew and merged with the same _merge_crew()/_department_rank()
    movie_detail() uses (Task 87), plus the TV-only record fields the film
    page has no analogue for (first/last aired, status, seasons/episodes,
    networks) in place of the film-only ones it drops (budget, revenue,
    runtime), every episode grouped by season with its own IMDb rating
    (Task 88), and a trailer via dim_series_video (Task 90) reusing
    movie_detail()'s _pick_trailer() unchanged.
    """
    series = get_object_or_404(Series.objects.using("warehouse"), slug=series_slug)
    series_id = series.series_id

    genres = (
        Genre.objects.using("warehouse")
        .filter(series_genres__series_id=series_id)
        .order_by("genre_name")
    )

    # One query for every credit on the show, cast and crew (and, since Task
    # 87, "Creator") alike — same shape as movie_detail()'s single Credit
    # query, just against fact_series_credit. Measured mean ~690 credits/show
    # against film's ~200, so materializing with list() before the client
    # pager and _merge_crew() below matters even more here.
    credits = list(
        SeriesCredit.objects.using("warehouse")
        .filter(series_id=series_id)
        .select_related("person")
        .order_by(F("ordering").asc(nulls_last=True), "job")
    )

    cast = [c for c in credits if c.department == "Acting"]

    # _merge_crew()/_department_rank() are reused unchanged from movie_detail
    # (Task 87 step 1) — they key on person and department, never on movie,
    # so nothing about them needed to change for a series-keyed credit list.
    # "Creation"/"Creator" (Task 87's created_by flatten) isn't in
    # DEPARTMENT_ORDER, so it sorts after every known department here,
    # exactly like the "Actors" TMDB anomaly _department_rank's docstring
    # already accounts for.
    crew_credits = [c for c in credits if c.department != "Acting"]
    merged_crew = _merge_crew(crew_credits)

    by_department = {}
    for m in merged_crew:
        by_department.setdefault(m["department"], []).append(m)
    crew = [
        {
            "name": name,
            "people": sorted(rows, key=lambda m: m["person"].name),
            "count": len(rows),
        }
        for name, rows in sorted(
            by_department.items(), key=lambda kv: _department_rank(kv[0])
        )
    ]

    # "Created by" replaces "Directed by" — a show has no director credit.
    # Task 87 decision: created_by is stored as a department="Creation"/
    # job="Creator" row in fact_series_credit (see
    # etl/silver/transform_series_credits.py's module docstring for why),
    # so this reads exactly like movie_detail()'s `directors` line.
    creators = [c.person for c in credits if c.job == "Creator"]

    networks = [
        sn.network for sn in
        SeriesNetwork.objects.using("warehouse")
        .filter(series_id=series_id)
        .select_related("network")
        .order_by("network__name")
    ]

    studios = [
        sc.company for sc in
        SeriesCompany.objects.using("warehouse")
        .filter(series_id=series_id)
        .select_related("company")
        .order_by("company__name")
    ]

    country_rows = list(
        SeriesCountry.objects.using("warehouse")
        .filter(series_id=series_id)
        .select_related("country")
    )
    countries = _country_provenance(country_rows)

    language_rows = list(
        SeriesLanguage.objects.using("warehouse")
        .filter(series_id=series_id)
        .select_related("language")
    )
    languages = _reconcile_languages(series.original_language, language_rows)

    series_rating = (
        SeriesRating.objects.using("warehouse")
        .filter(series_id=series_id, source="imdb")
        .first()
    )

    # dim_series_video (Task 90): the movie page's trailer pipeline, reused
    # unchanged — same query shape as movie_detail()'s, same _pick_trailer()
    # ladder, same _video_embed.html partial. No clips section here either
    # (that feature was removed from the film page by user request 2026-09-07,
    # so it was never built for TV in the first place).
    videos = list(
        SeriesVideo.objects.using("warehouse")
        .filter(series_id=series_id, site="YouTube")
        .order_by(F("published_at").desc(nulls_last=True), "video_id")
    )
    trailer = _pick_trailer(videos)

    seasons = sorted(
        Season.objects.using("warehouse").filter(series_id=series_id),
        # "Specials" (season_number 0) reads last, not first, matching the
        # convention every streaming app already uses — TMDB's season stub
        # numbers it 0 because that's its position in the API array, not
        # because a reader wants to watch it before Season 1.
        key=lambda s: (s.season_number == 0, s.season_number),
    )

    # One query for every episode on the show, joined to its IMDb rating via
    # the fact_episode_rating reverse relation (Task 88 step 1) — the same
    # "one query, not one per season" posture as the credits query above, and
    # the same filtered-Max annotation Task 68 used for fact_movie_rating, so
    # sorting and display can never disagree here either.
    episodes = (
        Episode.objects.using("warehouse")
        .filter(series_id=series_id)
        .annotate(
            imdb_rating=Max("episoderating__rating", filter=Q(episoderating__source="imdb")),
            imdb_vote_count=Max(
                "episoderating__vote_count", filter=Q(episoderating__source="imdb")
            ),
        )
        .order_by("episode_number")
    )
    episodes_by_season = {}
    for episode in episodes:
        episodes_by_season.setdefault(episode.season_number, []).append(episode)

    # A season with no entry here isn't an error — Task 82's TV_SEASONS_MAX_NEW
    # cap means dim_season already lists every season TMDB knows about while
    # dim_episode only covers the 300/734 shows backfilled so far (Task 85).
    # _episode_table.html renders that as a quiet "not catalogued yet" state,
    # never an empty table.
    seasons = [
        {"season": season, "episodes": episodes_by_season.get(season.season_number, [])}
        for season in seasons
    ]

    context = {
        "series": series,
        "genres": genres,
        "cast": cast,
        "cast_count": len(cast),
        "crew": crew,
        "crew_person_count": len(merged_crew),
        "credit_count": len(credits),
        "creators": creators,
        "networks": networks,
        "studios": studios,
        "countries": countries,
        "languages": languages,
        "series_rating": series_rating,
        "seasons": seasons,
        "year_span": _series_year_span(series),
        "trailer": trailer,
        "collection_flags": collection_flags(
            request.user, CollectionItem.SERIES, series.series_id
        ),
    }
    return render(request, "movies/series_detail.html", context)


# ?sort= values accepted by studio_list, mapped to an order_by expression.
STUDIO_SORTS = {
    "film_count": F("film_count").desc(nulls_last=True),
    "revenue": F("total_revenue").desc(nulls_last=True),
    "name": F("name").asc(),
}


def studio_list(request):
    """Browsable studio index: logo grid + name search + sort + pagination —
    the same shape as /people/ (Task 62 redesign), not the ranked table it
    used to be. A studio is a browsable entity with its own identity and
    artwork, same as a person; a table of numbers was the wrong instinct
    even though .annotate(Count).filter(...__gt=0) (a HAVING clause) still
    does the ranking underneath.
    """
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "film_count")
    if sort not in STUDIO_SORTS:
        sort = "film_count"

    # total_revenue sums straight off dim_movie through the bridge — one bridge
    # row per (movie, studio), so no genre-fanout guard is needed here (same as
    # studio_detail()'s revenue stat).
    companies = (
        Company.objects.using("warehouse")
        .annotate(
            film_count=Count("movie_companies"),
            total_revenue=Sum("movie_companies__movie__revenue"),
        )
        .filter(film_count__gt=0)
    )
    if q:
        companies = companies.filter(name__icontains=q)
    companies = companies.order_by(STUDIO_SORTS[sort], "name")

    page_obj = Paginator(companies, STUDIOS_PER_PAGE).get_page(request.GET.get("page"))
    context = {
        "page_obj": page_obj, "q": q, "sort": sort,
        "base_query": urlencode({"q": q, "sort": sort}),
    }
    # See _person_list()'s identical branch: static/js/theoria.js's
    # initLiveFilter() re-requests this URL with this header on every filter
    # change and only wants the results fragment back, not the page around it.
    if _is_ajax(request):
        return render(request, "movies/_studio_results.html", context)
    return render(request, "movies/studio_list.html", context)


@login_required
def studio_detail(request, company_slug):
    """One studio: header stats over its whole output, plus a searchable,
    sortable, paginated filmography — the same movie-browsing toolbar as
    /movies/, scoped to this studio's films (Task 62 redesign).
    """
    company = get_object_or_404(Company.objects.using("warehouse"), slug=company_slug)

    # Task 65: resolve the parent company for a link, if it has one *and* that
    # parent is itself in the catalog. A holding-company parent (Warner Bros.
    # Entertainment, Viacom International) is often never directly credited on
    # a film, so it has no dim_company row — the template then falls back to
    # company.parent_company_name as plain text. One extra query, and only on
    # a single studio page, never the list.
    parent_company = None
    if company.parent_company_id:
        parent_company = (
            Company.objects.using("warehouse")
            .filter(company_id=company.parent_company_id)
            .first()
        )

    movie_ids = list(
        MovieCompany.objects.using("warehouse")
        .filter(company_id=company.company_id)
        .values_list("movie_id", flat=True)
    )
    all_movies = Movie.objects.using("warehouse").filter(movie_id__in=movie_ids)

    # Revenue sums straight off dim_movie (one row per film). Rating used to
    # need a fact_movie_metrics genre-fanout guard (.values().distinct()
    # before averaging) — fact_movie_rating is one row per (movie, source),
    # so that guard is unnecessary here and deliberately not ported
    # (Task 68): a plain .filter(source="imdb").aggregate(Avg(...)) is
    # already correct at this grain. Computed once, over the *whole*
    # filmography — the header stats describe this studio's entire output
    # and must not shift as the grid below is filtered, the same way a
    # person page's stat row doesn't move when someone pages through their
    # filmography.
    stats = all_movies.aggregate(film_count=Count("movie_id"), total_revenue=Sum("revenue"))
    avg_rating = (
        MovieRating.objects.using("warehouse")
        .filter(movie_id__in=movie_ids, source="imdb")
        .aggregate(avg_rating=Avg("rating"))["avg_rating"]
    )
    span = all_movies.aggregate(start=Min("release_date"), end=Max("release_date"))

    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "release")
    if sort not in MOVIE_SORTS:
        sort = "release"

    # Annotated unconditionally (not only when sort == "rating"), same as
    # movie_list() — the grid below always displays this figure, so it must
    # always be there to display, whichever way the list is sorted.
    movies = all_movies.annotate(
        imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb"))
    )
    if q:
        movies = movies.filter(title__icontains=q)
    movies = movies.order_by(MOVIE_SORTS[sort])

    page_obj = Paginator(movies, MOVIES_PER_PAGE).get_page(request.GET.get("page"))

    context = {
        "company": company,
        "parent_company": parent_company,
        "page_obj": page_obj,
        "q": q,
        "sort": sort,
        "base_query": urlencode({"q": q, "sort": sort}),
        "film_count": stats["film_count"],
        "total_revenue": stats["total_revenue"],
        "avg_rating": avg_rating,
        "period": _career_period(span["start"], span["end"]),
    }
    # Same live-filter contract as movie_list()/_person_list(): the header
    # stats above never need to be part of the swap, so only the grid+pager
    # fragment is returned for an AJAX refetch.
    if _is_ajax(request):
        return render(request, "movies/_studio_movies_results.html", context)
    return render(request, "movies/studio_detail.html", context)


def _career_period(start, end):
    """Render a studio's release span as a stat, e.g. "1997–2019" or "2019–Active".

    A closed range naming the same year twice (e.g. "2026–2026" for a single
    film released this year) reads as a typo, not a fact, so a one-year span
    collapses to the year alone. A range that ends in the current year isn't
    really "closed" — the latest release is one that just came out, not one
    that ended the studio.

    The person page does not use this — a person's activity is just
    "Active"/"Retired", keyed off whether they have a recorded deathday.
    """
    if not start:
        return "—"
    current_year = date.today().year
    if end.year >= current_year:
        return "Active" if start.year == end.year else f"{start.year}–Active"
    return str(start.year) if start.year == end.year else f"{start.year}–{end.year}"


# Acting first, then the crafts in roughly the order a viewer thinks about
# them; anything TMDB reports outside this list is appended alphabetically.
DEPARTMENT_ORDER = [
    "Acting", "Directing", "Writing", "Production", "Camera", "Editing",
    "Sound", "Art", "Costume & Make-Up", "Visual Effects", "Lighting", "Crew",
]


def _department_rank(name):
    """Sort key for a department name against DEPARTMENT_ORDER.

    Extracted from the identical inline lambda that used to live separately in
    both movie_detail and person_detail. Anything TMDB reports outside the
    list (e.g. the "Actors" anomaly on movie 1372) sorts after every known
    one, alphabetically among themselves.
    """
    return (
        DEPARTMENT_ORDER.index(name) if name in DEPARTMENT_ORDER
        else len(DEPARTMENT_ORDER),
        name,
    )


def _merge_crew(credits):
    """Collapse a person's several crew credits on one film into one record.

    fact_credit's PK is (movie_id, person_id, department, job), so a director
    who also wrote and produced is three separate rows — rendered before this
    as three names in three department sections. This groups by person_id and
    files each person under their single most senior department (min by
    _department_rank), while listing every job they hold in the same order,
    so "Director / Screenplay / Story / Producer" leads with the job that
    matters instead of alphabetizing it away.

    Cast is untouched by this — fact_credit's job is the literal "Actor" for
    every one of the 62,713 Acting rows, so cast is already one row per
    person; merging is purely a crew-department concern.
    """
    by_person = {}
    for credit in credits:
        by_person.setdefault(credit.person_id, []).append(credit)

    merged = []
    for person_id, rows in by_person.items():
        rows_sorted = sorted(rows, key=lambda c: _department_rank(c.department))
        jobs = [c.job for c in rows_sorted]
        merged.append({
            "person": rows_sorted[0].person,
            "department": rows_sorted[0].department,
            "jobs": jobs,
            "job_display": " / ".join(jobs),
        })
    return merged


# ?sort= values accepted by person_detail's filmography toolbar: each name
# maps to a sort direction only (descending?). The same four segments as the
# /movies/ and studio-page toolbars (MOVIE_SORTS), applied in Python rather
# than the ORM — a person's filmography is a merged list of dicts, not a
# queryset, so it can't be reordered with .order_by(). Task 89 mixed movies
# and shows into one filmography, so there is no longer one fixed attribute
# name per sort (a movie's release_date/revenue have no analogue on a show);
# _filmography_sort_key() below resolves the right attribute per row's kind.
# Revenue is kept as a segment rather than hidden once a show is present —
# TV has no revenue measure (see 21_series_ratings.sql's fact_series_metrics
# omission), so every show's revenue reads as a missing value and sorts to
# the end under this sort, exactly like a movie with no revenue figure
# already does. That's an existing, understood behavior on this toolbar, not
# a new one a Revenue sort with shows in the mix would silently introduce.
FILMOGRAPHY_SORTS = {
    "release": True,
    "rating": True,
    "revenue": True,
    "title": False,
}


def _filmography_title(title_obj):
    """A title's display name, read generically since Task 89 mixed a
    movie's `.title` and a show's `.name` into one filmography list."""
    return getattr(title_obj, "title", None) or getattr(title_obj, "name", None) or ""


def _filmography_sort_key(row, sort):
    """The value _sorted_filmography() orders one row on. `title_obj` is
    either a Movie or a Series (Task 89) — release reads release_date or
    first_air_date, rating reads the imdb_rating annotation person_detail()
    attaches to either kind, and revenue reads Movie.revenue (None, via
    getattr's default, for every show — see FILMOGRAPHY_SORTS' comment)."""
    obj = row["title_obj"]
    if sort == "title":
        return _filmography_title(obj).lower()
    if sort == "release":
        return getattr(obj, "release_date", None) or getattr(obj, "first_air_date", None)
    if sort == "rating":
        return getattr(obj, "imdb_rating", None)
    return getattr(obj, "revenue", None)  # sort == "revenue"


def _sorted_filmography(rows, sort):
    """Order merged filmography rows, nulls always last.

    Mirrors MOVIE_SORTS' nulls_last=True: a row missing the sort field (no
    IMDb rating yet, no revenue figure — every show, for revenue) sorts
    after every row that has one, whichever direction the sort runs, rather
    than a null leading a descending list.
    """
    descending = FILMOGRAPHY_SORTS[sort]
    if sort == "title":
        return sorted(rows, key=lambda r: _filmography_sort_key(r, sort))
    present = [r for r in rows if _filmography_sort_key(r, sort) is not None]
    missing = [r for r in rows if _filmography_sort_key(r, sort) is None]
    present.sort(key=lambda r: _filmography_sort_key(r, sort), reverse=descending)
    return present + missing


def _merge_person_credits(credits):
    """Collapse a person's several credits on one title into one filmography
    row. `credits` can mix fact_credit and fact_series_credit rows (Task 89)
    — a Credit carries `.movie_id`/`.movie`, a SeriesCredit carries
    `.series_id`/`.series`, and nothing else distinguishes them structurally,
    so `kind` is read off which one a row has.

    Same shape of problem as _merge_crew, keyed by (kind, id) instead of
    person: an actor who also directed or wrote the same title would
    otherwise appear as duplicate entries in separate department sections
    (Acting, Directing, ...). One row per title, with every job on it joined
    in department order, so a director who also wrote the script reads
    "Director / Screenplay" once, under one poster, rather than twice under
    two.
    """
    by_key = {}
    for credit in credits:
        if hasattr(credit, "movie_id"):
            key = ("movie", credit.movie_id)
        else:
            key = ("series", credit.series_id)
        by_key.setdefault(key, []).append(credit)

    merged = []
    for (kind, _id), rows in by_key.items():
        rows_sorted = sorted(rows, key=lambda c: _department_rank(c.department))
        labels = [
            c.character_name if c.department == "Acting" and c.character_name else c.job
            for c in rows_sorted
        ]
        title_obj = rows_sorted[0].movie if kind == "movie" else rows_sorted[0].series
        merged.append({
            "kind": kind,
            "title_obj": title_obj,
            "job_display": " / ".join(labels),
        })
    return merged


def _redirect_to_person(slug):
    """301 a legacy /actors/<slug>/ or /directors/<slug>/ URL to /people/<slug>/.

    Task 51 resolved this through dim_actor/dim_director to handle the 381 slugs
    that changed when the two namespaces merged. Those tables are gone as of
    Task 53, so the mapping is gone with them: a legacy URL now resolves only if
    its slug still names the same person in dim_person, which is true of 44,178
    of the 44,554 actor slugs. The remaining 376 are unrecoverable and 404 —
    the honest outcome, rather than carrying two dead dimension tables purely as
    a redirect map.
    """
    person = get_object_or_404(Person.objects.using("warehouse"), slug=slug)
    return redirect("movies:person_detail", person_slug=person.slug, permanent=True)


@login_required
def actor_detail(request, actor_slug):
    return _redirect_to_person(actor_slug)


@login_required
def director_detail(request, director_slug):
    return _redirect_to_person(director_slug)


@login_required
def person_detail(request, person_slug):
    """One person, every title they worked on — film or show (Task 89 folded
    fact_series_credit into the same filmography fact_credit already fed),
    and what they did there."""
    person = get_object_or_404(Person.objects.using("warehouse"), slug=person_slug)
    person_id = person.person_id

    # One query for every film credit, joined to its film. Merging happens in
    # Python below, since a GROUP BY can't return the rows themselves.
    credits = list(
        Credit.objects.using("warehouse")
        .filter(person_id=person_id)
        .select_related("movie")
        .order_by(F("movie__release_date").desc(nulls_last=True))
    )

    # A second, symmetric query against fact_series_credit (Task 89) — always
    # issued, even for a person with no TV work, so there is one code path
    # rather than a branch on "does this person have any shows"; an empty
    # result here changes nothing about what renders below.
    series_credits = list(
        SeriesCredit.objects.using("warehouse")
        .filter(person_id=person_id)
        .select_related("series")
        .order_by(F("series__first_air_date").desc(nulls_last=True))
    )

    filmography = _merge_person_credits(credits + series_credits)

    movie_ids = {c.movie_id for c in credits}
    series_ids = {c.series_id for c in series_credits}

    # fact_movie_rating/fact_series_rating are each one row per (title,
    # source) — no genre fan-out — so, unlike the old fact_movie_metrics read
    # this replaces, there's no .values(...).distinct() dedupe guard to port
    # here (Task 68). These two dicts also give every poster in the grid its
    # own IMDb figure (below), without turning the grid into one query per
    # card — a constant number of queries regardless of how many titles this
    # person has, whichever mix of films and shows they are.
    movie_ratings = dict(
        MovieRating.objects.using("warehouse")
        .filter(movie_id__in=movie_ids, source="imdb")
        .values_list("movie_id", "rating")
    )
    series_ratings = dict(
        SeriesRating.objects.using("warehouse")
        .filter(series_id__in=series_ids, source="imdb")
        .values_list("series_id", "rating")
    )
    for row in filmography:
        obj = row["title_obj"]
        if row["kind"] == "movie":
            obj.imdb_rating = movie_ratings.get(obj.movie_id)
        else:
            obj.imdb_rating = series_ratings.get(obj.series_id)

    # The average is taken over exactly the figures the two dicts above hand
    # to the grid (Task 89 step 4), rather than a separate aggregate query
    # against each rating table — the header number can't drift from what
    # the cards show, by construction, and it costs no extra query.
    all_ratings = list(movie_ratings.values()) + list(series_ratings.values())
    avg_rating = sum(all_ratings) / len(all_ratings) if all_ratings else None

    # Search + reorder the filmography, the same toolbar /studios/<slug>/ puts
    # over its filmography (Task 62). Done in Python: the list above is already
    # merged one-row-per-title and fully in memory, and a filmography is small
    # (a few hundred rows for the most prolific person here). The header stats
    # are computed over the whole filmography above and never move as this
    # narrows — the same contract the studio page keeps.
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "release")
    if sort not in FILMOGRAPHY_SORTS:
        sort = "release"

    rows = filmography
    if q:
        rows = [r for r in rows if q.lower() in _filmography_title(r["title_obj"]).lower()]
    rows = _sorted_filmography(rows, sort)

    page_obj = Paginator(rows, MOVIES_PER_PAGE).get_page(request.GET.get("page"))

    context = {
        "person": person,
        "filmography": filmography,
        "page_obj": page_obj,
        "q": q,
        "sort": sort,
        "base_query": urlencode({"q": q, "sort": sort}),
        "film_count": len(movie_ids),
        "show_count": len(series_ids),
        "title_count": len(movie_ids) + len(series_ids),
        "credit_count": len(credits) + len(series_credits),
        "avg_rating": avg_rating,
        # Activity is deliberately just two states: "Retired" once TMDB records
        # a death date, "Active" otherwise. The catalogue is too thin for
        # "years since last credit" to mean anything, and this reads the same
        # for an actor, a director or a crew member.
        "activity": "Retired" if person.deathday else "Active",
    }
    # Same live-filter contract as movie_list()/studio_detail(): the record
    # header is never part of the swap, so only the grid+pager fragment comes
    # back for an AJAX refetch.
    if _is_ajax(request):
        return render(request, "movies/_person_filmography_results.html", context)
    return render(request, "movies/person_detail.html", context)


