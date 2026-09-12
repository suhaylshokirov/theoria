"""Analytics dashboard.

Each panel runs one of the hand-written .sql files from warehouse/queries/
directly against the warehouse connection, rather than reimplementing the
same aggregation in the ORM — the project rule is that all analytics SQL
lives in .sql files, so the dashboard reads and executes them as-is.
"""

from pathlib import Path

from django.db import connections
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

QUERIES_DIR = Path(__file__).resolve().parent.parent.parent / "warehouse" / "queries"


def _run_query(filename):
    """Execute a .sql file against the warehouse and return rows as dicts."""
    sql = (QUERIES_DIR / filename).read_text()
    with connections["warehouse"].cursor() as cursor:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


@login_required
def dashboard(request):
    movies_by_decade = _run_query("movies_by_decade.sql")
    revenue_by_genre = _run_query("revenue_by_genre.sql")
    top_studios_by_revenue = _run_query("top_studios_by_revenue.sql")
    films_by_production_country = _run_query("films_by_production_country.sql")
    # TV panels (Task 90) — all shaped for TV, not ported from the film
    # queries: no revenue column anywhere (TV carries no money measure), and
    # episode_rating_by_season has no film-side analogue at all.
    series_by_decade = _run_query("series_by_decade.sql")
    episode_rating_by_season = _run_query("episode_rating_by_season.sql")
    longest_running_series = _run_query("longest_running_series.sql")
    top_networks_by_series = _run_query("top_networks_by_series.sql")

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
