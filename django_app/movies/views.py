from django.db.models import Avg
from django.shortcuts import render

from movies.models import Actor, Director, Movie, MovieMetrics


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
