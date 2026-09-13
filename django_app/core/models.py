"""Personal-list models: a user's Liked / Watch later / Top collections.

These models intentionally live on Django's ``default`` database. The movie
warehouse is a separate, read-only connection and its integer content IDs are
referenced without cross-database foreign keys.

Identity (the `User` model, email codes) lives in the `accounts` app —
`Collection` references it only through `settings.AUTH_USER_MODEL`, never by
importing a concrete model class, so this app has no import-time dependency
on which app owns `AUTH_USER_MODEL`.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Collection(models.Model):
    LIKED = "liked"
    WATCH_LATER = "watch_later"
    TOP = "top"
    KINDS = (
        (LIKED, "Liked"),
        (WATCH_LATER, "Watch later"),
        (TOP, "Top"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="collections"
    )
    kind = models.CharField(max_length=20, choices=KINDS)
    name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "kind"], name="one_collection_kind_per_user"),
        ]
        ordering = ["kind"]

    def __str__(self):
        return f"{self.user.username}: {self.name}"


class CollectionItem(models.Model):
    MOVIE = "movie"
    SERIES = "series"
    CONTENT_TYPES = ((MOVIE, "Movie"), (SERIES, "TV show"))

    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="items")
    content_type = models.CharField(max_length=10, choices=CONTENT_TYPES)
    content_id = models.PositiveIntegerField()
    position = models.PositiveIntegerField(default=0)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "content_type", "content_id"],
                name="one_content_per_collection",
            ),
        ]
        indexes = [
            models.Index(fields=["collection", "position"]),
            models.Index(fields=["content_type", "content_id"]),
        ]
        ordering = ["position", "-added_at"]
