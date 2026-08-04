from datetime import date

from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Max, Min, Sum
from django.db.models.functions import ExtractYear
from django.shortcuts import get_object_or_404, redirect, render

from movies.models import (
    Actor, Cast, Collaboration, Collection, Credit, Crew, Director, Genre,
    Movie, MovieMetrics, Person,
)

MOVIES_PER_PAGE = 24
PEOPLE_PER_PAGE = 30

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

    context = {"page_obj": page_obj, "q": q, "sort": sort}
    return render(request, "movies/movie_list.html", context)


def _person_list(request, people, list_title, scope):
    """Shared list view for every people index: name search + pagination.

    Takes a queryset rather than a model, because the three indexes now differ
    by which credits a person holds, not by which table they live in. Every
    person page lives at /people/<slug>/, so there is no per-list URL name.
    """
    q = request.GET.get("q", "").strip()

    if q:
        people = people.filter(name__icontains=q)
    people = people.order_by(F("popularity").desc(nulls_last=True))

    page_obj = Paginator(people, PEOPLE_PER_PAGE).get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "q": q,
        "list_title": list_title,
        "scope": scope,
        "detail_url_name": "movies:person_detail",
    }
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


def collection_list(request):
    """Every franchise, ranked by how many of its films the catalog holds.

    Roughly half the catalog belongs to no franchise, so this is deliberately a
    view of a minority of the corpus — a `Count` over the reverse FK, which
    excludes collections with no loaded films rather than showing empty rows.
    """
    collections = (
        Collection.objects.using("warehouse")
        .annotate(movie_count=Count("movies"))
        .filter(movie_count__gt=0)
        .order_by("-movie_count", "name")
    )

    context = {"collections": collections}
    return render(request, "movies/collection_list.html", context)


def collection_detail(request, collection_slug):
    """Single franchise: its films in release order, plus series totals."""
    collection = get_object_or_404(
        Collection.objects.using("warehouse"), slug=collection_slug
    )

    films = (
        Movie.objects.using("warehouse")
        .filter(collection_id=collection.collection_id)
        .order_by(F("release_date").asc(nulls_last=True))
    )

    # Revenue and film count come straight off dim_movie — one row per film, so
    # no fan-out to guard against. avg_rating does need the guard: it lives in
    # fact_movie_metrics at (movie, date, genre) grain, so it is collapsed to
    # one row per movie before averaging, the same shape actor_detail uses.
    totals = films.aggregate(total_revenue=Sum("revenue"), total_budget=Sum("budget"))
    ratings = (
        MovieMetrics.objects.using("warehouse")
        .filter(movie_id__in=films.values("movie_id"))
        .values("movie_id", "rating")
        .distinct()
    )
    rating_values = [r["rating"] for r in ratings if r["rating"] is not None]
    avg_rating = sum(rating_values) / len(rating_values) if rating_values else None

    years = films.aggregate(first=Min("release_date"), last=Max("release_date"))
    span = _career_period(years["first"], years["last"])

    context = {
        "collection": collection,
        "films": films,
        "film_count": films.count(),
        "total_revenue": totals["total_revenue"],
        "avg_rating": avg_rating,
        "span": span,
    }
    return render(request, "movies/collection_detail.html", context)


def movie_detail(request, movie_slug):
    """Single movie: core facts, genres, directors, and cast."""
    movie = get_object_or_404(
        Movie.objects.using("warehouse").select_related("collection"), slug=movie_slug
    )
    movie_id = movie.movie_id

    genres = (
        Genre.objects.using("warehouse")
        .filter(moviemetrics__movie_id=movie_id)
        .distinct()
    )

    # One query for every credit on the film, cast and crew alike, then split
    # in Python. Cast leads by billing order; crew is grouped by department.
    # Before fact_credit this page could only show the cast and the director —
    # the ~150 other people who made the film had no warehouse row at all.
    credits = list(
        Credit.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("person")
        .order_by(F("ordering").asc(nulls_last=True), "job")
    )

    cast = [c for c in credits if c.department == "Acting"]

    crew_by_department = {}
    for credit in credits:
        if credit.department != "Acting":
            crew_by_department.setdefault(credit.department, []).append(credit)
    crew = [
        {"name": name, "credits": rows, "count": len(rows)}
        for name, rows in sorted(
            crew_by_department.items(),
            key=lambda kv: (
                DEPARTMENT_ORDER.index(kv[0]) if kv[0] in DEPARTMENT_ORDER
                else len(DEPARTMENT_ORDER),
                kv[0],
            ),
        )
    ]

    directors = [c.person for c in credits if c.job == "Director"]

    # fact_movie_metrics has one row per (movie, date, genre), and rating /
    # vote_count are movie-level measures repeated identically across those
    # rows. So take one row rather than averaging: .values(...).distinct()
    # collapses the genre fan-out to a single tuple, and .first() reads it.
    # Averaging here would be a silent trap — it happens to give the right
    # answer only because the duplicated values are equal.
    metrics = (
        MovieMetrics.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .values("rating", "vote_count")
        .distinct()
        .first()
    )

    context = {
        "movie": movie,
        "genres": genres,
        "cast": cast,
        "crew": crew,
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

# How many repeat collaborators the person page prints.
COLLABORATORS_SHOWN = 8


def _redirect_to_person(request, legacy_model, legacy_pk, slug):
    """301 a legacy /actors/<slug>/ or /directors/<slug>/ URL to /people/<slug>/.

    Resolved by id, never by slug. Unifying dim_actor and dim_director into one
    dim_person namespace re-numbered 381 slugs (a crew member with a lower TMDB
    id claims the base name), so the legacy slug and the person slug are not
    interchangeable — the id is the only stable link between them.
    """
    legacy = get_object_or_404(legacy_model.objects.using("warehouse"), slug=slug)
    person = get_object_or_404(
        Person.objects.using("warehouse"), pk=getattr(legacy, legacy_pk)
    )
    return redirect("movies:person_detail", person_slug=person.slug, permanent=True)


def actor_detail(request, actor_slug):
    return _redirect_to_person(request, Actor, "actor_id", actor_slug)


def director_detail(request, director_slug):
    return _redirect_to_person(request, Director, "director_id", director_slug)


def person_detail(request, person_slug):
    """One person, every credit they hold, and who they keep working with."""
    person = get_object_or_404(Person.objects.using("warehouse"), slug=person_slug)
    person_id = person.person_id

    # One query for every credit, joined to its film. Grouping happens in
    # Python below: a GROUP BY can't return the rows themselves, and one query
    # per department would be an N+1 in the number of crafts a person works in.
    credits = (
        Credit.objects.using("warehouse")
        .filter(person_id=person_id)
        .select_related("movie")
        .order_by(F("movie__release_date").desc(nulls_last=True))
    )

    by_department = {}
    for credit in credits:
        by_department.setdefault(credit.department, []).append(credit)

    ordered = sorted(
        by_department.items(),
        key=lambda kv: (
            DEPARTMENT_ORDER.index(kv[0]) if kv[0] in DEPARTMENT_ORDER
            else len(DEPARTMENT_ORDER),
            kv[0],
        ),
    )
    departments = [
        {"name": name, "credits": rows, "count": len(rows)} for name, rows in ordered
    ]

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
        "departments": departments,
        "film_count": len(movie_ids),
        "credit_count": len(credits),
        "avg_rating": avg_rating,
        "career_period": _career_period(span["earliest"], span["latest"]),
        "collaborators": _top_collaborators(person_id),
    }
    return render(request, "movies/person_detail.html", context)


def _top_collaborators(person_id):
    """The people this person has worked with most, from fact_collaboration.

    Pairs are stored canonically (person_a_id < person_b_id), so a person can
    be on either side and both columns have to be searched. That's the cost of
    halving the table — paid here, on read, once per page.
    """
    as_a = (
        Collaboration.objects.using("warehouse")
        .filter(person_a_id=person_id)
        .select_related("person_b")
    )
    as_b = (
        Collaboration.objects.using("warehouse")
        .filter(person_b_id=person_id)
        .select_related("person_a")
    )

    rows = [
        {"person": c.person_b, "films_together": c.films_together,
         "first_year": c.first_year, "last_year": c.last_year}
        for c in as_a
    ] + [
        {"person": c.person_a, "films_together": c.films_together,
         "first_year": c.first_year, "last_year": c.last_year}
        for c in as_b
    ]

    rows.sort(key=lambda r: (-r["films_together"], r["person"].name))
    return rows[:COLLABORATORS_SHOWN]


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
