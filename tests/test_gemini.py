"""Tests for the Gemini explanation layer without making internet calls."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from assistant.gemini import generate_companion_reply, plan_companion_message  # noqa: E402


def _candidate():
    return {
        "content_id": 101,
        "content_type": "movie",
        "title": "Arrival",
        "runtime": 116,
        "year": 2016,
        "imdb_rating": 7.9,
        "status": "new",
        "reason": "It is a highly rated option from the Theoria catalogue.",
    }


def test_gemini_reply_sends_compact_taste_and_candidate_context():
    client = Mock()
    client.interactions.create.return_value = SimpleNamespace(
        output_text="Arrival is a smart, atmospheric choice for tonight."
    )
    provider = SimpleNamespace(Client=Mock(return_value=client))
    summary = {"liked": [{"title": "Blade Runner 2049", "year": 2017}], "top": []}

    with (
        patch("assistant.gemini.config.GEMINI_API_KEY", "test-key"),
        patch("assistant.gemini.genai", provider),
    ):
        reply = generate_companion_reply("I want a smart sci-fi movie", summary, [_candidate()])

    assert reply == "Arrival is a smart, atmospheric choice for tonight."
    prompt = client.interactions.create.call_args.kwargs["input"]
    assert "Blade Runner 2049" in prompt
    assert "Arrival" in prompt
    assert "test-key" not in prompt


def test_gemini_reply_falls_back_when_the_provider_is_unavailable():
    with patch("assistant.gemini.config.GEMINI_API_KEY", ""):
        assert generate_companion_reply("Anything good?", {}, [_candidate()]) is None


def test_gemini_reply_accepts_decimal_ratings_from_the_warehouse():
    client = Mock()
    client.interactions.create.return_value = SimpleNamespace(output_text="Arrival fits.")
    provider = SimpleNamespace(Client=Mock(return_value=client))
    candidate = {**_candidate(), "imdb_rating": Decimal("7.9")}

    with (
        patch("assistant.gemini.config.GEMINI_API_KEY", "test-key"),
        patch("assistant.gemini.genai", provider),
    ):
        reply = generate_companion_reply("Something smart", {}, [candidate])

    assert reply == "Arrival fits."
    assert '"imdb_rating": 7.9' in client.interactions.create.call_args.kwargs["input"]


def test_gemini_reply_for_an_existing_chat_forbids_another_greeting():
    client = Mock()
    client.interactions.create.return_value = SimpleNamespace(output_text="Arrival fits.")
    provider = SimpleNamespace(Client=Mock(return_value=client))

    with (
        patch("assistant.gemini.config.GEMINI_API_KEY", "test-key"),
        patch("assistant.gemini.genai", provider),
    ):
        generate_companion_reply(
            "Something fun",
            {},
            [_candidate()],
            recent_turns=[{"role": "user", "message": "Hi"}],
        )

    prompt = client.interactions.create.call_args.kwargs["input"]
    assert "do not greet, reintroduce" in prompt


def test_gemini_reply_strips_a_repeated_greeting_from_an_existing_chat():
    client = Mock()
    client.interactions.create.return_value = SimpleNamespace(output_text="Hey there! Arrival fits.")
    provider = SimpleNamespace(Client=Mock(return_value=client))

    with (
        patch("assistant.gemini.config.GEMINI_API_KEY", "test-key"),
        patch("assistant.gemini.genai", provider),
    ):
        reply = generate_companion_reply(
            "Something fun",
            {},
            [_candidate()],
            recent_turns=[{"role": "user", "message": "Hi"}],
            chat_memory={"excluded_genres": ["Horror"]},
        )

    assert reply == "Arrival fits."
    assert '"excluded_genres": ["Horror"]' in client.interactions.create.call_args.kwargs["input"]


def test_gemini_conversation_plan_uses_history_and_returns_validated_intent():
    client = Mock()
    client.interactions.create.return_value = SimpleNamespace(
        output_text='{"intent": "repeat_concern", "reply": "You’re right — I will avoid those titles."}'
    )
    provider = SimpleNamespace(Client=Mock(return_value=client))

    with (
        patch("assistant.gemini.config.GEMINI_API_KEY", "test-key"),
        patch("assistant.gemini.genai", provider),
    ):
        plan = plan_companion_message(
            "Why are those the same movies again?",
            [{"role": "assistant", "message": "Try Arrival."}],
            [{"content_id": 101, "content_type": "movie", "title": "Arrival"}],
        )

    assert plan.intent == "repeat_concern"
    assert not plan.needs_recommendations
    request = client.interactions.create.call_args.kwargs
    assert request["response_format"]["mime_type"] == "application/json"
    assert "Arrival" in request["input"]


def test_gemini_conversation_plan_has_a_honest_fallback_for_a_greeting():
    with patch("assistant.gemini.config.GEMINI_API_KEY", ""):
        plan = plan_companion_message("Hi", [], [])

    assert plan.intent == "small_talk"
    assert not plan.needs_recommendations
    assert "tell me" in plan.reply.lower()


def test_gemini_fallback_does_not_greet_again_in_an_existing_chat():
    with patch("assistant.gemini.config.GEMINI_API_KEY", ""):
        plan = plan_companion_message("Hi", [{"role": "user", "message": "Earlier message"}], [])

    assert plan.intent == "small_talk"
    assert not plan.reply.lower().startswith(("hi", "hello", "hey"))
