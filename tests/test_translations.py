"""Tests for Task 93: the translation pipeline (Bronze -> Silver -> warehouse loader -> DQ).

Everything is in-memory: S3 and the SQLAlchemy session are mocked, TMDB is a
stub client. Nothing here touches a network or a database.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy.dialects import postgresql

import etl.warehouse_loader.load_dimensions as load_dimensions_module
from data_quality.warehouse_checks import check_fk_integrity, check_translation_sanity
from etl import s3_utils
from etl.bronze.ingest_countries import ingest_countries
from etl.bronze.ingest_genres import ingest_genres
from etl.bronze.ingest_movie_details import ingest_movie_details
from etl.bronze.ingest_people import ingest_people
from etl.bronze.refresh_movies import _split_payload
from etl.silver.transform_country_translations import transform_country_translations
from etl.silver.transform_genres import transform_genre_translations
from etl.silver.transform_movie_translations import (
    _extract_translation_rows as _extract_movie_rows,
    transform_movie_translations,
)
from etl.silver.transform_people_translations import (
    _extract_translation_rows as _extract_person_rows,
    transform_people_translations,
)
from etl.tmdb_client import TMDBClient
from etl.translations import TRANSLATION_LANGUAGES, select_translations, trim_translations
from etl.warehouse_loader.load_dimensions import (
    load_country_translation,
    load_genre_translation,
    load_movie_translation,
    load_person_translation,
)
from tests.test_etl import _make_s3_mock_with_files, _person_payload, _raw_movie

DATE = dt.date(2026, 9, 24)


# --- fixtures ----------------------------------------------------------------

def _entry(lang: str, region: str, **data) -> dict:
    return {"iso_639_1": lang, "iso_3166_1": region, "name": lang, "english_name": lang, "data": data}


def _translations_block(*entries: dict) -> dict:
    return {"id": 1, "translations": list(entries)}


def _movie_with_translations(movie_id: int, *entries: dict) -> dict:
    raw = _raw_movie(movie_id)
    raw["translations"] = _translations_block(*entries)
    return raw


RU_FIGHT_CLUB = _entry(
    "ru", "RU", title="Бойцовский клуб", overview="Сотрудник страховой компании", tagline="«Интриги»",
    homepage="", runtime=139,
)
UZ_FIGHT_CLUB = _entry("uz", "UZ", title="Jang klubi", overview="Hech qanday", tagline="", homepage="", runtime=0)
DE_FIGHT_CLUB = _entry("de", "DE", title="Fight Club", overview="Ein Angestellter", tagline="", homepage="", runtime=0)


def _parquet_from_last_put(mock_s3) -> pd.DataFrame:
    _, kwargs = mock_s3.put_object.call_args
    return pd.read_parquet(io.BytesIO(kwargs["Body"]))


# --- etl.translations --------------------------------------------------------

def test_trim_translations_keeps_only_shipped_languages():
    payload = _movie_with_translations(1, RU_FIGHT_CLUB, DE_FIGHT_CLUB, UZ_FIGHT_CLUB)
    trimmed = trim_translations(payload)
    langs = [e["iso_639_1"] for e in trimmed["translations"]["translations"]]
    assert langs == ["ru", "uz"]
    # the input is not mutated, the rest of the payload is untouched
    assert len(payload["translations"]["translations"]) == 3
    assert trimmed["title"] == payload["title"]


def test_trim_translations_passes_through_a_payload_without_the_block():
    payload = _raw_movie(1)
    assert trim_translations(payload) is payload


def test_select_translations_normalises_empty_strings_to_none():
    picked = select_translations(
        _movie_with_translations(1, RU_FIGHT_CLUB, UZ_FIGHT_CLUB), ("title", "overview", "tagline")
    )
    assert picked["ru"]["tagline"] == "«Интриги»"
    assert picked["uz"]["tagline"] is None          # "" -> None
    assert picked["uz"]["title"] == "Jang klubi"


def test_select_translations_omits_a_language_with_no_text_at_all():
    empty_uz = _entry("uz", "UZ", title="", overview="", tagline="")
    picked = select_translations(
        _movie_with_translations(1, RU_FIGHT_CLUB, empty_uz), ("title", "overview", "tagline")
    )
    assert set(picked) == {"ru"}


def test_select_translations_prefers_the_home_country_variant():
    by = _entry("ru", "BY", title="Belarusian-region title")
    picked = select_translations(_movie_with_translations(1, by, RU_FIGHT_CLUB), ("title",))
    assert picked["ru"]["title"] == "Бойцовский клуб"


def test_select_translations_ignores_unshipped_languages():
    picked = select_translations(_movie_with_translations(1, DE_FIGHT_CLUB), ("title",))
    assert picked == {}


def test_select_translations_returns_none_when_the_block_is_absent():
    """The backfill-is-impossible signal: None (no key) is not {} (no ru/uz text)."""
    assert select_translations(_raw_movie(1), ("title",)) is None


# --- TMDB client -------------------------------------------------------------

def _client() -> TMDBClient:
    return TMDBClient(api_key="test-key", max_retries=0, backoff_factor=0)


def _ok(body) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp


@pytest.mark.parametrize("method,path", [
    ("get_genres", "/genre/movie/list"),
    ("get_tv_genres", "/genre/tv/list"),
])
def test_genre_calls_send_language_only_when_asked(method, path):
    client = _client()
    with patch.object(client.session, "get", return_value=_ok({"genres": []})) as mock_get:
        getattr(client, method)()
        assert "language" not in mock_get.call_args[1]["params"]
        getattr(client, method)(language="ru")
    args, kwargs = mock_get.call_args
    assert args[0].endswith(path)
    assert kwargs["params"]["language"] == "ru"


def test_get_person_details_forwards_append_to_response():
    client = _client()
    with patch.object(client.session, "get", return_value=_ok({"id": 31})) as mock_get:
        client.get_person_details(31, append_to_response="translations")
    assert mock_get.call_args[1]["params"]["append_to_response"] == "translations"


def test_get_countries_hits_configuration_countries_with_language():
    client = _client()
    with patch.object(client.session, "get", return_value=_ok([])) as mock_get:
        client.get_countries(language="uz")
    args, kwargs = mock_get.call_args
    assert args[0].endswith("/configuration/countries")
    assert kwargs["params"]["language"] == "uz"


# --- Bronze ------------------------------------------------------------------

def _put_keys(mock_s3) -> list[str]:
    return [c[1]["Key"] for c in mock_s3.put_object.call_args_list]


def test_ingest_genres_with_translations_fetches_russian_only():
    mock_client = MagicMock()
    mock_client.get_genres.return_value = {"genres": [{"id": 18, "name": "Drama"}]}
    mock_client.get_tv_genres.return_value = {"genres": [{"id": 10759, "name": "Action & Adventure"}]}
    mock_s3 = MagicMock()

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ingest_genres(ingestion_date=DATE, client=mock_client, with_tv=True, with_translations=True)

    keys = _put_keys(mock_s3)
    assert "bronze/genres/ingestion_date=2026-09-24/genres_ru.json" in keys
    assert "bronze/genres/ingestion_date=2026-09-24/genres_tv_ru.json" in keys
    assert not any("genres_uz" in k or "genres_tv_uz" in k for k in keys)   # TMDB's uz names are all null
    languages_asked = [c.kwargs.get("language") for c in mock_client.get_genres.call_args_list]
    assert languages_asked == [None, "ru"]


def test_ingest_genres_without_translations_makes_no_language_call():
    mock_client = MagicMock()
    mock_client.get_genres.return_value = {"genres": []}
    with patch.object(s3_utils, "get_s3_client", return_value=MagicMock()):
        ingest_genres(ingestion_date=DATE, client=mock_client)
    mock_client.get_genres.assert_called_once_with()


def test_ingest_countries_writes_one_file_per_language():
    mock_client = MagicMock()
    mock_client.get_countries.return_value = [
        {"iso_3166_1": "US", "english_name": "United States of America", "native_name": "x"}
    ]
    mock_s3 = MagicMock()

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uris = ingest_countries(ingestion_date=DATE, client=mock_client)

    assert len(uris) == 2
    assert _put_keys(mock_s3) == [
        "bronze/countries/ingestion_date=2026-09-24/countries_ru.json",
        "bronze/countries/ingestion_date=2026-09-24/countries_uz.json",
    ]
    assert [c.kwargs["language"] for c in mock_client.get_countries.call_args_list] == ["ru", "uz"]


def test_ingest_movie_details_appends_translations_and_trims_to_shipped_languages():
    mock_client = MagicMock()
    mock_client.get_movie_details.return_value = _movie_with_translations(
        550, RU_FIGHT_CLUB, DE_FIGHT_CLUB, UZ_FIGHT_CLUB
    )
    mock_s3 = MagicMock()

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ingest_movie_details([550], ingestion_date=DATE, client=mock_client)

    assert mock_client.get_movie_details.call_args.kwargs["append_to_response"] == "videos,translations"
    written = json.loads(mock_s3.put_object.call_args[1]["Body"])
    assert [e["iso_639_1"] for e in written["translations"]["translations"]] == ["ru", "uz"]


def test_split_payload_keeps_translations_inline_in_details_and_trimmed():
    payload = _movie_with_translations(550, RU_FIGHT_CLUB, DE_FIGHT_CLUB)
    payload["credits"] = {"cast": [], "crew": []}

    details, credits = _split_payload(550, payload)

    assert "credits" not in details
    assert [e["iso_639_1"] for e in details["translations"]["translations"]] == ["ru"]
    assert "translations" not in credits


def test_ingest_people_appends_translations_and_trims():
    mock_client = MagicMock()
    payload = _person_payload(31)
    payload["translations"] = _translations_block(
        _entry("ru", "RU", biography="Актёр"), _entry("fr", "FR", biography="Acteur")
    )
    mock_client.get_person_details.return_value = payload
    mock_s3 = MagicMock()
    mock_s3.get_paginator.return_value.paginate.return_value = [{"Contents": []}]

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        ingest_people([31], ingestion_date=DATE, client=mock_client)

    assert mock_client.get_person_details.call_args.kwargs["append_to_response"] == "translations"
    written = json.loads(mock_s3.put_object.call_args[1]["Body"])
    assert [e["iso_639_1"] for e in written["translations"]["translations"]] == ["ru"]


# --- Silver: movies ----------------------------------------------------------

def test_extract_movie_rows_one_row_per_shipped_language():
    rows = _extract_movie_rows(_movie_with_translations(550, RU_FIGHT_CLUB, UZ_FIGHT_CLUB, DE_FIGHT_CLUB))
    assert {r["lang"] for r in rows} == {"ru", "uz"}
    assert all(r["movie_id"] == 550 for r in rows)


def test_extract_movie_rows_none_for_a_pre_task_93_payload():
    assert _extract_movie_rows(_raw_movie(550)) is None


def test_transform_movie_translations_writes_silver_parquet():
    key = "bronze/movie_details/ingestion_date=2026-09-24/550.json"
    mock_s3 = _make_s3_mock_with_files({key: _movie_with_translations(550, RU_FIGHT_CLUB, UZ_FIGHT_CLUB)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uri = transform_movie_translations(ingestion_date=DATE, bucket="theoria-datalake")

    assert uri == (
        "s3://theoria-datalake/silver/movie_translations/ingestion_date=2026-09-24/movie_translations.parquet"
    )
    df = _parquet_from_last_put(mock_s3).sort_values("lang").reset_index(drop=True)
    assert list(df.columns) == ["movie_id", "lang", "title", "overview", "tagline"]
    assert list(df["lang"]) == ["ru", "uz"]
    assert df.loc[0, "title"] == "Бойцовский клуб"
    assert pd.isna(df.loc[1, "tagline"])            # "" became null


def test_transform_movie_translations_old_partition_writes_empty_wellformed_parquet(caplog):
    """The backfill-is-impossible contract, proven against a pre-Task-93 partition:
    zero rows, every column, one aggregate warning, no exception."""
    payloads = {
        f"bronze/movie_details/ingestion_date=2026-09-06/{mid}.json": _raw_movie(mid)
        for mid in (550, 551, 552)
    }
    mock_s3 = _make_s3_mock_with_files(payloads)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with caplog.at_level(logging.WARNING):
            transform_movie_translations(ingestion_date=dt.date(2026, 9, 6), bucket="theoria-datalake")

    df = _parquet_from_last_put(mock_s3)
    assert len(df) == 0
    assert set(df.columns) == {"movie_id", "lang", "title", "overview", "tagline"}
    warnings = [r for r in caplog.records if "no `translations` key" in r.message]
    assert len(warnings) == 1 and "3 of 3" in warnings[0].message


def test_transform_movie_translations_film_with_no_ru_uz_text_is_not_a_missing_key(caplog):
    """A film whose block holds only German is a real 'no Russian' — no warning, no row."""
    key = "bronze/movie_details/ingestion_date=2026-09-24/1.json"
    mock_s3 = _make_s3_mock_with_files({key: _movie_with_translations(1, DE_FIGHT_CLUB)})

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with caplog.at_level(logging.WARNING):
            transform_movie_translations(ingestion_date=DATE, bucket="theoria-datalake")

    assert len(_parquet_from_last_put(mock_s3)) == 0
    assert not any("no `translations` key" in r.message for r in caplog.records)


def test_transform_movie_translations_raises_when_no_bronze_files():
    mock_s3 = MagicMock()
    mock_s3.get_paginator.return_value.paginate.return_value = [{"Contents": []}]
    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with pytest.raises(FileNotFoundError):
            transform_movie_translations(ingestion_date=DATE, bucket="theoria-datalake")


# --- Silver: people ----------------------------------------------------------

def _person_with_translations(person_id: int, *entries: dict) -> dict:
    payload = _person_payload(person_id)
    payload["translations"] = _translations_block(*entries)
    return payload


def test_extract_person_rows_skips_a_language_with_an_empty_biography():
    rows = _extract_person_rows(_person_with_translations(
        31, _entry("ru", "RU", biography="Актёр"), _entry("uz", "UZ", biography="")
    ))
    assert [(r["lang"], r["biography"]) for r in rows] == [("ru", "Актёр")]


def test_transform_people_translations_reads_every_partition_later_wins():
    payloads = {
        "bronze/person_details/ingestion_date=2026-09-01/31.json":
            _person_with_translations(31, _entry("ru", "RU", biography="Старая")),
        "bronze/person_details/ingestion_date=2026-09-20/31.json":
            _person_with_translations(31, _entry("ru", "RU", biography="Новая")),
        "bronze/person_details/ingestion_date=2026-09-10/7.json":
            _person_with_translations(7, _entry("ru", "RU", biography="Другой")),
    }
    mock_s3 = _make_s3_mock_with_files(payloads)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        uri = transform_people_translations(ingestion_date=DATE, bucket="theoria-datalake")

    assert uri.endswith("silver/person_translations/ingestion_date=2026-09-24/person_translations.parquet")
    df = _parquet_from_last_put(mock_s3).set_index("person_id")
    assert len(df) == 2
    assert df.loc[31, "biography"] == "Новая"


def test_transform_people_translations_pre_task_93_people_yield_zero_rows_and_one_warning(caplog):
    payloads = {
        f"bronze/person_details/ingestion_date=2026-08-01/{pid}.json": _person_payload(pid)
        for pid in (1, 2, 3)
    }
    mock_s3 = _make_s3_mock_with_files(payloads)

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with caplog.at_level(logging.WARNING):
            transform_people_translations(ingestion_date=DATE, bucket="theoria-datalake")

    df = _parquet_from_last_put(mock_s3)
    assert len(df) == 0
    assert set(df.columns) == {"person_id", "lang", "biography"}
    assert len([r for r in caplog.records if "no `translations` key" in r.message]) == 1


# --- Silver: genres ----------------------------------------------------------

def _genre_files(**files: dict) -> dict:
    return {f"bronze/genres/ingestion_date=2026-09-24/{name}.json": body for name, body in files.items()}


def test_transform_genre_translations_merges_movie_and_tv_and_skips_null_names():
    mock_s3 = _make_s3_mock_with_files(_genre_files(
        genres_ru={"genres": [{"id": 28, "name": "боевик"}, {"id": 99, "name": None}]},
        genres_tv_ru={"genres": [{"id": 28, "name": "боевик"}, {"id": 10759, "name": "Боевик и Приключения"}]},
    ))

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        transform_genre_translations(ingestion_date=DATE, bucket="theoria-datalake", with_tv=True)

    df = _parquet_from_last_put(mock_s3).sort_values("genre_id").reset_index(drop=True)
    assert list(df["genre_id"]) == [28, 10759]      # 28 de-duplicated, null-named 99 skipped
    assert set(df["lang"]) == {"ru"}


def test_transform_genre_translations_raises_when_a_language_has_no_names():
    mock_s3 = _make_s3_mock_with_files(_genre_files(genres_ru={"genres": [{"id": 28, "name": None}]}))
    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        with pytest.raises(ValueError):
            transform_genre_translations(ingestion_date=DATE, bucket="theoria-datalake")


# --- Silver: countries -------------------------------------------------------

def test_transform_country_translations_uses_native_name_per_language():
    prefix = "bronze/countries/ingestion_date=2026-09-24/"
    mock_s3 = _make_s3_mock_with_files({
        prefix + "countries_ru.json": [
            {"iso_3166_1": "US", "english_name": "United States of America", "native_name": "Соединенные Штаты"},
            {"iso_3166_1": "XX", "english_name": "Nowhere", "native_name": ""},
        ],
        prefix + "countries_uz.json": [
            {"iso_3166_1": "US", "english_name": "United States of America", "native_name": "Qoʻshma Shtatlar"},
        ],
    })

    with patch.object(s3_utils, "get_s3_client", return_value=mock_s3):
        transform_country_translations(ingestion_date=DATE, bucket="theoria-datalake")

    df = _parquet_from_last_put(mock_s3)
    assert {(r.country_code, r.lang, r.name) for r in df.itertuples()} == {
        ("US", "ru", "Соединенные Штаты"),
        ("US", "uz", "Qoʻshma Shtatlar"),
    }


# --- warehouse loaders -------------------------------------------------------

def _known(monkeypatch, *, ints: set[int] = frozenset(), strs: set[str] = frozenset()):
    monkeypatch.setattr(load_dimensions_module, "_existing_ids", lambda s, t, c: set(ints))
    monkeypatch.setattr(load_dimensions_module, "_existing_str_ids", lambda s, t, c: set(strs))


def _movie_df(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["movie_id", "lang", "title", "overview", "tagline"]).astype(
        {"movie_id": "Int64"}
    )


def test_load_movie_translation_replaces_by_parent_and_quarantines(monkeypatch):
    _known(monkeypatch, ints={1})
    session = MagicMock()
    df = _movie_df([
        (1, "ru", "Т", "О", None),
        (1, "uz", "T", None, None),
        (999, "ru", "X", "Y", None),            # no dim_movie row
        (1, "de", "Fight Club", "E", None),     # a language we don't ship
        (1, "ru", None, None, None),            # no text at all
    ])

    count, rejects = load_movie_translation(session, df, DATE)

    assert count == 2
    assert [r["rejection_reason"] for r in rejects] == [
        "unknown movie_id", "unsupported lang 'de'", "no translated text",
    ]
    calls = session.execute.call_args_list
    assert "DELETE FROM movie_translation WHERE movie_id = ANY(:parent_ids)" in str(calls[0][0][0])
    assert calls[0][0][1] == {"parent_ids": [1]}
    inserted = calls[1][0][1]
    assert [(r["movie_id"], r["lang"]) for r in inserted] == [(1, "ru"), (1, "uz")]
    assert inserted[1]["overview"] is None and inserted[0]["ingestion_date"] == DATE


def test_load_movie_translation_a_films_set_can_shrink(monkeypatch):
    """Why this is REPLACE, not upsert: load ru+uz, then ru only — the second load
    must delete the film's rows first, so the stale uz row does not survive."""
    _known(monkeypatch, ints={1})
    session = MagicMock()
    load_movie_translation(session, _movie_df([(1, "ru", "a", "b", None), (1, "uz", "c", "d", None)]), DATE)
    session.reset_mock()

    count, _ = load_movie_translation(session, _movie_df([(1, "ru", "a", "b", None)]), DATE)

    calls = session.execute.call_args_list
    assert count == 1 and len(calls) == 2
    assert "DELETE FROM movie_translation" in str(calls[0][0][0])
    assert [r["lang"] for r in calls[1][0][1]] == ["ru"]


def test_load_person_translation_is_replace_scoped_to_people_present(monkeypatch):
    _known(monkeypatch, ints={31})
    session = MagicMock()
    df = pd.DataFrame({"person_id": pd.array([31, 5], dtype="Int64"), "lang": ["ru", "ru"], "biography": ["Б", "Б"]})

    count, rejects = load_person_translation(session, df, DATE)

    assert count == 1 and [r["rejection_reason"] for r in rejects] == ["unknown person_id"]
    assert session.execute.call_args_list[0][0][1] == {"parent_ids": [31]}


def test_load_genre_translation_is_an_upsert_that_never_touches_uz(monkeypatch):
    """ON CONFLICT (genre_id, lang): the hand-seeded uz rows are a different key,
    and Silver only ever carries ru — so the seed cannot be overwritten."""
    _known(monkeypatch, ints={28})
    session = MagicMock()
    df = pd.DataFrame({"genre_id": pd.array([28], dtype="Int64"), "lang": ["ru"], "genre_name": ["боевик"]})

    count, rejects = load_genre_translation(session, df, DATE)

    assert count == 1 and rejects == []
    stmt = str(session.execute.call_args_list[0][0][0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (genre_id, lang) DO UPDATE" in stmt
    assert "DELETE" not in stmt


def test_load_country_translation_skips_countries_not_in_dim_country_without_rejecting(monkeypatch, caplog):
    _known(monkeypatch, strs={"US"})
    session = MagicMock()
    df = pd.DataFrame({
        "country_code": ["US", "ZZ", "US"], "lang": ["ru", "ru", "uz"],
        "name": ["Соединенные Штаты", "Нигде", "Qoʻshma Shtatlar"],
    })

    with caplog.at_level(logging.INFO):
        count, rejects = load_country_translation(session, df, DATE)

    assert count == 2 and rejects == []             # ZZ is valid TMDB data we don't need, not a bad row
    assert any("skipped 1 not in dim_country" in r.message for r in caplog.records)


# --- DQ checks ---------------------------------------------------------------

def _session_with_lang_counts(counts_by_table: dict[str, dict[str, int]]) -> MagicMock:
    session = MagicMock()

    def execute(stmt, *a, **k):
        sql = str(stmt)
        table = sql.split("FROM ")[1].split()[0]
        result = MagicMock()
        result.all.return_value = list(counts_by_table.get(table, {}).items())
        return result

    session.execute.side_effect = execute
    return session


def _silver_reader(rows_by_entity: dict[str, int]):
    def read(bucket, entity, date, filename):
        if entity not in rows_by_entity:
            raise FileNotFoundError(entity)
        return pd.DataFrame({"x": range(rows_by_entity[entity])})
    return read


def test_check_translation_sanity_passes_when_loaded_and_in_domain(monkeypatch):
    import data_quality.warehouse_checks as wc
    session = _session_with_lang_counts({
        "movie_translation": {"ru": 1200, "uz": 800}, "person_translation": {"ru": 90},
        "genre_translation": {"ru": 27, "uz": 27}, "country_translation": {"ru": 56, "uz": 56},
    })
    monkeypatch.setattr(wc, "_read_silver_parquet", _silver_reader({
        "movie_translations": 2000, "person_translations": 90, "genre_translations": 27, "country_translations": 250,
    }))

    results = check_translation_sanity(session, "b", DATE)

    assert len(results) == 8 and all(r.passed for r in results)
    assert "ru=1200, uz=800" in next(r for r in results if r.check == "translations:movie_translation:load").detail


def test_check_translation_sanity_flags_a_stray_lang(monkeypatch):
    import data_quality.warehouse_checks as wc
    session = _session_with_lang_counts({"movie_translation": {"ru": 5, "ru-RU": 2}})
    monkeypatch.setattr(wc, "_read_silver_parquet", _silver_reader({}))

    results = check_translation_sanity(session, "b", DATE)

    failed = [r for r in results if not r.passed]
    assert [r.check for r in failed] == ["translations:movie_translation:lang_domain"]
    assert "ru-RU" in failed[0].detail


def test_check_translation_sanity_flags_an_empty_table_despite_silver_rows(monkeypatch):
    import data_quality.warehouse_checks as wc
    session = _session_with_lang_counts({})
    monkeypatch.setattr(wc, "_read_silver_parquet", _silver_reader({"movie_translations": 10}))

    results = check_translation_sanity(session, "b", DATE)

    failed = {r.check for r in results if not r.passed}
    assert failed == {"translations:movie_translation:load"}


def test_check_translation_sanity_pre_task_93_partition_skips_the_load_check(monkeypatch):
    """No Silver file (or an empty one) is the pre-Task-93 state: empty tables are fine."""
    import data_quality.warehouse_checks as wc
    session = _session_with_lang_counts({})
    monkeypatch.setattr(wc, "_read_silver_parquet", _silver_reader({"movie_translations": 0}))

    results = check_translation_sanity(session, "b", DATE)

    assert all(r.passed for r in results)
    assert not any(r.check.endswith(":load") and "person" in r.check for r in results)


def test_check_fk_integrity_covers_the_four_translation_tables():
    session = MagicMock()
    session.execute.return_value.scalar.return_value = 0
    checks = {r.check for r in check_fk_integrity(session)}
    for fk in (
        "movie_translation.movie_id->dim_movie.movie_id",
        "person_translation.person_id->dim_person.person_id",
        "genre_translation.genre_id->dim_genre.genre_id",
        "country_translation.country_code->dim_country.country_code",
    ):
        assert f"fk:{fk}" in checks


def test_translation_languages_are_the_two_the_site_ships():
    assert TRANSLATION_LANGUAGES == ("ru", "uz")
