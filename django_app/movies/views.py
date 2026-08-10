from datetime import date

from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Max, Min, Sum
from django.db.models.functions import ExtractYear
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import urlencode


from movies.models import (
    Credit, Genre, Movie, MovieMetrics, Person,
)

MOVIES_PER_PAGE = 24
PEOPLE_PER_PAGE = 30


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
MOVIE_SORTS = {
    "release": F("release_date").desc(nulls_last=True),
    "rating": F("top_rating").desc(nulls_last=True),
    "revenue": F("revenue").desc(nulls_last=True),
    "title": F("title").asc(),
}


def home(request):
    """Landing page: the catalog as a contact sheet, plus warehouse-wide stats."""
    top_rated = (
        Movie.objects.using("warehouse")
        .annotate(top_rating=Max("moviemetrics__rating"))
        .order_by(F("top_rating").desc(nulls_last=True))[:12]
    )
    newest = (
        Movie.objects.using("warehouse")
        .order_by(F("release_date").desc(nulls_last=True))[:12]
    )

    # The mosaic only holds films that actually have a poster — a missing
    # image would punch a hole in the sheet.
    mosaic = (
        Movie.objects.using("warehouse")
        .filter(poster_path__isnull=False)
        .order_by(F("release_date").desc(nulls_last=True))[:MOSAIC_LIMIT]
    )

    context = {
        "movie_count": Movie.objects.using("warehouse").count(),
        "person_count": Person.objects.using("warehouse").count(),
        "credit_count": Credit.objects.using("warehouse").count(),
        "avg_rating": MovieMetrics.objects.using("warehouse").aggregate(
            avg_rating=Avg("rating")
        )["avg_rating"],
        "top_rated": top_rated,
        "newest": newest,
        "mosaic": mosaic,
    }
    return render(request, "movies/home.html", context)


def movie_list(request):
    """Browsable movie catalog: poster grid + title search + sort + pagination."""
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "release")
    if sort not in MOVIE_SORTS:
        sort = "release"

    movies = Movie.objects.using("warehouse").all()
    if q:
        movies = movies.filter(title__icontains=q)
    if sort == "rating":
        movies = movies.annotate(top_rating=Max("moviemetrics__rating"))
    movies = movies.order_by(MOVIE_SORTS[sort])

    page_obj = Paginator(movies, MOVIES_PER_PAGE).get_page(request.GET.get("page"))

    # Built here, not in the template, so the shared _pager.html partial
    # doesn't need to know which params any given page carries — see
    # _pager.html's docstring.
    context = {
        "page_obj": page_obj, "q": q, "sort": sort,
        "base_query": urlencode({"q": q, "sort": sort}),
    }
    # See _person_list()'s identical branch: static/js/theoria.js's
    # initLiveFilter() re-requests this URL with this header on every filter
    # change and only wants the results fragment back, not the page around it.
    if _is_ajax(request):
        return render(request, "movies/_movie_results.html", context)
    return render(request, "movies/movie_list.html", context)


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


def genre_list(request):
    """All genres, with how much of the catalog each one accounts for.

    fact_movie_metrics holds one row per (movie, date, genre), so counting
    movie_id needs distinct=True or a movie appearing under several date_ids
    would inflate its genre's total.
    """
    genres = (
        Genre.objects.using("warehouse")
        .annotate(movie_count=Count("moviemetrics__movie_id", distinct=True))
        .order_by("-movie_count", "genre_name")
    )

    # The share bars scale against the biggest genre, so the widest bar is
    # always full — computed here rather than in the template, which can't.
    # getattr guards the annotation: a Genre built outside this queryset has
    # no movie_count, and an empty warehouse has no rows at all.
    max_count = max((getattr(g, "movie_count", 0) or 0 for g in genres), default=0)

    context = {"genres": genres, "max_count": max_count}
    return render(request, "movies/genre_list.html", context)


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

    # fact_movie_metrics has one row per (movie, date, genre), and rating is
    # a movie-level measure repeated identically across those rows. So take
    # one row rather than averaging: .values(...).distinct() collapses the
    # genre fan-out to a single tuple, and .first() reads it. Averaging here
    # would be a silent trap — it happens to give the right answer only
    # because the duplicated values are equal.
    metrics = (
        MovieMetrics.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .values("rating")
        .distinct()
        .first()
    )

    context = {
        "movie": movie,
        "genres": genres,
        "cast": cast,
        "cast_count": len(cast),
        "crew": crew,
        "crew_person_count": len(merged_crew),
        "credit_count": len(credits),
        "directors": directors,
        "metrics": metrics,
    }
    return render(request, "movies/movie_detail.html", context)


def _career_period(start, end):
    """Render a career span as a person-page stat, e.g. "1997–2019" or "2019–Active".

    A closed range naming the same year twice (e.g. "2026–2026" for a single
    film released this year) reads as a typo, not a fact. And a range that
    ends in the current year isn't really "closed" — the person's latest
    known film is one that just came out, not one that ended their career.
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


def _merge_person_credits(credits):
    """Collapse a person's several credits on one film into one filmography row.

    Same shape of problem as _merge_crew, keyed by movie instead of person: an
    actor who also directed or wrote the same film would otherwise appear as
    duplicate entries in separate department sections (Acting, Directing, ...).
    One row per movie, with every job on it joined in department order, so a
    director who also wrote the script reads "Director / Screenplay" once,
    under one poster, rather than twice under two.
    """
    by_movie = {}
    for credit in credits:
        by_movie.setdefault(credit.movie_id, []).append(credit)

    merged = []
    for movie_id, rows in by_movie.items():
        rows_sorted = sorted(rows, key=lambda c: _department_rank(c.department))
        labels = [
            c.character_name if c.department == "Acting" and c.character_name else c.job
            for c in rows_sorted
        ]
        merged.append({
            "movie": rows_sorted[0].movie,
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


def actor_detail(request, actor_slug):
    return _redirect_to_person(actor_slug)


def director_detail(request, director_slug):
    return _redirect_to_person(director_slug)


def person_detail(request, person_slug):
    """One person, every film they worked on, and what they did there."""
    person = get_object_or_404(Person.objects.using("warehouse"), slug=person_slug)
    person_id = person.person_id

    # One query for every credit, joined to its film. Merging happens in
    # Python below, since a GROUP BY can't return the rows themselves.
    credits = list(
        Credit.objects.using("warehouse")
        .filter(person_id=person_id)
        .select_related("movie")
        .order_by(F("movie__release_date").desc(nulls_last=True))
    )

    filmography = _merge_person_credits(credits)

    movie_ids = {c.movie_id for c in credits}

    # Same genre-fanout guard as everywhere else: fact_movie_metrics repeats a
    # movie's rating once per genre, so collapse before averaging.
    avg_rating = (
        MovieMetrics.objects.using("warehouse")
        .filter(movie_id__in=movie_ids)
        .values("movie_id", "rating")
        .distinct()
        .aggregate(avg_rating=Avg("rating"))["avg_rating"]
    )

    span = Movie.objects.using("warehouse").filter(movie_id__in=movie_ids).aggregate(
        earliest=Min("release_date"), latest=Max("release_date")
    )

    context = {
        "person": person,
        "filmography": filmography,
        "film_count": len(movie_ids),
        "credit_count": len(credits),
        "avg_rating": avg_rating,
        "career_period": _career_period(span["earliest"], span["latest"]),
    }
    return render(request, "movies/person_detail.html", context)


def genre_detail(request, genre_id):
    """Single genre: top-rated movies and revenue trend by year.

    Mirrors etl.gold.build_gold_datasets._build_genre_metrics, but computed
    live via the ORM against fact_movie_metrics rather than read from the
    Gold Parquet in S3 — Django's warehouse connection is Postgres-only.
    """
    genre = get_object_or_404(Genre.objects.using("warehouse"), pk=genre_id)

    metrics = (
        MovieMetrics.objects.using("warehouse")
        .filter(genre_id=genre_id)
        .select_related("movie")
    )

    top_movies = metrics.order_by("-rating")[:10]

    # Group by release year to build a revenue trend. fact_movie_metrics has
    # one row per (movie_id, date_id, genre_id), but a movie only ever has
    # one date_id/release_date, so grouping directly on the filtered metrics
    # (rather than re-querying Movie) doesn't double-count revenue.
    revenue_by_year = (
        metrics.filter(movie__release_date__isnull=False)
        .annotate(year=ExtractYear("movie__release_date"))
        .values("year")
        .annotate(total_revenue=Sum("movie__revenue"))
        .order_by("year")
    )

    movie_count = metrics.values("movie_id").distinct().count()
    avg_rating = metrics.aggregate(avg_rating=Avg("rating"))["avg_rating"]

    context = {
        "genre": genre,
        "top_movies": top_movies,
        "revenue_by_year": revenue_by_year,
        "movie_count": movie_count,
        "avg_rating": avg_rating,
    }
    return render(request, "movies/genre_detail.html", context)
