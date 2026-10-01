"""Persistence helpers for the assistant's bounded conversation context."""

from __future__ import annotations

from uuid import UUID

from django.db import transaction

from assistant.models import ChatSession, ChatTurn, RecommendationEvent


MAX_RECENT_RECOMMENDATIONS = 12
MAX_SAVED_CHATS = 3


def _title_from_message(message):
    """Turn a first message into a short, useful recent-chat label."""
    title = " ".join(message.split())
    return title if len(title) <= 80 else title[:77].rstrip() + "…"


def create_session(user):
    """Create a private chat and retain only the user's three newest chats."""
    with transaction.atomic():
        session = ChatSession.objects.create(user=user)
        stale_session_ids = list(
            ChatSession.objects.filter(user=user)
            .order_by("-updated_at", "-created_at")
            .values_list("pk", flat=True)[MAX_SAVED_CHATS:]
        )
        if stale_session_ids:
            ChatSession.objects.filter(pk__in=stale_session_ids).delete()
    return session


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
    return create_session(user)


def recent_sessions(user):
    """Return the reader's saved chats, newest first."""
    return ChatSession.objects.filter(user=user).order_by("-updated_at", "-created_at")[:MAX_SAVED_CHATS]


def recent_turns(session):
    """Return every turn from one chat, in the order it was said.

    Chats are deliberately isolated. Their messages remain available for the
    life of the saved chat and disappear only when that chat is removed.
    """
    turns = session.turns.order_by("created_at")
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
    update_fields = ["updated_at"]
    if role == ChatTurn.USER and not session.title:
        session.title = _title_from_message(message)
        update_fields.append("title")
    session.save(update_fields=update_fields)
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
