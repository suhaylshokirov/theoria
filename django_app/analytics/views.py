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
    studio_output_by_decade = _run_query("studio_output_by_decade.sql")
    top_studios_by_revenue = _run_query("top_studios_by_revenue.sql")

    context = {
        "revenue_by_genre": revenue_by_genre,
        "movies_by_decade": movies_by_decade,
        "studio_output_by_decade": studio_output_by_decade,
        "top_studios_by_revenue": top_studios_by_revenue,
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
