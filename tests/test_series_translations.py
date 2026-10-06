"""Tests for Task 109: Russian (and Uzbek) text for TV shows.

The TV counterpart of the movie half of tests/test_translations.py and
tests/test_i18n.py. Everything is in-memory: S3, the SQLAlchemy session and the
warehouse managers are mocked. Nothing touches a network or a database.
"""

from __future__ import annotations

import datetime as dt
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from django.utils import translation

import etl.warehouse_loader.load_dimensions as load_dimensions_module
from data_quality.warehouse_checks import check_fk_integrity
from etl import s3_utils
from etl.bronze.ingest_series_details import ingest_series_details
from etl.silver.transform_series_translations import (
    _extract_translation_rows,
    transform_series_translations,
)
from etl.warehouse_loader.load_dimensions import load_series_translation
# Imported first: these modules put django_app on sys.path and call django.setup(),
# which the `movies` imports below depend on.
from tests.test_django_views import (  # noqa: F401  (also runs django.setup())
    client, setup_module, teardown_module, _series, _series_detail_mocks,
)
from tests.test_etl import _make_s3_mock_with_files, _series_detail
from tests.test_i18n import _get, _sql
from tests.test_translations import _entry, _parquet_from_last_put, _translations_block

from movies import i18n  # noqa: E402
from movies.models import CountryTranslation, Genre, GenreTranslation, Series  # noqa: E402

DATE = dt.date(2026, 10, 6)
RU_BB = _entry(
    "ru", "RU", name="Во все тяжкие", overview="Школьный учитель химии...",
    tagline="", homepage="",
)
UZ_BB = _entry("uz", "UZ", name="", overview="", tagline="", homepage="")
DE_BB = _entry("de", "DE", name="Breaking Bad", overview="Ein Lehrer", tagline="", homepage="")


def _series_with_translations(series_id: int, *entries: dict) -> dict:
    raw = _series_detail(series_id)
    raw["translations"] = _translations_block(*entries)
    return raw


@pytest.fixture(autouse=True)
def _no_real_vocabulary_reads():
    """Under ru the views read genre/country vocabularies; keep them off the warehouse."""
    with patch.object(GenreTranslation, "objects", new=MagicMock()) as genres, \
            patch.object(CountryTranslation, "objects", new=MagicMock()) as countries:
        genres.using.return_value.filter.return_value.values_list.return_value = []
        countries.using.return_value.filter.return_value.values_list.return_value = []
        yield


# --- Bronze ------------------------------------------------------------------

def test_ingest_series_details_appends_translations_and_trims_to_shipped_languages():
    mock_client = MagicMock()
    mock_client.get_series_details.return_value = _series_with_translations(
        1396, RU_BB, DE_BB, UZ_BB
    )
    mock_s3 = MagicMock()

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ingest_series_details([1396], ingestion_date=DATE, client=mock_client)

    assert "translations" in mock_client.get_series_details.call_args.kwargs["append_to_response"].split(",")
    written = json.loads(mock_s3.put_object.call_args[1]["Body"])
    assert [e["iso_639_1"] for e in written["translations"]["translations"]] == ["ru", "uz"]
    # The rest of the payload is untouched.
    assert written["aggregate_credits"] == {"cast": [], "crew": []}


# --- Silver ------------------------------------------------------------------

def test_extract_rows_one_row_per_language_with_text():
    rows = _extract_translation_rows(_series_with_translations(1396, RU_BB, UZ_BB, DE_BB))
    # uz has no text at all, de is not shipped.
    assert [(r["series_id"], r["lang"], r["name"]) for r in rows] == [(1396, "ru", "Во все тяжкие")]
    assert rows[0]["tagline"] is None                      # "" became null


def test_extract_rows_none_for_a_pre_task_109_payload():
    assert _extract_translation_rows(_series_detail(1396)) is None


def test_transform_series_translations_writes_silver_parquet():
    key = "bronze/series_details/ingestion_date=2026-10-06/1396.json"
    mock_s3 = _make_s3_mock_with_files({key: _series_with_translations(1396, RU_BB)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uri = transform_series_translations(ingestion_date=DATE, bucket="theoria-datalake")

    assert uri == (
        "s3://theoria-datalake/silver/series_translations/ingestion_date=2026-10-06/"
        "series_translations.parquet"
    )
    df = _parquet_from_last_put(mock_s3)
    assert list(df.columns) == ["series_id", "lang", "name", "overview", "tagline"]
    assert df.loc[0, "name"] == "Во все тяжкие"


def test_transform_series_translations_old_partition_writes_empty_wellformed_parquet(caplog):
    payloads = {
        f"bronze/series_details/ingestion_date=2026-10-06/{sid}.json": _series_detail(sid)
        for sid in (1, 2, 3)
    }
    mock_s3 = _make_s3_mock_with_files(payloads)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with caplog.at_level("WARNING"):
            transform_series_translations(ingestion_date=DATE, bucket="theoria-datalake")

    df = _parquet_from_last_put(mock_s3)
    assert len(df) == 0
    assert list(df.columns) == ["series_id", "lang", "name", "overview", "tagline"]
    assert sum("had no `translations` key" in r.message for r in caplog.records) == 1


def test_transform_series_translations_raises_when_no_bronze_files():
    mock_s3 = _make_s3_mock_with_files({})
    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with pytest.raises(FileNotFoundError):
            transform_series_translations(ingestion_date=DATE, bucket="theoria-datalake")


# --- warehouse loader / checks -----------------------------------------------

def _series_df(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["series_id", "lang", "name", "overview", "tagline"]).astype(
        {"series_id": "Int64"}
    )


def test_load_series_translation_replaces_by_parent_and_quarantines(monkeypatch):
    monkeypatch.setattr(load_dimensions_module, "_existing_ids", lambda s, t, c: {1})
    session = MagicMock()
    df = _series_df([
        (1, "ru", "Н", "О", None),
        (999, "ru", "X", "Y", None),            # no dim_series row
        (1, "de", "Name", "E", None),           # a language we don't ship
        (1, "uz", None, None, None),            # no text at all
    ])

    count, rejects = load_series_translation(session, df, DATE)

    assert count == 1
    assert [r["rejection_reason"] for r in rejects] == [
        "unknown series_id", "unsupported lang 'de'", "no translated text",
    ]
    calls = session.execute.call_args_list
    assert "DELETE FROM series_translation WHERE series_id = ANY(:parent_ids)" in str(calls[0][0][0])
    assert [(r["series_id"], r["lang"]) for r in calls[1][0][1]] == [(1, "ru")]


def test_check_fk_integrity_covers_series_translation():
    session = MagicMock()
    session.execute.return_value.scalar.return_value = 0
    checks = {r.check for r in check_fk_integrity(session)}
    assert "fk:series_translation.series_id->dim_series.series_id" in checks


# --- the model and the i18n helpers ------------------------------------------

def test_display_helpers_fall_back_to_english_when_not_annotated():
    show = Series(series_id=1, name="Breaking Bad", overview="A teacher.", tagline="Tread lightly.")
    assert show.display_title == "Breaking Bad"
    assert show.display_overview == "A teacher."
    assert show.display_tagline == "Tread lightly."
    assert not show.prose_is_fallback


def test_display_helpers_read_the_annotations():
    show = Series(series_id=1, name="Breaking Bad", overview="A teacher.", tagline="Tread lightly.")
    show.name_i18n, show.overview_i18n, show.tagline_i18n = "Во все тяжкие", "Учитель.", None
    show.overview_tr, show.tagline_tr = "Учитель.", None
    assert show.display_title == "Во все тяжкие"
    assert show.display_overview == "Учитель."
    assert show.display_tagline == "Tread lightly."         # no translation -> English
    assert show.tagline_is_fallback and not show.overview_is_fallback


def test_english_requests_are_not_touched():
    queryset = MagicMock()
    with translation.override("en"):
        assert i18n.localize_series(queryset) is queryset
    queryset.annotate.assert_not_called()


def test_localize_series_builds_a_per_language_subquery_with_english_fallback():
    with translation.override("ru"):
        sql, params = _sql(i18n.localize_series(Series.objects.using("warehouse")))
    assert "series_translation" in sql
    assert "COALESCE" in sql
    assert "ru" in params


def test_series_name_sort_and_search_follow_the_language():
    with translation.override("en"):
        assert i18n.series_name_order("name", "ENGLISH") == "ENGLISH"
        assert i18n.series_name_order("rating", "ENGLISH") == "ENGLISH"
    with translation.override("ru"):
        assert i18n.series_name_order("rating", "ENGLISH") == "ENGLISH"
        assert str(i18n.series_name_order("name", "ENGLISH")) != "ENGLISH"
        queryset = i18n.localize_series(Series.objects.using("warehouse"))
        sql, _ = _sql(i18n.filter_by_series_name(queryset, "Тяжкие"))
        assert sql.count("LIKE") >= 2                       # translated OR English name


# --- the show page -----------------------------------------------------------

def _translated_show(**overrides):
    show = _series(name="Breaking Bad", slug="breaking-bad")
    show.original_name = "Breaking Bad"
    show.overview = "A chemistry teacher."
    show.tagline = "Tread lightly."
    attrs = {
        "name_tr": "Во все тяжкие", "name_i18n": "Во все тяжкие",
        "overview_tr": "Школьный учитель химии.", "overview_i18n": "Школьный учитель химии.",
        "tagline_tr": None, "tagline_i18n": "Tread lightly.",
    }
    attrs.update(overrides)
    for k, v in attrs.items():
        setattr(show, k, v)
    return show


def test_russian_show_page_shows_the_russian_text_and_english_fallback_marker():
    show = _translated_show()
    with _series_detail_mocks(show):
        body = _get("ru", "/tv/breaking-bad/").content.decode()
    assert "Во все тяжкие" in body
    assert "Школьный учитель химии." in body
    assert "A chemistry teacher." not in body
    # The tagline had no Russian text, so English stands in, flagged.
    assert 'lang="en">Tread lightly.' in body
    assert 'class="prose-note"' in body
    # The English name is kept as the "originally" line.
    assert "Breaking Bad" in body


def test_fully_translated_show_page_has_no_marker():
    show = _translated_show(tagline_tr="Осторожно.", tagline_i18n="Осторожно.")
    with _series_detail_mocks(show):
        body = _get("ru", "/tv/breaking-bad/").content.decode()
    assert "Осторожно." in body
    assert 'class="prose-note"' not in body


def test_english_show_page_is_unchanged_and_has_no_marker():
    show = _series(name="Breaking Bad", slug="breaking-bad")
    show.overview = "A chemistry teacher."
    with _series_detail_mocks(show):
        body = _get("", "/tv/breaking-bad/").content.decode()
    assert "A chemistry teacher." in body
    assert 'class="prose-note"' not in body


# --- the lists ---------------------------------------------------------------

def test_russian_series_search_matches_the_translated_or_english_name():
    with patch.object(Series, "objects", new=MagicMock()) as series_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr, patch("movies.views.filter_by_series_name") as search:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = []
        qs = series_mgr.using.return_value.all.return_value
        qs.annotate.return_value = qs
        qs.order_by.return_value = [_translated_show()]
        search.side_effect = lambda queryset, term: queryset

        response = _get("ru", "/tv/?q=Тяжкие")

    assert response.status_code == 200
    assert search.call_args.args[1] == "Тяжкие"
    assert "Во все тяжкие" in response.content.decode()


def test_english_series_search_is_the_plain_name_filter():
    with patch.object(Series, "objects", new=MagicMock()) as series_mgr, patch.object(
        Genre, "objects", new=MagicMock()
    ) as genre_mgr:
        genre_mgr.using.return_value.annotate.return_value.filter.return_value \
            .order_by.return_value.values_list.return_value = []
        qs = series_mgr.using.return_value.all.return_value
        qs.filter.return_value = qs
        qs.annotate.return_value = qs
        qs.order_by.return_value = []

        _get("", "/tv/?q=bad")

    qs.filter.assert_called_once_with(name__icontains="bad")


def test_cartoon_card_shows_the_translated_show_name():
    from tests.test_django_views import _cartoon_mocks
    from movies.models import Movie

    show = _translated_show()
    # The localize_* annotate() calls would change the mocked queryset chain
    # _cartoon_mocks wires; the fixture show already carries its annotations.
    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Series, "objects", new=MagicMock()
    ) as series_mgr, patch("movies.views.localize_movies", side_effect=lambda qs: qs), patch(
        "movies.views.localize_series", side_effect=lambda qs: qs
    ):
        _cartoon_mocks(movie_mgr, series_mgr, movies=[], series=[show])
        body = _get("ru", "/cartoons/").content.decode()

    assert "Во все тяжкие" in body
