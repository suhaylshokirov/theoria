"""Season rating chart data for a show page (Tasks 130-131).

Turns the {season, episodes} blocks _build_seasons() already loads into the
JSON the Episodes section's area chart draws from: one point per season (its
average episode rating) for the "All seasons" trend, and one point per
episode for each season. Pure Python over rows already in memory -- no query
of its own, so it adds nothing to the page's warehouse cost.

The rules an analyst would ask about, in one place:

* An average is the plain mean of *rated* episodes, each episode counting once
  (a viewer watches every episode, not every vote). An unrated or unaired
  episode is left out -- never counted as 0.
* Specials (season 0) are not part of the run: _build_seasons() already
  drops them, and they are skipped here too, defensively.
* Fewer than FEW_RATINGS rated episodes flags a season's average as thin
  (the tooltip says so).
"""

FEW_RATINGS = 3


def _rating(episode):
    return float(episode.imdb_rating) if episode.imdb_rating is not None else None


def _round(value):
    return round(value, 2) if value is not None else None


def _season_point(block):
    season = block["season"]
    episodes = block["episodes"]
    ratings = [_rating(ep) for ep in episodes if ep.imdb_rating is not None]
    return {
        "number": season.season_number,
        "name": season.name or "",
        "avg": _round(sum(ratings) / len(ratings)) if ratings else None,
        "low": min(ratings) if ratings else None,
        "high": max(ratings) if ratings else None,
        "rated": len(ratings),
        "episodes": len(episodes),
        "votes": sum(ep.imdb_vote_count or 0 for ep in episodes if ep.imdb_rating is not None),
        "few": 0 < len(ratings) < FEW_RATINGS,
    }


def season_rating_summary(seasons):
    """The chart's data, from _build_seasons() output.

    Returns {"has_trend", "chart"}:
      has_trend -- True when 2+ seasons carry a rated episode, i.e. an
                   "All seasons" trend has something to compare; otherwise
                   the slicer offers no "All seasons" option
      chart     -- JSON-ready (floats, no Decimals):
                   {"show_avg", "seasons": [...], "episodes": {"<n>": {...}}}
    """
    blocks = [b for b in seasons if b["season"].season_number != 0]
    points = [_season_point(b) for b in blocks]

    rated = [
        _rating(ep)
        for b in blocks
        for ep in b["episodes"]
        if ep.imdb_rating is not None
    ]

    episodes = {}
    for point, block in zip(points, blocks):
        if not point["rated"]:
            continue
        episodes[str(point["number"])] = {
            "avg": point["avg"],
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

    return {
        "has_trend": sum(1 for p in points if p["rated"]) >= 2,
        "chart": {
            "show_avg": _round(sum(rated) / len(rated)) if rated else None,
            "seasons": points,
            "episodes": episodes,
        },
    }
