"""JSON endpoints for the in-page AI Movie Companion."""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from assistant.gemini import generate_companion_reply
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

    candidates = select_movie_candidates(request.user, message)
    if not candidates:
        return JsonResponse(
            {
                "reply": "I could not find a good match with those limits. Try a wider genre or a little more time.",
                "recommendations": [],
            }
        )

    taste_summary = build_taste_summary(request.user)
    reply = generate_companion_reply(message, taste_summary, candidates)
    return JsonResponse(
        {
            "reply": reply or "Here are a few picks from Theoria that fit your request.",
            "recommendations": [_recommendation_response(candidate) for candidate in candidates],
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
