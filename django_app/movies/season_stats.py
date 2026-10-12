"""Season rating analytics for a show page (Task 130).

Turns the {season, episodes} blocks _build_seasons() already loads into the
figures the Episodes section reads: one row per season (average, range, best
and weakest episode, coverage) plus the show-wide episode average, and the
JSON the area chart draws from. Pure Python over rows already in memory -- no
query of its own, so it adds nothing to the page's warehouse cost.

The rules an analyst would ask about, in one place:

* An average is the plain mean of *rated* episodes, each episode counting once
  (a viewer watches every episode, not every vote). An unrated or unaired
  episode is left out -- never counted as 0.
* Specials (season 0) keep their own row but stay out of the season trend and
  the show-wide average: they are extras, not part of the run.
* Fewer than FEW_RATINGS rated episodes flags an average as thin.
"""

from datetime import date

FEW_RATINGS = 3


def _rating(episode):
    return float(episode.imdb_rating) if episode.imdb_rating is not None else None


def _best_and_worst(rated):
    """(best, weakest) episode of a season. Ties go to the episode more people
    voted on, then to the earlier one -- the reading that is least a fluke."""
    def key(ep):
        return (_rating(ep), ep.imdb_vote_count or 0, -(ep.episode_number or 0))

    best = max(rated, key=key)
    worst = min(rated, key=lambda ep: (_rating(ep), -(ep.imdb_vote_count or 0), ep.episode_number or 0))
    return best, worst


def _season_row(block, today):
    season = block["season"]
    episodes = block["episodes"]
    rated = [ep for ep in episodes if ep.imdb_rating is not None]
    ratings = [_rating(ep) for ep in rated]
    dates = [ep.air_date for ep in episodes if ep.air_date]

    row = {
        "season": season,
        # Carried through so a row can stand in for its {season, episodes}
        # block -- _episode_table.html reads block.episodes.
        "episodes": episodes,
        "number": season.season_number,
        "is_special": season.season_number == 0,
        "episode_count": len(episodes),
        "rated_count": len(rated),
        "avg": None,
        "low": None,
        "high": None,
        "votes": sum(ep.imdb_vote_count or 0 for ep in rated),
        "best": None,
        "worst": None,
        "first_air": min(dates) if dates else None,
        "last_air": max(dates) if dates else None,
        # Some episodes have aired and some have not (or have no date yet):
        # the season's average is still moving.
        "in_progress": bool(dates)
        and any(d <= today for d in dates)
        and any(ep.air_date is None or ep.air_date > today for ep in episodes),
        "few_ratings": 0 < len(rated) < FEW_RATINGS,
    }
    if rated:
        row["avg"] = sum(ratings) / len(ratings)
        row["low"] = min(ratings)
        row["high"] = max(ratings)
        row["best"], row["worst"] = _best_and_worst(rated)
    return row


def _round(value):
    return round(value, 2) if value is not None else None


def season_rating_summary(seasons, today=None):
    """Every figure the Episodes section shows, from _build_seasons() output.

    Returns {"rows", "trend_rows", "show", "has_overview", "has_few_ratings",
    "chart"}:
      rows        -- one dict per season, in the page's order (specials last)
      trend_rows  -- rows minus specials: what the trend and show average use
      show        -- show-wide episode average, best/weakest season, coverage
      has_overview-- True when 2+ regular seasons have episodes catalogued,
                     i.e. an "All seasons" view has a trend to show; a
                     one-season show -- or one whose episodes haven't been
                     loaded yet -- goes straight to its season panels
      chart       -- JSON-ready data for the area chart (floats, no Decimals)
    """
    today = today or date.today()
    rows = [_season_row(block, today) for block in seasons]
    trend_rows = [r for r in rows if not r["is_special"]]

    rated_eps = [
        _rating(ep)
        for block in seasons
        if block["season"].season_number != 0
        for ep in block["episodes"]
        if ep.imdb_rating is not None
    ]
    averaged = [r for r in trend_rows if r["avg"] is not None]
    # A best/weakest season is only worth naming against seasons whose
    # average rests on enough episodes; fall back to every averaged season
    # when none clears the bar, and name neither when there's nothing to
    # compare against.
    comparable = [r for r in averaged if not r["few_ratings"]] or averaged
    best_season = worst_season = None
    if len(comparable) >= 2:
        best_season = max(comparable, key=lambda r: r["avg"])
        worst_season = min(comparable, key=lambda r: r["avg"])

    show = {
        "avg": sum(rated_eps) / len(rated_eps) if rated_eps else None,
        "rated_count": len(rated_eps),
        "episode_count": sum(r["episode_count"] for r in trend_rows),
        "best_season": best_season,
        "worst_season": worst_season,
    }

    chart = {
        "show_avg": _round(show["avg"]),
        "seasons": [
            {
                "number": r["number"],
                "name": r["season"].name or "",
                "avg": _round(r["avg"]),
                "low": r["low"],
                "high": r["high"],
                "rated": r["rated_count"],
                "episodes": r["episode_count"],
                "votes": r["votes"],
                "few": r["few_ratings"],
            }
            for r in trend_rows
        ],
        "episodes": {
            str(r["number"]): {
                "avg": _round(r["avg"]),
                "points": [
                    {
                        "number": ep.episode_number,
                        "name": ep.name or "",
                        "rating": _rating(ep),
                        "votes": ep.imdb_vote_count or 0,
                    }
                    for ep in block["episodes"]
                ],
            }
            for r, block in zip(rows, seasons)
            if r["rated_count"]
        },
    }

    return {
        "rows": rows,
        "trend_rows": trend_rows,
        "show": show,
        "has_overview": sum(1 for r in trend_rows if r["episode_count"]) >= 2,
        # The chart key names the hollow "few ratings" point only when one is drawn.
        "has_few_ratings": any(r["few_ratings"] for r in trend_rows),
        "chart": chart,
    }
