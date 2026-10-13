"""Analytics dashboard.

Each panel runs one of the hand-written .sql files from warehouse/queries/
directly against the warehouse connection, rather than reimplementing the
same aggregation in the ORM -- the project rule is that all analytics SQL
lives in .sql files, so the dashboard reads and executes them as-is
(analytics/cached_reads.py, which also caches the results, Task 116).

The reshaping and the one-sentence findings live in analytics/insights.py;
this view only fetches, translates names, and renders (Task 133).
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from analytics import insights
from analytics.cached_reads import dashboard_rows
from movies.i18n import (
    country_labels, genre_labels, localize_rows, movie_titles_by_slug, person_names_by_slug,
)


@login_required
def dashboard(request):
    # The raw result sets come from the cache (or, on a miss, the warehouse).
    # The .sql files stay English (project rule: analytics SQL lives in .sql
    # files, unparameterized); genre and country names are translated here,
    # after the read, from the same lookups the movie pages use -- so the
    # tables and the Chart.js labels can never disagree.
    rows = dashboard_rows()
    genres = genre_labels()
    for filename in ("return_bands_by_genre.sql", "genre_share_by_decade.sql"):
        localize_rows(rows[filename], "genre_name", genres)
    localize_rows(rows["films_by_production_country.sql"], "country_name", country_labels())

    # People are keyed by slug in the rows, so one lookup renames the director
    # table, the partnership table and the headline finding alike (Task 134).
    directors, partners = rows["top_rated_directors.sql"], rows["signature_partnerships.sql"]
    people = person_names_by_slug(
        [r["director_slug"] for r in directors + partners]
        + [r["collaborator_slug"] for r in partners]
    )
    for row in directors + partners:
        row["director_name"] = people.get(row["director_slug"], row["director_name"])
    for row in partners:
        row["collaborator_name"] = people.get(row["collaborator_slug"], row["collaborator_name"])

    context = insights.build(rows)

    # The two poster callouts carry a title the reader can see; the thousand
    # scatter points keep the original title (a tooltip, not a heading).
    callouts = [c for c in (context["money"]["best"], context["money"]["priciest"]) if c]
    titles = movie_titles_by_slug(c["slug"] for c in callouts)
    for callout in callouts:
        callout["title"] = titles.get(callout["slug"], callout["title"])

    return render(request, "analytics/dashboard.html", context)
