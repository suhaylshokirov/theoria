"""Short-lived, user-owned conversation state for the movie companion."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class ChatSession(models.Model):
    """One private conversation, scoped to its signed-in reader."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_sessions"
    )
    title = models.CharField(max_length=80, blank=True)
    memory = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-updated_at"])]
        ordering = ["-updated_at"]


class ChatTurn(models.Model):
    """A message retained only to make the next few turns coherent."""

    USER = "user"
    ASSISTANT = "assistant"
    ROLES = ((USER, "User"), (ASSISTANT, "Assistant"))

    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="turns")
    role = models.CharField(max_length=10, choices=ROLES)
    message = models.TextField()
    intent = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["session", "created_at"])]
        ordering = ["created_at"]


class RecommendationEvent(models.Model):
    """A trusted record of a title shown in one conversation.

    The title is a display snapshot rather than a warehouse relationship: the
    catalogue is on a separate, read-only database, so cross-database foreign
    keys are deliberately not possible here.
    """

    session = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="recommendation_events"
    )
    content_type = models.CharField(max_length=10)
    content_id = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    shown_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["session", "shown_at"]),
            models.Index(fields=["session", "content_type", "content_id"]),
        ]
