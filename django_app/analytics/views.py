"""Analytics dashboard.

Each panel runs one of the hand-written .sql files from warehouse/queries/
directly against the warehouse connection, rather than reimplementing the
same aggregation in the ORM — the project rule is that all analytics SQL
lives in .sql files, so the dashboard reads and executes them as-is.
"""

from pathlib import Path

from django.db import connections
from django.shortcuts import render

QUERIES_DIR = Path(__file__).resolve().parent.parent.parent / "warehouse" / "queries"


def _run_query(filename):
    """Execute a .sql file against the warehouse and return rows as dicts."""
    sql = (QUERIES_DIR / filename).read_text()
    with connections["warehouse"].cursor() as cursor:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def dashboard(request):
    movies_by_decade = _run_query("movies_by_decade.sql")
    revenue_by_genre = _run_query("revenue_by_genre.sql")

    context = {
        "top_rated_directors": _run_query("top_rated_directors.sql"),
        "most_productive_actors": _run_query("most_productive_actors.sql"),
        "revenue_by_genre": revenue_by_genre,
        "movies_by_decade": movies_by_decade,
        "director_trend_over_time": _run_query("director_trend_over_time.sql"),
        "actor_collaboration_frequency": _run_query("actor_collaboration_frequency.sql"),
        "genre_growth_over_time": _run_query("genre_growth_over_time.sql"),
        # Pre-shaped as flat label/value lists (with Decimal -> float) for the
        # two Chart.js trend panels — the tables above reuse the raw rows.
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
    }
    return render(request, "analytics/dashboard.html", context)
