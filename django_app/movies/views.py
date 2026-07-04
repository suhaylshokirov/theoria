from django.db.models import Avg
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
