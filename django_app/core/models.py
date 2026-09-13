"""Durable application models for Theoria accounts and personal lists.

These models intentionally live on Django's ``default`` database. The movie
warehouse is a separate, read-only connection and its integer content IDs are
referenced without cross-database foreign keys.
"""

from __future__ import annotations

import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, username, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        if not username:
            raise ValueError("A username is required.")
        user = self.model(
            email=self.normalize_email(email).casefold(),
            username=username,
            **extra_fields,
        )
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, username, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, username, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=30, unique=True)
    date_joined = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.username


class EmailCode(models.Model):
    SIGNUP = "signup"
    LOGIN = "login"
    PURPOSES = ((SIGNUP, "Sign up"), (LOGIN, "Log in"))

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(db_index=True)
    purpose = models.CharField(max_length=16, choices=PURPOSES)
    username = models.CharField(max_length=30, blank=True)
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    request_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["email", "purpose", "created_at"]),
            models.Index(fields=["request_ip", "created_at"]),
        ]


class Collection(models.Model):
    LIKED = "liked"
    WATCH_LATER = "watch_later"
    TOP = "top"
    KINDS = (
        (LIKED, "Liked"),
        (WATCH_LATER, "Watch later"),
        (TOP, "Top"),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="collections")
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
