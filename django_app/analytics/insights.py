"""Shaping and findings for the analytics dashboard (Task 133).

`analytics.cached_reads` hands back raw result sets, one per .sql file. This
module turns them into what the template and the charts need, and works out the
one-sentence *finding* that opens each chapter. Everything here is pure Python
over plain rows -- no database, no request -- so it is tested directly.

A finding is a small dict of numbers and a `kind`, never a finished sentence:
the template owns the words (so each reads naturally in en/ru/uz) and this
module owns the arithmetic. A finding is None when the data is too thin to
state one, and the template then shows no sentence at all rather than a vague
one. Nothing here is hard-coded prose about the archive: if the data changes,
the sentence changes with it.

All figures describe THIS archive, not cinema at large -- it is deliberately
sampled (about 200 movies per decade, ~91% English-language). The captions in
the template say so.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean

from django.utils.translation import get_language_info
from django.utils.translation import gettext as _

# A season counts as "holding" its first-season rating while it stays within
# this many rating points of it.
SEASON_HOLD_TOLERANCE = 0.10
# Two runtimes (minutes) closer than this are "about the same length".
RUNTIME_SAME_MINUTES = 3
# Two medians closer than this are "about the same".
RATING_SAME_POINTS = 0.1
# How many languages get their own row before the rest are folded together.
LANGUAGE_ROWS = 7
# A tiny budget makes a silly multiple (a $1M film earning $400M), so the
# "biggest return" callout only considers movies that cost at least this much.
BEST_RETURN_MIN_BUDGET = 5_000_000


def _f(value):
    """Decimal/int -> float, keeping None."""
    return None if value is None else float(value)


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


# --- Chapter 1: quality -----------------------------------------------------


def _rating_histogram(rows):
    counts = {"movie": {}, "series": {}}
    for row in rows:
        counts[row["kind"]][_f(row["band_start"])] = row["title_count"]
    bands_seen = [band for kind in counts.values() for band in kind]
    if not bands_seen:
        return None
    low, high = min(bands_seen), max(bands_seen)
    bands = [low + 0.5 * i for i in range(int(round((high - low) / 0.5)) + 1)]
    movie = [counts["movie"].get(b, 0) for b in bands]
    series = [counts["series"].get(b, 0) for b in bands]
    return {
        "bands": bands,
        "movie": movie,
        "series": series,
    }


def _histogram_table(histogram):
    """The histogram as rows for the "show the numbers" table."""
    if not histogram:
        return []
    return [
        {"band": band, "movie": movie, "series": series}
        for band, movie, series in zip(histogram["bands"], histogram["movie"], histogram["series"])
    ]


def _quality_finding(kpis):
    movie, series = _f(kpis.get("median_movie_rating")), _f(kpis.get("median_series_rating"))
    if movie is None or series is None:
        return None
    gap = round(series - movie, 1)
    if abs(gap) < RATING_SAME_POINTS:
        kind = "same"
    else:
        kind = "up" if gap > 0 else "down"
    return {"kind": kind, "gap": abs(gap), "movie": movie, "series": series}


# --- Chapter 2: money -------------------------------------------------------


def _callout(row):
    return {
        "slug": row["movie_slug"],
        "title": row["title"],
        "year": row["release_year"],
        "poster_path": row["poster_path"],
        "budget": row["budget"],
        "revenue": row["revenue"],
        "multiple": _f(row["return_multiple"]),
    }


def _return_callouts(pool):
    """(biggest return, most expensive movie) from the usable movies.

    A "lowest return" pick was tried and dropped: box-office data cannot tell a
    flop from a streaming release (Turning Red, Luca, KPop Demon Hunters and
    Frankenstein all sit at the bottom with little or no theatrical revenue), so
    the list would be headlined by a data artifact. The most expensive movie and
    what it earned is a plain fact either way.
    """
    if not pool:
        return None, None
    big = [r for r in pool if r["budget"] >= BEST_RETURN_MIN_BUDGET]
    best = max(big, key=lambda r: r["return_multiple"], default=None)
    priciest = max(pool, key=lambda r: r["budget"])
    return (_callout(best) if best else None), _callout(priciest)


def _return_bands(rows):
    labels, median, count = [], [], []
    series = {"under_1x": [], "from_1x_to_2x": [], "from_2x_to_5x": [], "over_5x": []}
    for row in rows:
        total = row["movie_count"]
        labels.append(row["genre_name"])
        median.append(_f(row["median_multiple"]))
        count.append(total)
        for key, values in series.items():
            values.append(_pct(row[key], total))
    return {"labels": labels, "median": median, "count": count, **series}


def _money(rows):
    pool = rows["budget_vs_revenue.sql"]
    total = len(pool)
    under = sum(1 for r in pool if r["revenue"] < r["budget"])
    over5 = sum(1 for r in pool if r["revenue"] >= 5 * r["budget"])
    best, priciest = _return_callouts(pool)
    bands = rows["return_bands_by_genre.sql"]
    finding = None
    if total:
        finding = {"over_5x_pct": round(100.0 * over5 / total), "under_1x_pct": round(100.0 * under / total)}
    return {
        "has_data": bool(total),
        "movie_count": total,
        "finding": finding,
        # [budget, revenue, title, year, slug] -- arrays, not objects, because
        # this is the heaviest payload on the page (about a thousand points).
        "scatter": [
            [r["budget"], r["revenue"], r["title"], r["release_year"], r["movie_slug"]]
            for r in pool
        ],
        "best": best,
        "priciest": priciest,
        "bands": _return_bands(bands) if bands else None,
        "band_rows": bands,
        "studios": rows["studio_returns.sql"],
    }


# --- Chapter 3: time --------------------------------------------------------


def _runtime_finding(rows):
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    gap = int(last["median_runtime"]) - int(first["median_runtime"])
    if abs(gap) < RUNTIME_SAME_MINUTES:
        kind = "same"
    else:
        kind = "up" if gap > 0 else "down"
    return {"kind": kind, "minutes": abs(gap), "first_decade": first["decade"], "last_decade": last["decade"]}


def _heatmap(rows):
    """A genre x decade grid of shares, rows ordered by how many movies the genre has."""
    if not rows:
        return None
    decades = sorted({r["decade"] for r in rows})
    share = defaultdict(dict)
    weight = Counter()
    for row in rows:
        share[row["genre_name"]][row["decade"]] = _f(row["share_pct"])
        weight[row["genre_name"]] += row["movie_count"]
    peak = max(v for cells in share.values() for v in cells.values()) or 1.0
    grid = []
    for genre in sorted(share, key=lambda g: (-weight[g], g)):
        cells = []
        for decade in decades:
            value = share[genre].get(decade)
            # "v" is the cell's intensity as a preformatted STRING: it lands in
            # a CSS custom property, and a localized "0,42" would be invalid CSS.
            cells.append({
                "pct": value,
                "v": f"{(value / peak if value else 0):.2f}",
            })
        grid.append({"name": genre, "cells": cells})
    return {"decades": decades, "rows": grid}


def _time(rows):
    runtime = rows["runtime_by_decade.sql"]
    non_english = rows["non_english_cinema_over_time.sql"]
    return {
        "has_data": bool(runtime or non_english or rows["genre_share_by_decade.sql"]),
        "finding": _runtime_finding(runtime),
        "runtime": {
            "labels": [r["decade"] for r in runtime],
            "values": [_f(r["median_runtime"]) for r in runtime],
            "counts": [r["movie_count"] for r in runtime],
        } if runtime else None,
        "non_english": {
            "labels": [r["decade"] for r in non_english],
            "values": [_f(r["non_english_pct"]) for r in non_english],
            "counts": [r["film_count"] for r in non_english],
        } if non_english else None,
        "runtime_table": [
            {"decade": r["decade"], "movie_count": r["movie_count"], "median_runtime": _f(r["median_runtime"])}
            for r in runtime
        ],
        "heatmap": _heatmap(rows["genre_share_by_decade.sql"]),
    }


# --- Chapter 4: world -------------------------------------------------------


def _language_name(code, english_name):
    """The language's name in the reader's language where Django knows it."""
    try:
        return get_language_info(code)["name_translated"]
    except KeyError:
        return english_name


def _languages(rows):
    total = sum(r["movie_count"] for r in rows)
    shown = rows[:LANGUAGE_ROWS]
    out = [
        {
            "name": _language_name(r["language_code"], r["language_name"]),
            "count": r["movie_count"],
            "share": _pct(r["movie_count"], total),
        }
        for r in shown
    ]
    rest = total - sum(r["movie_count"] for r in shown)
    if rest > 0:
        out.append({"name": None, "count": rest, "share": _pct(rest, total)})  # "other languages"
    return out


def _world(rows):
    countries = rows["films_by_production_country.sql"]
    languages_rows = rows["original_language_mix.sql"]
    total_languages = sum(r["movie_count"] for r in languages_rows)
    english = next((r["movie_count"] for r in languages_rows if r["language_code"] == "en"), 0)
    finding = None
    if countries and total_languages:
        finding = {
            "english_pct": round(100.0 * english / total_languages),
            "country": countries[0]["country_name"],
            "country_pct": round(_f(countries[0]["film_share_pct"])),
        }
    return {
        "has_data": bool(countries or languages_rows),
        "finding": finding,
        "countries": countries,
        "languages": _languages(languages_rows),
    }


# --- Chapter 5: people ------------------------------------------------------


def craft_labels():
    """Credit job -> reader-facing label, translated at request time."""
    return {
        "Original Music Composer": _("Composer"),
        "Director of Photography": _("Cinematographer"),
        "Editor": _("Editor"),
        "Production Design": _("Production designer"),
        "Screenplay": _("Screenwriter"),
    }


def _people(rows):
    directors = rows["top_rated_directors.sql"]
    partners = rows["signature_partnerships.sql"]
    labels = craft_labels()
    partners = [dict(p, craft_label=labels.get(p["craft"], p["craft"])) for p in partners]
    finding = None
    if directors:
        top = directors[0]
        finding = {
            "name": top["director_name"],
            "rating": _f(top["avg_rating"]),
            "movie_count": top["movie_count"],
        }
    return {
        "has_data": bool(directors or partners),
        "finding": finding,
        "directors": directors,
        "partners": partners,
    }


# --- Chapter 6: TV ----------------------------------------------------------


def _season_finding(rows):
    """How far a show's rating drifts from its own first season.

    The run of seasons that stay within SEASON_HOLD_TOLERANCE of season 1 is
    "holding"; whatever follows is summarised by its average change.
    """
    if len(rows) < 3:
        return None
    points = [(r["season_number"], _f(r["avg_change_from_season_1"])) for r in rows]
    hold = points[0][0]
    for season, change in points:
        if abs(change) >= SEASON_HOLD_TOLERANCE:
            break
        hold = season
    last = points[-1][0]
    if hold == last:
        return {"kind": "holds", "last": last}
    tail = mean(change for season, change in points if season > hold)
    return {
        "kind": "slips" if tail < 0 else "gains",
        "hold": hold,
        "first": hold + 1,
        "last": last,
        "size": abs(tail),
    }


def status_labels():
    """TMDB's show status -> reader-facing label, translated at request time."""
    return {
        "Returning Series": _("Still running"),
        "Ended": _("Ended"),
        "Canceled": _("Cancelled"),
    }


def _tv(rows):
    seasons = rows["season_trajectory.sql"]
    labels = status_labels()
    status = [dict(s, status_label=labels.get(s["status"], s["status"])) for s in rows["series_rating_by_status.sql"]]
    return {
        "has_data": bool(seasons or status or rows["network_quality.sql"]),
        "finding": _season_finding(seasons),
        "seasons": {
            "labels": [r["season_number"] for r in seasons],
            "change": [_f(r["avg_change_from_season_1"]) for r in seasons],
            "rating": [_f(r["avg_rating"]) for r in seasons],
            "shows": [r["show_count"] for r in seasons],
        } if seasons else None,
        "season_table": [
            {
                "season": r["season_number"],
                "shows": r["show_count"],
                "rating": _f(r["avg_rating"]),
                "change": _f(r["avg_change_from_season_1"]),
            }
            for r in seasons
        ],
        "status": status,
        "networks": rows["network_quality.sql"],
    }


# --- Whole page -------------------------------------------------------------


def build(rows):
    """The template context for the dashboard, from `{filename: rows}`.

    `rows` is expected to have had its genre and country names translated
    already (the view does that after the cache read).
    """
    kpis_rows = rows["dashboard_kpis.sql"]
    kpis = kpis_rows[0] if kpis_rows else {}
    histogram = _rating_histogram(rows["rating_distribution.sql"])
    return {
        "kpis": kpis or None,
        "quality": {
            "has_data": histogram is not None,
            "finding": _quality_finding(kpis) if kpis else None,
            "histogram": histogram,
            "table": _histogram_table(histogram),
            "movie_median": _f(kpis.get("median_movie_rating")),
            "series_median": _f(kpis.get("median_series_rating")),
            "agreement": _f(kpis.get("imdb_tmdb_correlation")),
        },
        "money": _money(rows),
        "time": _time(rows),
        "world": _world(rows),
        "people": _people(rows),
        "tv": _tv(rows),
    }
