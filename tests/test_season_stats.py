"""Tasks 130-132: season rating figures (movies/season_stats.py).

Pure Python over rows already in memory, so plain stand-in objects carry the
few attributes it reads -- no database, no mocks of managers.
"""

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

DJANGO_APP_DIR = Path(__file__).resolve().parent.parent / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))

from movies.season_stats import season_rating_summary  # noqa: E402


def _season(number, name=None):
    return SimpleNamespace(season_number=number, name=name or f"Season {number}")


def _ep(number, rating=None, votes=None, name=None, air_date=None):
    return SimpleNamespace(
        episode_number=number,
        name=name or f"Episode {number}",
        imdb_rating=Decimal(str(rating)) if rating is not None else None,
        imdb_vote_count=votes,
        air_date=air_date,
    )


def _block(number, episodes, name=None):
    return {"season": _season(number, name), "episodes": episodes}


def test_season_average_is_the_mean_of_rated_episodes_only():
    """An unrated episode is left out, never counted as 0."""
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0, 100), _ep(2, 9.0, 300), _ep(3, None), _ep(4, 7.0, 50)])]
    )
    point = stats["chart"]["seasons"][0]
    assert point["avg"] == pytest.approx(8.0)
    assert (point["low"], point["high"]) == (7.0, 9.0)
    assert point["rated"] == 3
    assert point["episodes"] == 4
    assert point["votes"] == 450
    assert point["few"] is False


def test_specials_are_never_charted():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0), _ep(2, 8.0), _ep(3, 8.0)]),
         _block(2, [_ep(1, 9.0), _ep(2, 9.0), _ep(3, 9.0)]),
         _block(0, [_ep(1, 2.0, 10)], name="Specials")]
    )
    assert [s["number"] for s in stats["chart"]["seasons"]] == [1, 2]
    assert "0" not in stats["chart"]["episodes"]
    assert stats["chart"]["show_avg"] == pytest.approx(8.5)


def test_show_average_weights_every_episode_once_not_every_season():
    """A 10-episode season at 8 and a 2-episode season at 9 average 8.17,
    not 8.5: each episode counts once."""
    stats = season_rating_summary(
        [_block(1, [_ep(i, 8.0) for i in range(1, 11)]),
         _block(2, [_ep(1, 9.0), _ep(2, 9.0)])]
    )
    assert stats["chart"]["show_avg"] == pytest.approx(round(98 / 12, 2))


def test_a_season_with_no_rated_episodes_leaves_a_gap():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0), _ep(2, 8.2), _ep(3, 8.4)]),
         _block(2, [_ep(1, None), _ep(2, None)]),
         _block(3, [_ep(1, 7.9)])]
    )
    assert stats["chart"]["seasons"][1]["avg"] is None
    # No per-episode series for a season with nothing to plot.
    assert "2" not in stats["chart"]["episodes"]
    assert stats["has_trend"] is True


def test_fewer_than_three_rated_episodes_flags_the_average_as_thin():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0), _ep(2, 8.0), _ep(3, 8.0)]),
         _block(2, [_ep(1, 9.9), _ep(2, None)])]
    )
    assert stats["chart"]["seasons"][1]["few"] is True
    assert stats["chart"]["seasons"][0]["few"] is False


def test_a_single_rated_season_has_no_trend():
    """One season (or one with ratings) gives "All seasons" nothing to
    compare, so the slicer offers no such option."""
    stats = season_rating_summary([_block(1, [_ep(1, 8.2), _ep(2, 8.1)])])
    assert stats["has_trend"] is False
    assert list(stats["chart"]["episodes"]) == ["1"]
    points = stats["chart"]["episodes"]["1"]["points"]
    assert [p["rating"] for p in points] == [8.2, 8.1]


def test_seasons_known_but_no_episodes_loaded_has_no_trend():
    """Seasons arrive before their episodes (the nightly backfill is capped)."""
    stats = season_rating_summary(
        [_block(1, []), _block(2, []), _block(3, [_ep(1, 8.0)])]
    )
    assert stats["has_trend"] is False


def test_chart_data_is_json_serialisable_floats():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.25, 10), _ep(2, 8.0, 20), _ep(3, 7.75, 30)]),
         _block(2, [_ep(1, 9.0, 10)])]
    )
    payload = json.loads(json.dumps(stats["chart"]))
    assert payload["seasons"][0]["avg"] == 8.0
    assert payload["episodes"]["1"]["avg"] == 8.0
    assert payload["show_avg"] == pytest.approx(8.25)


def test_no_seasons_at_all():
    stats = season_rating_summary([])
    assert stats["has_trend"] is False
    assert stats["chart"] == {"show_avg": None, "seasons": [], "episodes": {}}


def test_season_row_carries_its_kpis():
    """Each season's KPI cards: average, best and weakest episode, coverage,
    air span -- unrated episodes left out of every figure."""
    from datetime import date

    stats = season_rating_summary([_block(1, [
        _ep(1, 8.0, 100, air_date=date(2004, 9, 22)),
        _ep(2, 9.0, 300, air_date=date(2004, 9, 29)),
        _ep(3, None, air_date=date(2004, 10, 6)),
        _ep(4, 7.0, 50, air_date=date(2005, 5, 25)),
    ])])
    row = stats["rows"][0]
    assert row["avg"] == pytest.approx(8.0)
    assert row["rated_count"] == 3 and row["episode_count"] == 4
    assert row["best"].episode_number == 2
    assert row["worst"].episode_number == 4
    assert (row["first_air"], row["last_air"]) == (date(2004, 9, 22), date(2005, 5, 25))
    # A row stands in for its block in _episode_table.html.
    assert len(row["episodes"]) == 4


def test_best_episode_tie_goes_to_the_one_more_people_rated():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 9.0, 100), _ep(2, 9.0, 5000), _ep(3, 7.0, 10)])]
    )
    assert stats["rows"][0]["best"].episode_number == 2


def test_show_kpis_name_best_and_weakest_season():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0), _ep(2, 8.0), _ep(3, 8.0)]),
         _block(2, [_ep(1, 9.0), _ep(2, 9.0), _ep(3, 9.0)]),
         _block(3, [_ep(1, 7.0), _ep(2, 7.0), _ep(3, 7.0), _ep(4, None)])]
    )
    show = stats["show"]
    assert show["avg"] == pytest.approx(8.0)
    assert (show["rated_count"], show["episode_count"]) == (9, 10)
    assert show["best_season"]["number"] == 2
    assert show["worst_season"]["number"] == 3


def test_a_thin_season_is_not_crowned_best():
    """A 9.9 season resting on one rating doesn't outrank seasons with a
    real sample."""
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0), _ep(2, 8.0), _ep(3, 8.0)]),
         _block(2, [_ep(1, 9.9), _ep(2, None)]),
         _block(3, [_ep(1, 7.0), _ep(2, 7.0), _ep(3, 7.0)])]
    )
    assert stats["rows"][1]["few_ratings"] is True
    assert stats["show"]["best_season"]["number"] == 1
    assert stats["show"]["worst_season"]["number"] == 3


def test_one_rated_season_names_no_best_or_weakest():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0), _ep(2, 8.4), _ep(3, 8.2)]), _block(2, [_ep(1, None)])]
    )
    assert stats["show"]["best_season"] is None
    assert stats["show"]["worst_season"] is None

