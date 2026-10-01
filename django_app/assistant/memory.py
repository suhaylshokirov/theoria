"""Chat-scoped preferences that must survive a long conversation."""

from __future__ import annotations

import re

from core.recommendations import GENRE_WORDS, read_request_constraints


EMPTY_CHAT_MEMORY = {"excluded_genres": [], "max_runtime": None}


def _words_for(genre):
    return tuple(word for word, value in GENRE_WORDS.items() if value == genre)


def _has_phrase(message, phrases):
    return any(re.search(phrase, message) for phrase in phrases)


def _blocks_genre(message, word):
    escaped = re.escape(word)
    return _has_phrase(
        message,
        (
            rf"\bno\s+{escaped}\b",
            rf"\bnot\s+{escaped}\b",
            rf"\bwithout\s+{escaped}\b",
            rf"\bavoid\s+{escaped}\b",
            rf"\b(?:do not|don't)\s+(?:want|recommend|show).*?\b{escaped}\b",
            rf"\bnot in (?:the )?mood for\s+{escaped}\b",
            rf"\banything but\s+{escaped}\b",
        ),
    )


def _allows_genre(message, word):
    escaped = re.escape(word)
    return _has_phrase(
        message,
        (
            rf"\b{escaped}\s+(?:is|are)\s+(?:okay|ok|fine|allowed)\b",
            rf"\b(?:actually\s+)?(?:i )?(?:want|would like|am looking for)\s+.*?\b{escaped}\b",
            rf"\b(?:you can|please)\s+(?:recommend|show).*?\b{escaped}\b",
        ),
    )


def normalise_chat_memory(value):
    """Return only the small, enforceable memory shape trusted by the app."""
    value = value if isinstance(value, dict) else {}
    excluded = value.get("excluded_genres", [])
    excluded = excluded if isinstance(excluded, list) else []
    known_genres = set(GENRE_WORDS.values())
    max_runtime = value.get("max_runtime")
    if not isinstance(max_runtime, int) or max_runtime <= 0:
        max_runtime = None
    return {
        "excluded_genres": sorted({genre for genre in excluded if genre in known_genres}),
        "max_runtime": max_runtime,
    }


def update_chat_memory(session, message):
    """Apply explicit user instructions to this chat's lasting rules.

    Only direct language creates a hard rule. A later direct request is allowed
    to remove an older rule, so a person can change their mind inside a chat.
    """
    memory = normalise_chat_memory(session.memory)
    normalized_message = " ".join(message.lower().split())
    excluded = set(memory["excluded_genres"])
    for genre in set(GENRE_WORDS.values()):
        words = _words_for(genre)
        if any(_blocks_genre(normalized_message, word) for word in words):
            excluded.add(genre)
        elif any(_allows_genre(normalized_message, word) for word in words):
            excluded.discard(genre)

    request_constraints = read_request_constraints(message)
    if request_constraints["max_runtime"] is not None:
        memory["max_runtime"] = request_constraints["max_runtime"]
    elif _has_phrase(
        normalized_message,
        (r"\bno\s+(?:time|runtime)\s+limit\b", r"\bany\s+length\s+is\s+fine\b"),
    ):
        memory["max_runtime"] = None

    memory["excluded_genres"] = sorted(excluded)
    session.memory = memory
    session.save(update_fields=["memory", "updated_at"])
    return memory
