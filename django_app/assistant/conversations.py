"""Persistence helpers for the assistant's bounded conversation context."""

from __future__ import annotations

from uuid import UUID

from assistant.models import ChatSession, ChatTurn, RecommendationEvent


MAX_CONTEXT_TURNS = 8
MAX_RECENT_RECOMMENDATIONS = 12


def get_session(user, session_id):
    """Return the user's requested session, or begin a private new one.

    A session id is an opaque browser value, never authority on its own: every
    lookup is constrained by the signed-in user so an id from another account
    cannot expose that account's conversation.
    """
    if session_id:
        try:
            parsed_id = UUID(str(session_id))
        except (TypeError, ValueError, AttributeError):
            parsed_id = None
        if parsed_id is not None:
            session = ChatSession.objects.filter(pk=parsed_id, user=user).first()
            if session is not None:
                return session
    return ChatSession.objects.create(user=user)


def recent_turns(session):
    """Return the small, chronological window the model may use as context."""
    turns = list(session.turns.order_by("-created_at")[:MAX_CONTEXT_TURNS])
    turns.reverse()
    return [{"role": turn.role, "message": turn.message} for turn in turns]


def recent_recommendations(session):
    """Return title snapshots and ids already shown in this conversation."""
    events = list(
        session.recommendation_events.order_by("-shown_at")[:MAX_RECENT_RECOMMENDATIONS]
    )
    return [
        {
            "content_type": event.content_type,
            "content_id": event.content_id,
            "title": event.title,
        }
        for event in events
    ]


def record_turn(session, role, message, *, intent=""):
    session.save(update_fields=["updated_at"])
    return ChatTurn.objects.create(session=session, role=role, message=message, intent=intent)


def record_recommendations(session, candidates):
    RecommendationEvent.objects.bulk_create(
        [
            RecommendationEvent(
                session=session,
                content_type=candidate["content_type"],
                content_id=candidate["content_id"],
                title=candidate["title"],
            )
            for candidate in candidates
        ]
    )
