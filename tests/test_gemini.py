"""Tests for the Gemini explanation layer without making internet calls."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from assistant.gemini import generate_companion_reply  # noqa: E402


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
