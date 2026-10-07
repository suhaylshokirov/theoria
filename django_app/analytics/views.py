"""Analytics dashboard.

Each panel runs one of the hand-written .sql files from warehouse/queries/
directly against the warehouse connection, rather than reimplementing the
same aggregation in the ORM — the project rule is that all analytics SQL
lives in .sql files, so the dashboard reads and executes them as-is
(analytics/cached_reads.py, which also caches the results, Task 116).
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from analytics.cached_reads import dashboard_rows
from movies.i18n import country_labels, genre_labels, localize_rows


@login_required
def dashboard(request):
    # The raw result sets come from the cache (or, on a miss, the warehouse).
    # The .sql files stay English (project rule: analytics SQL lives in .sql
    # files, unparameterized); genre and country names are translated here,
    # after the read, from the same lookups the movie pages use — so the
    # tables and the Chart.js labels below can never disagree.
    rows = dashboard_rows()
    movies_by_decade = rows["movies_by_decade.sql"]
    revenue_by_genre = localize_rows(
        rows["revenue_by_genre.sql"], "genre_name", genre_labels()
    )
    top_studios_by_revenue = rows["top_studios_by_revenue.sql"]
    films_by_production_country = localize_rows(
        rows["films_by_production_country.sql"], "country_name", country_labels()
    )
    series_by_decade = rows["series_by_decade.sql"]
    episode_rating_by_season = rows["episode_rating_by_season.sql"]
    longest_running_series = rows["longest_running_series.sql"]
    top_networks_by_series = rows["top_networks_by_series.sql"]

    context = {
        "revenue_by_genre": revenue_by_genre,
        "movies_by_decade": movies_by_decade,
        "top_studios_by_revenue": top_studios_by_revenue,
        "films_by_production_country": films_by_production_country,
        "series_by_decade": series_by_decade,
        "episode_rating_by_season": episode_rating_by_season,
        "longest_running_series": longest_running_series,
        "top_networks_by_series": top_networks_by_series,
        # Pre-shaped as flat label/value lists (with Decimal -> float) for the
        # Chart.js trend panels — the tables above reuse the raw rows.
        "decade_labels": [row["decade"] for row in movies_by_decade],
        "decade_avg_ratings": [
            float(row["avg_rating"]) if row["avg_rating"] is not None else None
            for row in movies_by_decade
        ],
        "genre_labels": [row["genre_name"] for row in revenue_by_genre],
        "genre_revenue": [
            float(row["total_revenue"]) if row["total_revenue"] is not None else None
            for row in revenue_by_genre
        ],
        "season_labels": [row["season_number"] for row in episode_rating_by_season],
        "season_avg_ratings": [
            float(row["avg_rating"]) if row["avg_rating"] is not None else None
            for row in episode_rating_by_season
        ],
    }
    return render(request, "analytics/dashboard.html", context)
