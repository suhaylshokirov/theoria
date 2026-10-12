"""Season rating figures for a show page (Tasks 130-132).

Turns the {season, episodes} blocks _build_seasons() already loads into
everything the Episodes section shows besides the episode rows themselves:

* one row per season -- the "All seasons" table and each season's KPI cards
  (average, best and weakest episode, rated coverage, air span, votes);
* the show-wide KPI cards (episode average, best and weakest season);
* the JSON the area chart draws from.

Pure Python over rows already in memory -- no query of its own, so it adds
nothing to the page's warehouse cost.

The rules an analyst would ask about, in one place:

* An average is the plain mean of *rated* episodes, each episode counting once
  (a viewer watches every episode, not every vote). An unrated or unaired
  episode is left out -- never counted as 0.
* Specials (season 0) are not part of the run: _build_seasons() already
  drops them, and they are skipped here too, defensively.
* Fewer than FEW_RATINGS rated episodes flags a season's average as thin; a
  thin season can't be named best or weakest while seasons with a real
  sample exist.
"""

FEW_RATINGS = 3


def _rating(episode):
    return float(episode.imdb_rating) if episode.imdb_rating is not None else None


def _round(value):
    return round(value, 2) if value is not None else None


def _best_and_worst(rated):
    """(best, weakest) episode of a season. Ties go to the episode more people
    voted on, then to the earlier one -- the reading that is least a fluke."""
    best = max(rated, key=lambda ep: (_rating(ep), ep.imdb_vote_count or 0, -(ep.episode_number or 0)))
    worst = min(rated, key=lambda ep: (_rating(ep), -(ep.imdb_vote_count or 0), ep.episode_number or 0))
    return best, worst


def _season_row(block):
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
        "few_ratings": 0 < len(rated) < FEW_RATINGS,
    }
    if rated:
        row["avg"] = sum(ratings) / len(ratings)
        row["low"] = min(ratings)
        row["high"] = max(ratings)
        row["best"], row["worst"] = _best_and_worst(rated)
    return row


def season_rating_summary(seasons):
    """Every figure the Episodes section shows, from _build_seasons() output.

    Returns {"rows", "show", "has_trend", "chart"}:
      rows      -- one dict per season, in order (see _season_row)
      show      -- show-wide KPIs: episode average, rated/total episodes,
                   best and weakest season (None when nothing to compare)
      has_trend -- True when 2+ seasons carry a rated episode: the slicer then
                   offers "All seasons" (and opens on it); otherwise the page
                   opens on its first season
      chart     -- JSON-ready (floats, no Decimals) for the area chart:
                   {"show_avg", "seasons": [...], "episodes": {"<n>": {...}}}
    """
    blocks = [b for b in seasons if b["season"].season_number != 0]
    rows = [_season_row(b) for b in blocks]

    rated_eps = [
        _rating(ep)
        for b in blocks
        for ep in b["episodes"]
        if ep.imdb_rating is not None
    ]
    averaged = [r for r in rows if r["avg"] is not None]
    comparable = [r for r in averaged if not r["few_ratings"]] or averaged
    best_season = worst_season = None
    if len(comparable) >= 2:
        best_season = max(comparable, key=lambda r: r["avg"])
        worst_season = min(comparable, key=lambda r: r["avg"])

    show_avg = sum(rated_eps) / len(rated_eps) if rated_eps else None
    show = {
        "avg": show_avg,
        "rated_count": len(rated_eps),
        "episode_count": sum(r["episode_count"] for r in rows),
        "best_season": best_season,
        "worst_season": worst_season,
    }

    chart = {
        "show_avg": _round(show_avg),
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
            for r in rows
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
                    for ep in r["episodes"]
                ],
            }
            for r in rows
            if r["rated_count"]
        },
    }

    return {
        "rows": rows,
        "show": show,
        "has_trend": len(averaged) >= 2,
        "chart": chart,
    }
