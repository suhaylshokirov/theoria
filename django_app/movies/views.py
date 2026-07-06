from django.db.models import Avg, Max, Min, Sum
from django.db.models.functions import ExtractYear
from django.shortcuts import get_object_or_404, render

from movies.models import Actor, Casting, Director, Genre, Movie, MovieMetrics


def home(request):
    """Landing page: high-level counts + average rating across the warehouse."""
    context = {
        "movie_count": Movie.objects.using("warehouse").count(),
        "actor_count": Actor.objects.using("warehouse").count(),
        "director_count": Director.objects.using("warehouse").count(),
        "avg_rating": MovieMetrics.objects.using("warehouse").aggregate(
            avg_rating=Avg("rating")
        )["avg_rating"],
    }
    return render(request, "movies/home.html", context)


def movie_detail(request, movie_id):
    """Single movie: core facts, genres, and cast/director — three queries total."""
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

    context = {
        "movie": movie,
        "genres": genres,
        "cast": cast,
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
