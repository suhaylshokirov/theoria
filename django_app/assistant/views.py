"""JSON endpoints for the in-page AI Movie Companion."""

from __future__ import annotations

import json

from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from assistant.conversations import (
    create_session,
    get_session,
    recent_recommendations,
    recent_sessions,
    recent_turns,
    record_recommendations,
    record_turn,
)
from assistant.gemini import generate_companion_reply, plan_companion_message
from assistant.models import ChatSession
from core.recommendations import select_movie_candidates
from core.services import build_taste_summary, record_title_feedback

MAX_MESSAGE_LENGTH = 300
FEEDBACK_ACTIONS = {
    "watched": {"watched": True},
    "not_interested": {"not_interested": True},
}


def _json_body(request):
    try:
        return json.loads(request.body)
    except (TypeError, json.JSONDecodeError):
        return None


def _sign_in_required():
    return JsonResponse(
        {"error": "Sign in to get recommendations based on your lists."},
        status=401,
    )


def _recommendation_response(candidate):
    url = None
    if candidate.get("slug"):
        url = reverse("movies:movie_detail", kwargs={"movie_slug": candidate["slug"]})
    return {
        "content_id": candidate["content_id"],
        "content_type": candidate["content_type"],
        "title": candidate["title"],
        "url": url,
        "status": candidate["status"],
        "reason": candidate["reason"],
    }


def _conversation_summary(session):
    return {
        "id": str(session.pk),
        "title": session.title or _("New chat"),
        "updated_at": session.updated_at.isoformat(),
    }


def _owned_session_or_404(user, session_id):
    try:
        return user.assistant_sessions.get(pk=session_id)
    except (ChatSession.DoesNotExist, ValueError):
        raise Http404("Chat not found")


@require_GET
def conversations(request):
    if not request.user.is_authenticated:
        return _sign_in_required()
    return JsonResponse(
        {"conversations": [_conversation_summary(session) for session in recent_sessions(request.user)]}
    )


@require_POST
def new_conversation(request):
    if not request.user.is_authenticated:
        return _sign_in_required()
    return JsonResponse({"conversation": _conversation_summary(create_session(request.user))}, status=201)


@require_GET
def conversation_detail(request, session_id):
    if not request.user.is_authenticated:
        return _sign_in_required()
    try:
        session = _owned_session_or_404(request.user, session_id)
    except Http404:
        return JsonResponse({"error": "Chat not found."}, status=404)
    return JsonResponse(
        {
            "conversation": _conversation_summary(session),
            "turns": list(session.turns.order_by("created_at").values("role", "message")),
        }
    )


@require_POST
def chat(request):
    if not request.user.is_authenticated:
        return _sign_in_required()

    payload = _json_body(request)
    message = payload.get("message", "").strip() if isinstance(payload, dict) else ""
    if not message:
        return JsonResponse({"error": "Tell the guide what you feel like watching."}, status=400)
    if len(message) > MAX_MESSAGE_LENGTH:
        return JsonResponse({"error": "Keep your message under 300 characters."}, status=400)

    session = get_session(request.user, payload.get("session_id"))
    conversation = recent_turns(session)
    shown_titles = recent_recommendations(session)
    record_turn(session, "user", message)
    plan = plan_companion_message(message, conversation, shown_titles)

    if plan.needs_recommendations:
        shown_movie_ids = {
            item["content_id"] for item in shown_titles if item["content_type"] == "movie"
        }
        candidates = select_movie_candidates(
            request.user, message, exclude_movie_ids=shown_movie_ids
        )
        if candidates:
            taste_summary = build_taste_summary(request.user)
            reply = generate_companion_reply(
                message, taste_summary, candidates, recent_turns=conversation
            )
            reply = reply or "Here are a few picks from Theoria that fit your request."
            record_recommendations(session, candidates)
        else:
            reply = "I could not find a new match with those limits. Try a wider genre or a little more time."
    elif plan.intent == "taste_assessment":
        taste_summary = build_taste_summary(request.user)
        reply = generate_companion_reply(
            message, taste_summary, [], recent_turns=conversation
        )
        reply = reply or "Like or watch a few more movies and ask me again — I need more to go on."
        candidates = []
    else:
        reply = plan.reply or "Tell me a little more about what you would like to watch."
        candidates = []

    record_turn(session, "assistant", reply, intent=plan.intent)
    return JsonResponse(
        {
            "reply": reply or "Here are a few picks from Theoria that fit your request.",
            "recommendations": [_recommendation_response(candidate) for candidate in candidates],
            "session_id": str(session.pk),
            "intent": plan.intent,
        }
    )


@require_POST
def feedback(request):
    if not request.user.is_authenticated:
        return _sign_in_required()

    payload = _json_body(request)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "That feedback could not be read."}, status=400)

    action = payload.get("action")
    if action not in FEEDBACK_ACTIONS:
        return JsonResponse({"error": "Unknown feedback action."}, status=400)
    content_type = payload.get("content_type")
    content_id = payload.get("content_id")
    if not isinstance(content_id, int):
        return JsonResponse({"error": "Unknown movie."}, status=400)

    try:
        record_title_feedback(
            request.user,
            content_type,
            content_id,
            **FEEDBACK_ACTIONS[action],
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    messages = {
        "watched": "Marked as watched. I will not recommend it again by default.",
        "not_interested": "Got it. I will leave this one out of future picks.",
    }
    return JsonResponse({"message": messages[action]})
