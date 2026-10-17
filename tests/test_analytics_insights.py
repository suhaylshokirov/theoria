"""Task 133: the analytics dashboard's shaping, findings and formatting.

`analytics.insights` is pure Python over plain rows, so these tests need no
database and no request: they feed it rows and read the numbers back.
"""

import os
import re
import sys
from decimal import Decimal
from pathlib import Path

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from django.utils import translation  # noqa: E402

from analytics import insights  # noqa: E402
from analytics.cached_reads import DASHBOARD_QUERIES, QUERIES_DIR  # noqa: E402
from analytics.templatetags.dash_format import compact_money  # noqa: E402
from tests.dashboard_fixtures import empty_rows, full_rows  # noqa: E402


# --- The query set --------------------------------------------------------------


def test_every_dashboard_query_exists_and_names_its_columns():
    assert len(DASHBOARD_QUERIES) == len(set(DASHBOARD_QUERIES))
    for filename in DASHBOARD_QUERIES:
        sql = (QUERIES_DIR / filename).read_text(encoding="utf-8")
        assert sql.strip().upper().startswith(("--", "WITH", "SELECT")), filename
        # Project rule: never SELECT * in app code.
        assert not re.search(r"select\s+\*", sql, re.I), filename
        assert not re.search(r",\s*\w+\.\*", sql), filename


def test_fixture_rows_cover_exactly_the_dashboard_queries():
    assert set(full_rows()) == set(DASHBOARD_QUERIES) == set(empty_rows())


# --- Chapter 1: ratings ---------------------------------------------------------


def test_histogram_zero_fills_bands_so_movies_and_shows_share_an_axis():
    hist = insights.build(full_rows())["quality"]["histogram"]
    assert hist["bands"] == [7.0, 7.5, 8.0]
    assert hist["movie"] == [307, 276, 0]
    assert hist["series"] == [0, 0, 180]
    assert insights.build(full_rows())["quality"]["table"][0] == {"band": 7.0, "movie": 307, "series": 0}


def test_quality_finding_up_down_same():
    def finding(movie, series):
        kpis = {"median_movie_rating": Decimal(movie), "median_series_rating": Decimal(series)}
        return insights._quality_finding(kpis)

    assert finding("7.3", "7.9") == {"kind": "up", "gap": 0.6, "movie": 7.3, "series": 7.9}
    assert finding("8.0", "7.5")["kind"] == "down"
    assert finding("7.30", "7.34")["kind"] == "same"
    assert insights._quality_finding({"median_movie_rating": None, "median_series_rating": Decimal("7")}) is None


# --- Chapter 2: money -----------------------------------------------------------


def test_money_shares_come_from_the_usable_movies():
    money = insights.build(full_rows())["money"]
    assert money["movie_count"] == 4
    # over 5x: cheap-hit (200x) and big-hit (80x) = 2 of 4; under 1x: small-flop = 1 of 4
    assert money["finding"] == {"over_5x_pct": 50, "under_1x_pct": 25}
    assert money["scatter"][0] == [2_000_000, 1_000_000, "Small Flop", 2024, "small-flop"]


def test_best_return_ignores_micro_budgets_and_priciest_is_the_largest_budget():
    money = insights.build(full_rows())["money"]
    # cheap-hit returned 200x but cost only $1.5M: under the $5M callout floor.
    assert money["best"]["slug"] == "big-hit"
    assert money["best"]["multiple"] == 80.0
    assert money["priciest"]["slug"] == "epic"
    assert money["priciest"]["budget"] == 650_000_000


def test_money_with_nothing_loaded_has_no_finding_and_no_callouts():
    money = insights.build(empty_rows())["money"]
    assert money["has_data"] is False
    assert money["finding"] is None
    assert money["best"] is None and money["priciest"] is None
    assert money["bands"] is None and money["scatter"] == []


def test_return_bands_are_percentages_of_each_genre():
    bands = insights.build(full_rows())["money"]["bands"]
    assert bands["labels"] == ["Action"]
    assert bands["count"] == [40]
    assert bands["under_1x"] == [5.0]
    assert bands["from_1x_to_2x"] == [15.0]
    assert bands["from_2x_to_5x"] == [40.0]
    assert bands["over_5x"] == [40.0]
    assert bands["median"] == [4.02]


# --- Chapter 3: time ------------------------------------------------------------


def test_runtime_finding_direction_and_size():
    def rows(first, last):
        return [{"decade": 1970, "median_runtime": Decimal(first)}, {"decade": 2020, "median_runtime": Decimal(last)}]

    assert insights._runtime_finding(rows("110", "116")) == {
        "kind": "up", "minutes": 6, "first_decade": 1970, "last_decade": 2020}
    assert insights._runtime_finding(rows("120", "108"))["kind"] == "down"
    assert insights._runtime_finding(rows("110", "112"))["kind"] == "same"
    assert insights._runtime_finding(rows("110", "112")[:1]) is None  # one decade is not a trend


def test_heatmap_orders_genres_by_size_and_blanks_missing_cells():
    heat = insights.build(full_rows())["time"]["heatmap"]
    assert heat["decades"] == [1970, 2020]
    assert [row["name"] for row in heat["rows"]] == ["Action", "Drama"]  # 136 movies vs 90
    drama = heat["rows"][1]["cells"]
    assert drama[0]["pct"] == 44.8
    assert drama[1]["pct"] is None and drama[1]["v"] == "0.00"
    # The busiest cell is the full intensity.
    assert max(float(c["v"]) for row in heat["rows"] for c in row["cells"]) == 1.0


def test_heatmap_intensity_is_a_plain_dot_decimal_in_every_language():
    # It lands in a CSS custom property: "0,45" would be invalid CSS.
    for lang in ("en", "ru", "uz"):
        with translation.override(lang):
            heat = insights.build(full_rows())["time"]["heatmap"]
        for row in heat["rows"]:
            for cell in row["cells"]:
                assert re.fullmatch(r"\d\.\d\d", cell["v"]), (lang, cell["v"])


# --- Chapter 4: world -----------------------------------------------------------


def test_world_finding_and_language_rows():
    world = insights.build(full_rows())["world"]
    assert world["finding"] == {"english_pct": 90, "country": "Japan", "country_pct": 1}
    assert [(lang["name"], lang["count"], lang["share"]) for lang in world["languages"]] == [
        ("English", 90, 90.0), ("Japanese", 10, 10.0)]


def test_languages_beyond_the_first_few_fold_into_other():
    rows = [{"language_code": f"x{i}", "language_name": f"L{i}", "movie_count": 100 - i} for i in range(10)]
    langs = insights._languages(rows)
    assert len(langs) == insights.LANGUAGE_ROWS + 1
    other = langs[-1]
    assert other["name"] is None  # the template says "Other languages"
    assert other["count"] == sum(100 - i for i in range(insights.LANGUAGE_ROWS, 10))
    assert sum(lang["count"] for lang in langs) == sum(r["movie_count"] for r in rows)


def test_language_names_follow_the_readers_language_where_django_knows_them():
    with translation.override("en"):
        assert insights._language_name("fr", "French") == "French"
    with translation.override("ru"):
        assert insights._language_name("fr", "French").lower() == "французский"
    # An unknown code keeps the warehouse's English name rather than failing.
    assert insights._language_name("zz", "Zzish") == "Zzish"


# --- Chapter 5 and labels -------------------------------------------------------


def test_people_rows_carry_translated_craft_labels():
    with translation.override("ru"):
        people = insights.build(full_rows())["people"]
    assert people["partners"][0]["craft_label"] == "Композитор"
    assert people["partners"][0]["craft"] == "Original Music Composer"  # the raw value stays for code
    assert people["finding"] == {"name": "Test Director", "rating": 8.2, "movie_count": 5}


def test_unknown_craft_and_status_fall_back_to_the_raw_value():
    rows = full_rows()
    rows["signature_partnerships.sql"][0]["craft"] = "Gaffer"
    rows["series_rating_by_status.sql"][0]["status"] = "Pilot"
    built = insights.build(rows)
    assert built["people"]["partners"][0]["craft_label"] == "Gaffer"
    assert built["tv"]["status"][0]["status_label"] == "Pilot"


# --- Chapter 6: TV --------------------------------------------------------------


def _seasons(*changes):
    return [
        {"season_number": i + 1, "avg_change_from_season_1": Decimal(str(c))}
        for i, c in enumerate(changes)
    ]


def test_season_finding_holds_the_whole_way():
    assert insights._season_finding(_seasons(0, 0.02, -0.03, 0.05)) == {"kind": "holds", "last": 4}


def test_season_finding_slips_after_the_hold():
    finding = insights._season_finding(_seasons(0, -0.02, -0.2, -0.3))
    assert finding["kind"] == "slips"
    assert (finding["hold"], finding["first"], finding["last"]) == (2, 3, 4)
    assert round(finding["size"], 2) == 0.25  # mean of -0.2 and -0.3


def test_season_finding_gains_after_the_hold():
    finding = insights._season_finding(_seasons(0, 0.05, 0.3, 0.4))
    assert finding["kind"] == "gains" and finding["hold"] == 2


def test_season_finding_needs_three_seasons():
    assert insights._season_finding(_seasons(0, -0.5)) is None
    assert insights._season_finding([]) is None


def test_status_labels_are_translated():
    with translation.override("ru"):
        tv = insights.build(full_rows())["tv"]
    assert [s["status_label"] for s in tv["status"]] == ["Завершён", "Продолжается"]
    assert tv["season_table"][0] == {"season": 1, "shows": 300, "rating": 7.66, "change": 0.0}


# --- Whole page -----------------------------------------------------------------


def test_an_empty_warehouse_builds_without_findings_or_errors():
    built = insights.build(empty_rows())
    assert built["kpis"] is None
    for chapter in ("quality", "money", "time", "world", "people", "tv"):
        assert built[chapter]["has_data"] is False, chapter
        assert built[chapter]["finding"] is None, chapter
    assert built["time"]["heatmap"] is None
    assert built["tv"]["seasons"] is None


# --- Money formatting -----------------------------------------------------------


def test_compact_money_in_english():
    with translation.override("en"):
        assert compact_money(45_411_061_972) == "$45.4B"
        assert compact_money(797_307_407) == "$797.3M"
        assert compact_money(12_000) == "$12K"
        assert compact_money(950) == "$950"
        assert compact_money(Decimal("1000000")) == "$1.0M"
        assert compact_money(None) == "—"


def test_compact_money_uses_the_translated_suffix_and_a_dot():
    with translation.override("ru"):
        text = compact_money(45_411_061_972)
    assert text.startswith("$45.4") and "млрд" in text
