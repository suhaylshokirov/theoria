"""Pipeline tests for franchises (TMDB collections), Task 153.

Bronze -> Silver -> loader. No network, no S3, no database: TMDB, S3 and the
session are faked, the same posture as test_etl.py. The Django side of the
feature is tested separately.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from etl.bronze import ingest_collections as bronze
from etl.silver import transform_collections as silver
from etl.silver.transform_movies import _flatten_movie
from etl.tmdb_client import TMDBAPIError, TMDBClient
from etl.translations import trim_translations
from etl.warehouse_loader import load_collections as loader

DATE = dt.date(2026, 11, 16)


# ---------------------------------------------------------------------------
# Fixtures: a payload shaped like the live `GET /collection/{id}` (measured 2026-11-16)
# ---------------------------------------------------------------------------

def _part(pid, title, **over):
    row = {
        "adult": False, "backdrop_path": f"/b{pid}.jpg", "genre_ids": [12, 14, 28],
        "id": pid, "title": title, "original_language": "en", "original_title": title,
        "overview": f"About {title}.", "popularity": 12.5, "poster_path": f"/p{pid}.jpg",
        "release_date": "2001-12-18", "softcore": False, "video": False,
        "vote_average": 8.4, "vote_count": 25000, "media_type": "movie",
    }
    row.update(over)
    return row


def _payload(**over):
    payload = {
        "id": 119, "name": "The Lord of the Rings Collection",
        "original_name": "The Lord of the Rings Collection", "original_language": "en",
        "overview": "An epic.", "poster_path": "/cp.jpg", "backdrop_path": "/cb.jpg",
        "parts": [_part(120, "The Fellowship of the Ring"), _part(121, "The Two Towers",
                                                                  release_date="2002-12-18")],
        "images": {
            "posters": [{"file_path": "/i1.jpg", "iso_639_1": "en", "iso_3166_1": "US",
                         "width": 1000, "height": 1500, "aspect_ratio": 0.667,
                         "vote_average": 5.3, "vote_count": 4}],
            "backdrops": [{"file_path": "/i2.jpg", "iso_639_1": None, "iso_3166_1": None,
                           "width": 1920, "height": 1080, "aspect_ratio": 1.778,
                           "vote_average": 5.1, "vote_count": 2}],
        },
        "translations": {"id": 119, "translations": [
            {"iso_639_1": "en", "iso_3166_1": "GB", "data": {"homepage": "https://gb.example"}},
            {"iso_639_1": "en", "iso_3166_1": "US", "data": {"homepage": "https://us.example"}},
            {"iso_639_1": "ru", "iso_3166_1": "RU",
             "data": {"title": "Властелин колец (Коллекция)", "overview": "Эпос.", "homepage": ""}},
            {"iso_639_1": "uz", "iso_3166_1": "UZ",
             "data": {"title": "Uzbek title", "overview": "", "homepage": "https://uz.example"}},
            {"iso_639_1": "de", "iso_3166_1": "DE", "data": {"title": "Herr der Ringe"}},
        ]},
    }
    payload.update(over)
    return payload


def _localized(lang, parts):
    return {"id": 119, "parts": parts}


# ---------------------------------------------------------------------------
# tmdb_client / translations
# ---------------------------------------------------------------------------

def _client_with_status(status, body=None):
    session = MagicMock()
    session.get.return_value = MagicMock(
        status_code=status, text="not found", json=MagicMock(return_value=body or {})
    )
    return TMDBClient(api_key="k", base_url="https://x/3", session=session, max_retries=0), session


def test_a_404_raises_tmdb_api_error_carrying_its_status_code():
    client, _ = _client_with_status(404)
    with pytest.raises(TMDBAPIError) as err:
        client.get_collection_details(7)
    assert err.value.status_code == 404


def test_get_collection_details_sends_append_and_language_params():
    client, session = _client_with_status(200, {"id": 1})
    client.get_collection_details(1, append_to_response="translations,images", language="ru-RU")
    (url,), kwargs = session.get.call_args
    assert url == "https://x/3/collection/1"
    assert kwargs["params"] == {
        "api_key": "k", "append_to_response": "translations,images", "language": "ru-RU",
    }


def test_trim_translations_can_keep_one_extra_language():
    trimmed = trim_translations(_payload(), keep=("ru", "uz", "en"))
    langs = [t["iso_639_1"] for t in trimmed["translations"]["translations"]]
    assert langs == ["en", "en", "ru", "uz"]  # de dropped, both en entries kept
    default = [t["iso_639_1"] for t in trim_translations(_payload())["translations"]["translations"]]
    assert default == ["ru", "uz"]


# ---------------------------------------------------------------------------
# Silver movies: the backdrop joins the flattened stub
# ---------------------------------------------------------------------------

def test_flatten_movie_keeps_the_collection_backdrop():
    flat = _flatten_movie({"id": 1, "belongs_to_collection": {
        "id": 119, "name": "X", "poster_path": "/p.jpg", "backdrop_path": "/b.jpg"}})
    assert flat["collection_backdrop_path"] == "/b.jpg"
    assert _flatten_movie({"id": 2})["collection_backdrop_path"] is None


# ---------------------------------------------------------------------------
# Bronze
# ---------------------------------------------------------------------------

def _tmdb(responses):
    """A fake client: responses maps (id, language) -> payload or an exception."""
    client = MagicMock()

    def get(collection_id, *, append_to_response=None, language=None):
        out = responses[(collection_id, language)]
        if isinstance(out, Exception):
            raise out
        return out

    client.get_collection_details.side_effect = get
    return client


def _run_ingest(responses, ids):
    written = {}
    with patch.object(bronze.s3_utils, "write_json",
                      side_effect=lambda bucket, key, data: written.__setitem__(key, data)):
        result = bronze.ingest_collections(ids, ingestion_date=DATE, client=_tmdb(responses))
    return result, written


def test_ingest_writes_details_and_both_localisations_for_each_collection():
    responses = {(119, None): _payload(), (119, "ru-RU"): _localized("ru", []),
                 (119, "uz-UZ"): _localized("uz", [])}

    (ok, failed, gone), written = _run_ingest(responses, [119])

    assert (ok, failed, gone) == ([119], [], [])
    assert sorted(written) == [
        "bronze/collection_details/ingestion_date=2026-11-16/119.json",
        "bronze/collection_localized/ingestion_date=2026-11-16/ru/119.json",
        "bronze/collection_localized/ingestion_date=2026-11-16/uz/119.json",
    ]
    details = written["bronze/collection_details/ingestion_date=2026-11-16/119.json"]
    kept = {t["iso_639_1"] for t in details["translations"]["translations"]}
    assert kept == {"en", "ru", "uz"}                      # de trimmed, en kept for the homepage
    assert len(details["images"]["posters"]) == 1          # images kept whole


def test_a_404_is_counted_as_gone_writes_nothing_and_is_not_a_failure():
    responses = {(5, None): TMDBAPIError("gone", status_code=404)}
    (ok, failed, gone), written = _run_ingest(responses, [5])
    assert (ok, failed, gone) == ([], [], [5])
    assert written == {}


def test_any_other_error_is_a_failure_not_gone():
    responses = {(5, None): TMDBAPIError("boom", status_code=500)}
    (ok, failed, gone), _ = _run_ingest(responses, [5])
    assert (ok, failed, gone) == ([], [5], [])


def test_a_collection_is_all_or_nothing_when_a_localised_fetch_fails():
    """Details fetched fine, ru-RU blew up: no file at all for that id, so Silver
    never sees a details file without its localised sibling."""
    responses = {(119, None): _payload(),
                 (119, "ru-RU"): TMDBAPIError("boom", status_code=503),
                 (119, "uz-UZ"): _localized("uz", [])}
    (ok, failed, gone), written = _run_ingest(responses, [119])
    assert (ok, failed) == ([], [119]) and written == {}


def test_one_failing_collection_does_not_stop_the_others():
    responses = {(1, None): TMDBAPIError("x", status_code=500),
                 (2, None): _payload(id=2), (2, "ru-RU"): {"id": 2, "parts": []},
                 (2, "uz-UZ"): {"id": 2, "parts": []}}
    (ok, failed, gone), _ = _run_ingest(responses, [1, 2])
    assert (ok, failed) == ([2], [1])


def test_extract_collection_ids_reads_the_partition_and_dedupes():
    films = [
        ("k1", {"belongs_to_collection": {"id": 10}}, None),
        ("k2", {"belongs_to_collection": {"id": 10}}, None),
        ("k3", {"belongs_to_collection": None}, None),
        ("k4", {"id": 3}, None),
        ("k5", None, RuntimeError("unreadable")),
        ("k6", {"belongs_to_collection": {"id": 4}}, None),
    ]
    with patch.object(bronze, "_list_keys", return_value=["k1", "k2", "k3", "k4", "k5", "k6"]), \
         patch.object(bronze.s3_utils, "read_json_objects", return_value=films):
        assert bronze.extract_collection_ids(DATE, bucket="b") == [4, 10]


# ---------------------------------------------------------------------------
# Silver
# ---------------------------------------------------------------------------

def _frames(details=None, localized=None):
    return silver.extract_frames(details or [_payload()], localized or {})


def test_collection_row_takes_the_us_english_homepage_and_blanks_become_null():
    row = _frames(details=[_payload(overview="", backdrop_path="")])["collections"].iloc[0]
    assert row["homepage"] == "https://us.example"          # US wins over GB
    assert pd.isna(row["overview"]) and pd.isna(row["backdrop_path"])
    assert row["name"] == "The Lord of the Rings Collection"


def test_parts_keep_every_key_but_the_two_constants_and_record_position():
    parts = _frames()["collection_parts"]
    assert list(parts.columns) == silver.PART_COLUMNS
    assert "media_type" not in parts.columns and "softcore" not in parts.columns
    assert parts["position"].tolist() == [0, 1]
    assert parts.iloc[0]["genre_ids"] == [12, 14, 28]
    assert parts.iloc[0]["release_date"] == dt.date(2001, 12, 18)


def test_an_empty_release_date_becomes_null_and_adult_video_flags_survive():
    payload = _payload(parts=[_part(9, "Untitled", release_date="", adult=True, video=True)])
    part = _frames(details=[payload])["collection_parts"].iloc[0]
    assert pd.isna(part["release_date"])
    assert bool(part["adult"]) is True and bool(part["video"]) is True


def test_images_carry_their_kind_and_a_repeated_path_is_deduplicated(caplog):
    payload = _payload()
    payload["images"]["posters"].append(dict(payload["images"]["posters"][0]))
    images = _frames(details=[payload])["collection_images"]
    assert sorted(zip(images["file_path"], images["kind"])) == [
        ("/i1.jpg", "poster"), ("/i2.jpg", "backdrop")]


def test_collection_translations_keep_russian_name_and_drop_uzbek_name_and_other_languages():
    tr = _frames()["collection_translations"].set_index("lang")
    assert set(tr.index) == {"ru", "uz"}                     # en and de never reach Silver
    assert tr.loc["ru", "name"] == "Властелин колец (Коллекция)"
    assert pd.isna(tr.loc["uz", "name"])                     # Task 110: no Uzbek titles
    assert tr.loc["uz", "homepage"] == "https://uz.example"


def test_part_translation_keeps_only_text_that_differs_from_english():
    english = _payload()
    ru = _localized("ru", [
        _part(120, "Братство Кольца", overview="About The Fellowship of the Ring."),  # title differs, overview same
        _part(121, "The Two Towers", overview="Две крепости."),                        # title same, overview differs
    ])
    rows = silver._extract_part_translations(english, {"ru": ru})
    by_part = {r["movie_id"]: r for r in rows}
    assert by_part[120]["title"] == "Братство Кольца" and by_part[120]["overview"] is None
    assert by_part[121]["title"] is None and by_part[121]["overview"] == "Две крепости."


def test_part_translation_skips_a_part_whose_localised_text_is_all_english_fallback():
    english = _payload()
    ru = _localized("ru", [_part(120, "The Fellowship of the Ring",
                                 overview="About The Fellowship of the Ring.")])
    assert silver._extract_part_translations(english, {"ru": ru}) == []


def test_part_translation_never_keeps_an_uzbek_title():
    english = _payload()
    uz = _localized("uz", [_part(120, "Uzbek title", overview="Uzbek overview")])
    (row,) = silver._extract_part_translations(english, {"uz": uz})
    assert row["title"] is None and row["overview"] == "Uzbek overview"


def test_part_translation_ignores_a_part_the_english_payload_does_not_list():
    ru = _localized("ru", [_part(999, "Лишний", overview="Нет")])
    assert silver._extract_part_translations(_payload(), {"ru": ru}) == []


def test_every_frame_has_its_declared_columns_even_with_no_rows():
    frames = silver.extract_frames([], {})
    for entity, columns in silver.OUTPUTS.items():
        assert list(frames[entity].columns) == columns and len(frames[entity]) == 0


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _silver_for(ids):
    details = [_payload(id=i, name=f"Franchise {i}") for i in ids]
    for d in details:
        d["parts"] = [_part(d["id"] * 10, f"Part of {d['id']}")]
    frames = silver.extract_frames(details, {
        i: {"ru": {"id": i, "parts": [_part(i * 10, "Часть", overview="Текст")]}} for i in ids
    })
    return {entity: df for entity, df in frames.items()}


def _run_load(silver_frames, known_ids):
    """Run load_collections against fakes; return the recorded replace calls."""
    order = []

    def replace(session, table, parent_col, parent_ids, columns, records):
        order.append((table, list(parent_ids), len(records)))
        return len(records)

    with patch.object(loader, "_read_silver_parquet",
                      side_effect=lambda b, entity, d, f: silver_frames[entity]), \
         patch.object(loader, "get_session", return_value=MagicMock(
             __enter__=MagicMock(return_value=MagicMock()), __exit__=MagicMock(return_value=False))), \
         patch.object(loader, "_existing_ids", return_value=set(known_ids)), \
         patch.object(loader, "_upsert", side_effect=lambda s, t, pk, cols, rows: len(rows)), \
         patch.object(loader, "_replace_by_parent", side_effect=replace), \
         patch.object(loader, "_load_translations", return_value=(0, [])) as load_tr, \
         patch.object(loader, "prune_orphan_collections", return_value=0), \
         patch.object(loader, "assign_slugs", return_value=0), \
         patch.object(loader, "_write_rejects"):
        counts = loader.load_collections(DATE, bucket="b")
    return order, counts, load_tr


def test_children_load_in_fk_order_part_translations_bracket_the_parts():
    order, counts, _ = _run_load(_silver_for([119, 645]), known_ids=[119, 645])

    assert [t for t, _, _ in order] == [
        "collection_part_translation",   # emptied first: its FK points at a part
        "dim_collection_part",
        "dim_collection_image",
        "collection_part_translation",   # refilled last, once the parts exist again
    ]
    assert order[0][2] == 0 and order[3][2] == 2
    assert counts["dim_collection_part"] == 2


def test_only_collections_in_todays_silver_are_replaced_so_a_failed_fetch_keeps_its_rows():
    """Collection 645's fetch failed tonight: it has no Silver row, so it is in no
    replace scope and yesterday's parts, images and translations stay untouched."""
    order, _, load_tr = _run_load(_silver_for([119]), known_ids=[119, 645])

    assert all(scope == [119] for _, scope, _ in order)
    assert load_tr.call_args.kwargs["scope_ids"] == [119]


def test_a_silver_collection_with_no_dimension_row_is_not_loaded_and_is_quarantined():
    order, _, _ = _run_load(_silver_for([119, 777]), known_ids=[119])
    assert all(scope == [119] for _, scope, _ in order)

    df = _silver_for([777])["collections"]
    session = MagicMock()
    with patch.object(loader, "_upsert", return_value=0):
        count, rejects = loader.load_collection_details(session, df, valid_ids={119})
    assert count == 0 and rejects[0]["rejection_reason"] == "unknown collection_id"


def test_a_collection_with_no_name_is_quarantined_not_upserted():
    df = _silver_for([119])["collections"].assign(name=None)
    with patch.object(loader, "_upsert", return_value=0) as upsert:
        count, rejects = loader.load_collection_details(MagicMock(), df, valid_ids={119})
    assert count == 0 and rejects[0]["rejection_reason"] == "missing name"
    assert upsert.call_args.args[4] == []


def test_part_rows_reach_the_database_as_plain_python_values():
    """genre_ids comes back from Parquet as an ndarray and ids as numpy/NA scalars;
    psycopg2 adapts none of those."""
    parts = _silver_for([119])["collection_parts"]
    row = loader._rows(parts, loader._PART_COLS)[0]
    assert row["genre_ids"] == [12, 14, 28] and type(row["genre_ids"]) is list
    assert type(row["collection_id"]) is int and type(row["position"]) is int
    assert row["adult"] is False
    blank = parts.assign(release_date=None, vote_count=pd.array([None], dtype="Int64"))
    assert loader._rows(blank, loader._PART_COLS)[0]["vote_count"] is None


def test_prune_deletes_children_first_then_the_collection_and_reports_the_count():
    session = MagicMock()
    session.execute.return_value.rowcount = 24

    assert loader.prune_orphan_collections(session) == 24

    statements = [str(c.args[0]) for c in session.execute.call_args_list]
    tables = [s.split("DELETE FROM ")[1].split(" ")[0] for s in statements]
    assert tables == [
        "collection_part_translation", "dim_collection_part", "dim_collection_image",
        "collection_translation", "dim_collection",
    ]
    assert all("NOT EXISTS (SELECT 1 FROM dim_movie" in s for s in statements)


def test_prune_is_idempotent_when_nothing_is_orphaned():
    session = MagicMock()
    session.execute.return_value.rowcount = 0
    assert loader.prune_orphan_collections(session) == 0
