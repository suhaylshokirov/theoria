from django.core.paginator import Paginator
from django.db.models import Avg, F, Max, Min, Sum
from django.db.models.functions import ExtractYear
from django.shortcuts import get_object_or_404, render

from movies.models import Actor, Casting, Director, Genre, Movie, MovieMetrics

MOVIES_PER_PAGE = 24
PEOPLE_PER_PAGE = 30

# ?sort= values accepted by movie_list, mapped to an order_by expression.
# Nulls always sort last so movies missing a field don't lead the list.
MOVIE_SORTS = {
    "release": F("release_date").desc(nulls_last=True),
    "rating": F("top_rating").desc(nulls_last=True),
    "revenue": F("revenue").desc(nulls_last=True),
    "title": F("title").asc(),
}


def home(request):
    """Landing page: warehouse-wide stats plus browse strips into the lists."""
    top_rated = (
        Movie.objects.using("warehouse")
        .annotate(top_rating=Max("moviemetrics__rating"))
        .order_by(F("top_rating").desc(nulls_last=True))[:12]
    )
    newest = (
        Movie.objects.using("warehouse")
        .order_by(F("release_date").desc(nulls_last=True))[:12]
    )

    context = {
        "movie_count": Movie.objects.using("warehouse").count(),
        "actor_count": Actor.objects.using("warehouse").count(),
        "director_count": Director.objects.using("warehouse").count(),
        "avg_rating": MovieMetrics.objects.using("warehouse").aggregate(
            avg_rating=Avg("rating")
        )["avg_rating"],
        "top_rated": top_rated,
        "newest": newest,
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


def _person_list(request, model, list_title, detail_url_name):
    """Shared list view for actors and directors: name search + pagination."""
    q = request.GET.get("q", "").strip()

    people = model.objects.using("warehouse").all()
    if q:
        people = people.filter(name__icontains=q)
    people = people.order_by(F("popularity").desc(nulls_last=True))

    page_obj = Paginator(people, PEOPLE_PER_PAGE).get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "q": q,
        "list_title": list_title,
        "detail_url_name": detail_url_name,
    }
    return render(request, "movies/person_list.html", context)


def actor_list(request):
    return _person_list(request, Actor, "Actors", "movies:actor_detail")


def director_list(request):
    return _person_list(request, Director, "Directors", "movies:director_detail")


def genre_list(request):
    """All genres, linking to each genre's detail page."""
    genres = Genre.objects.using("warehouse").order_by("genre_name")
    return render(request, "movies/genre_list.html", {"genres": genres})


def movie_detail(request, movie_id):
    """Single movie: core facts, genres, directors, and cast."""
    movie = get_object_or_404(Movie.objects.using("warehouse"), pk=movie_id)

    genres = (
        Genre.objects.using("warehouse")
        .filter(moviemetrics__movie_id=movie_id)
        .distinct()
    )

    cast = (
        Casting.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("actor", "director")
    )

    # fact_casting has one row per (actor, director) pair, so the same
    # director repeats across every actor row — collect them separately.
    directors = (
        Director.objects.using("warehouse")
        .filter(casting__movie_id=movie_id)
        .distinct()
    )

    context = {
        "movie": movie,
        "genres": genres,
        "cast": cast,
        "directors": directors,
    }
    return render(request, "movies/movie_detail.html", context)


def actor_detail(request, actor_id):
    """Single actor: filmography via fact_casting, with career stats computed in SQL."""
    actor = get_object_or_404(Actor.objects.using("warehouse"), pk=actor_id)

    movie_ids = (
        Casting.objects.using("warehouse")
        .filter(actor_id=actor_id)
        .values_list("movie_id", flat=True)
        .distinct()
    )

    filmography = (
        Movie.objects.using("warehouse")
        .filter(movie_id__in=movie_ids)
        .order_by("-release_date")
    )

    # fact_movie_metrics has one row per (movie_id, genre_id), so a multi-genre
    # movie would be double-counted by a plain Avg — collapse to one row per
    # movie first, then average across that.
    movie_ratings = (
        MovieMetrics.objects.using("warehouse")
        .filter(movie_id__in=movie_ids)
        .values("movie_id", "rating")
        .distinct()
    )
    avg_rating = movie_ratings.aggregate(avg_rating=Avg("rating"))["avg_rating"]

    career_span = filmography.aggregate(
        earliest=Min("release_date"), latest=Max("release_date")
    )

    context = {
        "actor": actor,
        "filmography": filmography,
        "film_count": filmography.count(),
        "avg_rating": avg_rating,
        "career_start": career_span["earliest"],
        "career_end": career_span["latest"],
    }
    return render(request, "movies/actor_detail.html", context)


def director_detail(request, director_id):
    """Single director: filmography via fact_casting, with career stats computed in SQL."""
    director = get_object_or_404(Director.objects.using("warehouse"), pk=director_id)

    movie_ids = (
        Casting.objects.using("warehouse")
        .filter(director_id=director_id)
        .values_list("movie_id", flat=True)
        .distinct()
    )

    filmography = (
        Movie.objects.using("warehouse")
        .filter(movie_id__in=movie_ids)
        .order_by("-release_date")
    )

    # fact_movie_metrics has one row per (movie_id, genre_id), so a multi-genre
    # movie would be double-counted by a plain Avg — collapse to one row per
    # movie first, then average across that.
    movie_ratings = (
        MovieMetrics.objects.using("warehouse")
        .filter(movie_id__in=movie_ids)
        .values("movie_id", "rating")
        .distinct()
    )
    avg_rating = movie_ratings.aggregate(avg_rating=Avg("rating"))["avg_rating"]

    career_span = filmography.aggregate(
        earliest=Min("release_date"), latest=Max("release_date")
    )

    context = {
        "director": director,
        "filmography": filmography,
        "film_count": filmography.count(),
        "avg_rating": avg_rating,
        "career_start": career_span["earliest"],
        "career_end": career_span["latest"],
    }
    return render(request, "movies/director_detail.html", context)


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
