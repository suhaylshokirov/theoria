"""The small, server-side Gemini bridge for Theoria's movie companion."""

from __future__ import annotations

import json
import logging

import config

try:
    from google import genai
except ImportError:  # Keeps local commands usable before dependencies are installed.
    genai = None


logger = logging.getLogger(__name__)
MAX_REPLY_LENGTH = 1_200
PROFILE_TITLES_PER_LIST = 6


def _titles(summary, kind):
    """Keep the provider context useful and deliberately small."""
    return [
        {
            "title": title["title"],
            "year": title.get("year"),
            "personal_rating": title.get("personal_rating"),
        }
        for title in summary.get(kind, [])[:PROFILE_TITLES_PER_LIST]
    ]


def _prompt(message, taste_summary, candidates):
    """Build the only information Theoria shares with Gemini for one reply."""
    context = {
        "request": message,
        "taste": {
            "liked": _titles(taste_summary, "liked"),
            "top": _titles(taste_summary, "top"),
            "watch_later": _titles(taste_summary, "watch_later"),
            "watched": _titles(taste_summary, "watched"),
            "disliked": _titles(taste_summary, "disliked"),
            "not_interested": _titles(taste_summary, "not_interested"),
        },
        "movie_options": [
            {
                "title": candidate["title"],
                "year": candidate.get("year"),
                "runtime_minutes": candidate.get("runtime"),
                "imdb_rating": candidate.get("imdb_rating"),
                "status": candidate["status"],
                "reason": candidate["reason"],
            }
            for candidate in candidates
        ],
    }
    return """You are Theoria's warm, concise movie companion. Explain why the
movie options below fit this person's request and stored taste. Recommend only
movies in movie_options. Never claim to know anything outside this data. If an
option has status 'on_list', say it is already on their Watch later list. Give
one short paragraph (no headings, no markdown list), maximum 100 words.

Theoria context:
""" + json.dumps(context, ensure_ascii=True)


def generate_companion_reply(message, taste_summary, candidates):
    """Return Gemini's explanation, or ``None`` so the caller can use rules.

    Gemini never decides which catalogue records are returned to the browser.
    Theoria selects those records first; this function only improves the human
    explanation attached to that already-safe result.
    """
    if not config.GEMINI_API_KEY or genai is None:
        return None

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        interaction = client.interactions.create(
            model=config.GEMINI_MODEL,
            input=_prompt(message, taste_summary, candidates),
        )
        reply = (interaction.output_text or "").strip()
    except Exception:  # The rule-based assistant remains available on outage/quota errors.
        logger.warning("Gemini movie companion request failed", exc_info=True)
        return None

    return reply[:MAX_REPLY_LENGTH] or None
