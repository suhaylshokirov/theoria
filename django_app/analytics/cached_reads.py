"""Cached dashboard reads (Task 116, reshaped in Task 133).

The dashboard's panels are fixed, parameterless .sql files, so their raw
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

# Task 133 changed which files make up the dashboard, so the cached dict has a
# different shape. A cache key outlives a deploy (it only changes when the
# pipeline publishes a new data version), so the old "dashboard_rows" entry
# would be served to the new view and miss every key. Renaming the entry makes
# the first request after the deploy a clean miss.
CACHE_NAME = "analytics_dashboard"

# The panels, grouped by the chapter that reads them.
DASHBOARD_QUERIES = (
    # Headline strip and chapter 1 (quality)
    "dashboard_kpis.sql",
    "rating_distribution.sql",
    # Chapter 2 (money)
    "budget_vs_revenue.sql",
    "return_bands_by_genre.sql",
    "studio_returns.sql",
    # Chapter 3 (time)
    "runtime_by_decade.sql",
    "non_english_cinema_over_time.sql",
    "genre_share_by_decade.sql",
    # Chapter 4 (world)
    "films_by_production_country.sql",
    "original_language_mix.sql",
    # Chapter 5 (people)
    "top_rated_directors.sql",
    "signature_partnerships.sql",
    # Chapter 6 (TV) — shaped for TV, not ported from the film queries: no
    # revenue column anywhere (TV carries no money measure).
    "season_trajectory.sql",
    "series_rating_by_status.sql",
    "network_quality.sql",
)


def _run_query(filename):
    """Execute a .sql file against the warehouse and return rows as dicts."""
    sql = (QUERIES_DIR / filename).read_text(encoding="utf-8")
    with connections["warehouse"].cursor() as cursor:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def build_dashboard_rows():
    """`{filename: rows}` for every panel, in English, as the SQL returns them."""
    return {filename: _run_query(filename) for filename in DASHBOARD_QUERIES}


def dashboard_rows():
    """The cached result sets.

    Callers may translate rows in place (analytics.views does, via
    localize_rows): that is safe only because every cache hit unpickles a fresh
    copy. Do not memoise this result in process memory, or the first reader's
    language would leak into the next.
    """
    return cached(CACHE_NAME, build_dashboard_rows)
