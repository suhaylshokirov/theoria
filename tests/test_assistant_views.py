"""Tests for the rule-based assistant HTTP endpoints."""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.utils import timezone  # noqa: E402
from django.urls import reverse  # noqa: E402

from assistant.gemini import ConversationPlan  # noqa: E402
from assistant.models import ChatSession, ChatTurn, RecommendationEvent  # noqa: E402

User = get_user_model()
_TEST_EMAIL = "assistant-view-test@example.com"


def _candidate():
    return {
        "content_id": 101,
        "content_type": "movie",
        "title": "Arrival",
        "slug": "arrival",
        "status": "new",
        "reason": "It matches your requested genre.",
    }


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_chat_requires_a_signed_in_user():
    response = Client().post(
        reverse("assistant:chat"),
        data=json.dumps({"message": "Something funny"}),
        content_type="application/json",
    )

    assert response.status_code == 401
    assert "Sign in" in response.json()["error"]


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_recent_conversations_are_private_and_returned_newest_first():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    other_user = User.objects.create_user(
        email="other-assistant-view-test@example.com", username="other-assistant-reader"
    )
    older = ChatSession.objects.create(user=user, title="Older chat")
    ChatSession.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(days=1))
    newer = ChatSession.objects.create(user=user, title="Newer chat")
    ChatSession.objects.create(user=other_user, title="Private chat")
    client = Client()
    client.force_login(user)
    try:
        response = client.get(reverse("assistant:conversations"))

        assert response.status_code == 200
        conversations = response.json()["conversations"]
        assert [item["id"] for item in conversations] == [str(newer.pk), str(older.pk)]
        assert [item["title"] for item in conversations] == ["Newer chat", "Older chat"]
    finally:
        User.objects.filter(email__in=[_TEST_EMAIL, "other-assistant-view-test@example.com"]).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_new_conversation_keeps_only_the_three_most_recent_chats():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    oldest = ChatSession.objects.create(user=user, title="Oldest")
    ChatSession.objects.filter(pk=oldest.pk).update(updated_at=timezone.now() - timedelta(days=1))
    ChatSession.objects.create(user=user, title="Second")
    ChatSession.objects.create(user=user, title="Third")
    client = Client()
    client.force_login(user)
    try:
        response = client.post(reverse("assistant:new_conversation"), data="{}", content_type="application/json")

        assert response.status_code == 201
        assert ChatSession.objects.filter(user=user).count() == 3
        assert not ChatSession.objects.filter(pk=oldest.pk).exists()
        assert response.json()["conversation"]["title"] == "New chat"
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_conversation_detail_restores_only_the_owner_messages():
    User.objects.filter(email__in=[_TEST_EMAIL, "other-assistant-view-test@example.com"]).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    session = ChatSession.objects.create(user=user, title="Funny movies")
    ChatTurn.objects.create(session=session, role="user", message="Something fun")
    ChatTurn.objects.create(session=session, role="assistant", message="Try a comedy.")
    other_user = User.objects.create_user(
        email="other-assistant-view-test@example.com", username="other-assistant-reader"
    )
    other_session = ChatSession.objects.create(user=other_user, title="Private chat")
    client = Client()
    client.force_login(user)
    try:
        response = client.get(reverse("assistant:conversation_detail", kwargs={"session_id": session.pk}))
        private_response = client.get(
            reverse("assistant:conversation_detail", kwargs={"session_id": other_session.pk})
        )

        assert response.status_code == 200
        assert response.json()["conversation"]["title"] == "Funny movies"
        assert response.json()["turns"] == [
            {"role": "user", "message": "Something fun"},
            {"role": "assistant", "message": "Try a comedy."},
        ]
        assert private_response.status_code == 404
    finally:
        User.objects.filter(email__in=[_TEST_EMAIL, "other-assistant-view-test@example.com"]).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_chat_returns_rule_based_recommendations_for_signed_in_user():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    client = Client()
    client.force_login(user)
    try:
        with (
            patch("assistant.views.select_movie_candidates", return_value=[_candidate()]),
            patch("assistant.views.build_taste_summary", return_value={}),
            patch(
                "assistant.views.plan_companion_message",
                return_value=ConversationPlan("recommend", ""),
            ),
            patch(
                "assistant.views.generate_companion_reply",
                return_value="Arrival is a thoughtful, personal pick for tonight.",
            ),
        ):
            response = client.post(
                reverse("assistant:chat"),
                data=json.dumps({"message": "Something funny"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["recommendations"] == [
            {
                "content_id": 101,
                "content_type": "movie",
                "title": "Arrival",
                "url": "/movies/arrival/",
                "status": "new",
                "reason": "It matches your requested genre.",
            }
        ]
        assert payload["reply"] == "Arrival is a thoughtful, personal pick for tonight."
        assert payload["intent"] == "recommend"
        assert payload["session_id"]
        assert ChatTurn.objects.filter(role="user", message="Something funny").exists()
        assert ChatTurn.objects.filter(role="assistant", intent="recommend").exists()
        assert RecommendationEvent.objects.filter(content_id=101).exists()
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_chat_skips_recommendations_for_a_taste_assessment_request():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    client = Client()
    client.force_login(user)
    try:
        with (
            patch("assistant.views.select_movie_candidates") as select_candidates,
            patch("assistant.views.build_taste_summary", return_value={}),
            patch(
                "assistant.views.plan_companion_message",
                return_value=ConversationPlan("taste_assessment", ""),
            ),
            patch(
                "assistant.views.generate_companion_reply",
                return_value="You lean toward thoughtful sci-fi and steer clear of horror.",
            ) as generate_reply,
        ):
            response = client.post(
                reverse("assistant:chat"),
                data=json.dumps({"message": "What do you think of my taste in movies?"}),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["recommendations"] == []
        assert payload["reply"] == "You lean toward thoughtful sci-fi and steer clear of horror."
        select_candidates.assert_not_called()
        generate_reply.assert_called_once_with(
            "What do you think of my taste in movies?", {}, [], recent_turns=[]
        )
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_chat_handles_a_repeat_concern_without_new_recommendations():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    session = ChatSession.objects.create(user=user)
    RecommendationEvent.objects.create(
        session=session, content_type="movie", content_id=101, title="Arrival"
    )
    client = Client()
    client.force_login(user)
    try:
        with (
            patch("assistant.views.select_movie_candidates") as select_candidates,
            patch(
                "assistant.views.plan_companion_message",
                return_value=ConversationPlan(
                    "repeat_concern",
                    "You’re right — I repeated myself. I will avoid those titles from here.",
                ),
            ),
        ):
            response = client.post(
                reverse("assistant:chat"),
                data=json.dumps(
                    {
                        "message": "Why do you keep recommending the same movies?",
                        "session_id": str(session.pk),
                    }
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["intent"] == "repeat_concern"
        assert payload["recommendations"] == []
        assert "repeated myself" in payload["reply"]
        assert payload["session_id"] == str(session.pk)
        select_candidates.assert_not_called()
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_chat_excludes_titles_already_shown_in_the_same_session():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    session = ChatSession.objects.create(user=user)
    RecommendationEvent.objects.create(
        session=session, content_type="movie", content_id=101, title="Arrival"
    )
    client = Client()
    client.force_login(user)
    try:
        with (
            patch(
                "assistant.views.plan_companion_message",
                return_value=ConversationPlan("recommend", ""),
            ),
            patch("assistant.views.select_movie_candidates", return_value=[]) as select_candidates,
        ):
            response = client.post(
                reverse("assistant:chat"),
                data=json.dumps(
                    {"message": "Show me another smart sci-fi movie", "session_id": str(session.pk)}
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert response.json()["recommendations"] == []
        select_candidates.assert_called_once_with(
            user, "Show me another smart sci-fi movie", exclude_movie_ids={101}
        )
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_feedback_records_the_selected_action():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="assistant-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("assistant.views.record_title_feedback") as record_feedback:
            response = client.post(
                reverse("assistant:feedback"),
                data=json.dumps(
                    {"action": "watched", "content_type": "movie", "content_id": 101}
                ),
                content_type="application/json",
            )

        assert response.status_code == 200
        assert "Marked as watched" in response.json()["message"]
        record_feedback.assert_called_once_with(user, "movie", 101, watched=True)
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()
