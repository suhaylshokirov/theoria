"""Task 130: season rating analytics (movies/season_stats.py).

Pure Python over rows already in memory, so plain stand-in objects carry the
few attributes it reads -- no database, no mocks of managers.
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

DJANGO_APP_DIR = Path(__file__).resolve().parent.parent / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))

from movies.season_stats import season_rating_summary  # noqa: E402

TODAY = date(2026, 10, 12)


def _season(number, name=None):
    return SimpleNamespace(season_number=number, name=name or f"Season {number}")


def _ep(number, rating=None, votes=None, name=None, air_date=date(2020, 1, 1)):
    return SimpleNamespace(
        episode_number=number,
        name=name or f"Episode {number}",
        imdb_rating=Decimal(str(rating)) if rating is not None else None,
        imdb_vote_count=votes,
        air_date=air_date,
    )


def _block(number, episodes, name=None):
    return {"season": _season(number, name), "episodes": episodes}


def test_average_is_the_mean_of_rated_episodes_only():
    """An unrated episode is left out, never counted as 0."""
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0, 100), _ep(2, 9.0, 300), _ep(3, None), _ep(4, 7.0, 50)])],
        today=TODAY,
    )
    row = stats["rows"][0]
    assert row["avg"] == pytest.approx(8.0)
    assert (row["low"], row["high"]) == (7.0, 9.0)
    assert row["rated_count"] == 3
    assert row["episode_count"] == 4
    assert row["votes"] == 450
    assert row["best"].episode_number == 2
    assert row["worst"].episode_number == 4
    assert row["few_ratings"] is False


def test_specials_are_listed_but_kept_out_of_the_trend_and_show_average():
    specials = _block(0, [_ep(1, 2.0, 10)], name="Specials")
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0), _ep(2, 8.0), _ep(3, 8.0)]),
         _block(2, [_ep(1, 9.0), _ep(2, 9.0), _ep(3, 9.0)]),
         specials],
        today=TODAY,
    )
    assert [r["number"] for r in stats["rows"]] == [1, 2, 0]
    assert [r["number"] for r in stats["trend_rows"]] == [1, 2]
    assert stats["rows"][2]["is_special"] is True
    assert stats["show"]["avg"] == pytest.approx(8.5)
    assert stats["show"]["rated_count"] == 6
    assert stats["show"]["episode_count"] == 6
    assert [s["number"] for s in stats["chart"]["seasons"]] == [1, 2]
    assert stats["has_overview"] is True


def test_show_average_weights_every_episode_once_not_every_season():
    """A 10-episode season at 8 and a 2-episode season at 9 average 8.17,
    not 8.5: each episode counts once."""
    stats = season_rating_summary(
        [_block(1, [_ep(i, 8.0) for i in range(1, 11)]),
         _block(2, [_ep(1, 9.0), _ep(2, 9.0)])],
        today=TODAY,
    )
    assert stats["show"]["avg"] == pytest.approx(98 / 12)


def test_a_season_with_no_rated_episodes_has_no_average_and_leaves_a_gap():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0), _ep(2, 8.2), _ep(3, 8.4)]),
         _block(2, [_ep(1, None), _ep(2, None)])],
        today=TODAY,
    )
    unrated = stats["rows"][1]
    assert unrated["avg"] is None and unrated["best"] is None
    assert unrated["few_ratings"] is False
    assert stats["chart"]["seasons"][1]["avg"] is None
    # No per-episode series for a season with nothing to plot.
    assert "2" not in stats["chart"]["episodes"]
    # One averaged season is nothing to compare against.
    assert stats["show"]["best_season"] is None
    assert stats["show"]["worst_season"] is None


def test_fewer_than_three_rated_episodes_flags_the_average_as_thin():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0), _ep(2, 8.0), _ep(3, 8.0)]),
         _block(2, [_ep(1, 9.9), _ep(2, None)]),
         _block(3, [_ep(1, 7.0), _ep(2, 7.0), _ep(3, 7.0)])],
        today=TODAY,
    )
    thin = stats["rows"][1]
    assert thin["few_ratings"] is True
    assert stats["chart"]["seasons"][1]["few"] is True
    # The thin 9.9 season is not crowned best over seasons with real samples.
    assert stats["show"]["best_season"]["number"] == 1
    assert stats["show"]["worst_season"]["number"] == 3


def test_a_single_season_show_has_no_overview():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.2), _ep(2, 8.1)])], today=TODAY
    )
    assert stats["has_overview"] is False
    assert list(stats["chart"]["episodes"]) == ["1"]
    points = stats["chart"]["episodes"]["1"]["points"]
    assert [p["rating"] for p in points] == [8.2, 8.1]


def test_a_season_part_aired_is_in_progress():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.0, air_date=date(2026, 9, 1)),
                    _ep(2, None, air_date=date(2026, 10, 30)),
                    _ep(3, None, air_date=None)]),
         _block(2, [_ep(1, 8.0, air_date=date(2020, 1, 1))])],
        today=TODAY,
    )
    assert stats["rows"][0]["in_progress"] is True
    assert stats["rows"][1]["in_progress"] is False


def test_best_episode_tie_goes_to_the_one_more_people_rated():
    stats = season_rating_summary(
        [_block(1, [_ep(1, 9.0, 100), _ep(2, 9.0, 5000), _ep(3, 7.0, 10)])],
        today=TODAY,
    )
    assert stats["rows"][0]["best"].episode_number == 2


def test_chart_data_is_json_serialisable_floats():
    import json

    stats = season_rating_summary(
        [_block(1, [_ep(1, 8.25, 10), _ep(2, 8.0, 20), _ep(3, 7.75, 30)]),
         _block(2, [_ep(1, 9.0, 10)])],
        today=TODAY,
    )
    payload = json.loads(json.dumps(stats["chart"]))
    assert payload["seasons"][0]["avg"] == 8.0
    assert payload["show_avg"] == pytest.approx(8.25)


def test_seasons_known_but_no_episodes_loaded_has_no_overview():
    """Seasons arrive before their episodes (the nightly backfill is capped),
    so a show can list five seasons with nothing in them yet. An overview of
    zeros would say less than the per-season "not catalogued yet" line."""
    stats = season_rating_summary(
        [_block(1, []), _block(2, []), _block(3, [_ep(1, 8.0)])], today=TODAY
    )
    assert stats["has_overview"] is False


def test_no_seasons_at_all():
    stats = season_rating_summary([], today=TODAY)
    assert stats["rows"] == []
    assert stats["has_overview"] is False
    assert stats["show"]["avg"] is None
    assert stats["chart"]["episodes"] == {}
