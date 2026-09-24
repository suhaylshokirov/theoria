"""Shared handling of TMDB's `translations` block (Task 93).

`append_to_response=translations` on `GET /movie/{id}` and `GET /person/{id}`
adds ``"translations": {"id": ..., "translations": [{iso_639_1, iso_3166_1,
name, english_name, data: {...}}, ...]}`` — one entry per language TMDB has
*any* text in, ~50-75 of them per film. The site ships two (ru, uz).

Two jobs live here so Bronze and Silver agree on them:

* ``trim_translations()`` — applied at **Bronze write time**. A film payload is
  ~65 KB heavier with all languages and a person's is ~70 KB heavier (against a
  ~3 KB base). Persons are re-read *cumulatively* by Silver every night
  (``transform_people_details`` sweeps every partition), so keeping all ~75
  languages would turn a 100 MB nightly download into ~5 GB. The block keeps its
  exact TMDB shape, just with the languages we do not ship dropped; the trimmed
  payload is still a valid, unmodified-otherwise TMDB response. Adding a
  language later means adding it to TRANSLATION_LANGUAGES: films pick it up on
  the next nightly refresh, people need a ``--no-skip-existing`` backfill.
* ``select_translations()`` — applied at **Silver**. Picks one entry per shipped
  language and normalises empty strings to None. Returns None (not ``{}``) when
  the payload has no ``translations`` key at all, so callers can tell a
  partition written before Task 93 (Bronze is immutable; it cannot be
  backfilled) from a film that genuinely has no Russian or Uzbek text.
"""

from __future__ import annotations

from typing import Any

# Languages the site ships translated data for, as bare ISO-639-1 codes. This is
# the value stored in every *_translation.lang column.
TRANSLATION_LANGUAGES = ("ru", "uz")


def _clean(value: Any) -> str | None:
    """Strip a text field; "" / whitespace / null / a non-string all become None."""
    return value.strip() or None if isinstance(value, str) else None


def trim_translations(payload: dict[str, Any]) -> dict[str, Any]:
    """Return `payload` with its translations block cut down to TRANSLATION_LANGUAGES.

    A payload without a ``translations`` block is returned unchanged. The input
    is not mutated.
    """
    block = payload.get("translations")
    if not isinstance(block, dict):
        return payload
    kept = [
        entry for entry in block.get("translations") or []
        if entry.get("iso_639_1") in TRANSLATION_LANGUAGES
    ]
    return {**payload, "translations": {**block, "translations": kept}}


def select_translations(
    payload: dict[str, Any], fields: tuple[str, ...]
) -> dict[str, dict[str, str | None]] | None:
    """Pick one ``{field: text}`` dict per shipped language from a TMDB payload.

    `fields` names the keys to lift out of each entry's ``data`` object
    (``("title", "overview", "tagline")`` for a film, ``("biography",)`` for a
    person). Text is stripped and an empty string becomes None — TMDB returns
    ``""`` for a field it has no text for, never null. A language whose every
    requested field is empty is omitted entirely, so no all-null row is written.

    TMDB can list more than one entry per language (a regional variant such as
    ``ru-BY`` beside ``ru-RU``). The entry whose country matches the language's
    home country (``ru``→``RU``, ``uz``→``UZ``) wins; otherwise the first one
    seen does.

    Returns None when the payload has no ``translations`` block at all.
    """
    block = payload.get("translations")
    if not isinstance(block, dict):
        return None

    best: dict[str, tuple[int, dict[str, str | None]]] = {}
    for entry in block.get("translations") or []:
        lang = entry.get("iso_639_1")
        if lang not in TRANSLATION_LANGUAGES:
            continue
        data = entry.get("data") or {}
        values = {f: _clean(data.get(f)) for f in fields}
        if all(v is None for v in values.values()):
            continue
        home = (entry.get("iso_3166_1") or "").upper() == lang.upper()
        rank = 0 if home else 1
        if lang not in best or rank < best[lang][0]:
            best[lang] = (rank, values)
    return {lang: values for lang, (_, values) in best.items()}
