"""Cached dashboard reads (Task 116).

The dashboard's eight panels are fixed, parameterless .sql files, so their raw
result sets are the same for every reader and change only when the warehouse
does. They are cached once, in English exactly as the SQL returns them, under a
language-free key; translating genre and country names is left to the view,
after the cache read, so one entry serves en, ru and uz.
"""

from __future__ import annotations

from pathlib import Path

from django.db import connections

from core.datacache import cached

QUERIES_DIR = Path(__file__).resolve().parent.parent.parent / "warehouse" / "queries"

# The eight panels, in the order the dashboard shows them.
DASHBOARD_QUERIES = (
    "movies_by_decade.sql",
    "revenue_by_genre.sql",
    "top_studios_by_revenue.sql",
    "films_by_production_country.sql",
    # TV panels (Task 90) — all shaped for TV, not ported from the film
    # queries: no revenue column anywhere (TV carries no money measure), and
    # episode_rating_by_season has no film-side analogue at all.
    "series_by_decade.sql",
    "episode_rating_by_season.sql",
    "longest_running_series.sql",
    "top_networks_by_series.sql",
)


def _run_query(filename):
    """Execute a .sql file against the warehouse and return rows as dicts."""
    sql = (QUERIES_DIR / filename).read_text()
    with connections["warehouse"].cursor() as cursor:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def build_dashboard_rows():
    """`{filename: rows}` for all eight panels, in English, as the SQL returns them."""
    return {filename: _run_query(filename) for filename in DASHBOARD_QUERIES}


def dashboard_rows():
    """The cached result sets.

    Callers may translate rows in place (analytics.views does, via
    localize_rows): that is safe only because every cache hit unpickles a fresh
    copy. Do not memoise this result in process memory, or the first reader's
    language would leak into the next.
    """
    return cached("dashboard_rows", build_dashboard_rows)
