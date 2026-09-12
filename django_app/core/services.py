"""Application services for email-code authentication and personal lists."""

from __future__ import annotations

import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import Collection, CollectionItem, EmailCode, User

CODE_TTL = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
EMAIL_WINDOW = timedelta(hours=1)
MAX_CODES_PER_HOUR = 8
MAX_CODES_PER_IP_HOUR = 30
MAX_ATTEMPTS = 5
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,29}$")
RESERVED_USERNAMES = {"admin", "account", "accounts", "auth", "login", "logout", "theoria"}


class CodeRequestError(Exception):
    """Raised when a code cannot safely be issued."""


class CodeVerificationError(Exception):
    """Raised when a code is invalid, expired or already consumed."""


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def validate_username(value: str) -> str:
    username = value.strip()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Use 3–30 letters, numbers, dots, hyphens or underscores.")
    if username.casefold() in RESERVED_USERNAMES:
        raise ValueError("That username is reserved.")
    if User.objects.filter(username__iexact=username).exists():
        raise ValueError("That username is already in use.")
    return username


def validate_and_normalize_email(value: str) -> str:
    email = normalize_email(value)
    validate_email(email)
    return email


def safe_next(candidate: str | None) -> str:
    """Allow only a host-relative path after authentication."""
    candidate = candidate or "/"
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or urlsplit(candidate).scheme
        or urlsplit(candidate).netloc
    ):
        return "/"
    return candidate


def issue_email_code(*, email, purpose, username="", request_ip=None):
    email = validate_and_normalize_email(email)
    now = timezone.now()
    recent = EmailCode.objects.filter(email=email, created_at__gte=now - EMAIL_WINDOW)
    if recent.count() >= MAX_CODES_PER_HOUR:
        raise CodeRequestError("Too many codes requested. Try again later.")
    if request_ip:
        ip_recent = EmailCode.objects.filter(
            request_ip=request_ip, created_at__gte=now - EMAIL_WINDOW
        )
        if ip_recent.count() >= MAX_CODES_PER_IP_HOUR:
            raise CodeRequestError("Too many codes requested. Try again later.")
    if EmailCode.objects.filter(
        email=email,
        purpose=purpose,
        created_at__gte=now - RESEND_COOLDOWN,
        consumed_at__isnull=True,
    ).exists():
        raise CodeRequestError("A code was already sent. Check your email or wait a minute.")

    raw_code = f"{secrets.randbelow(1_000_000):06d}"
    with transaction.atomic():
        EmailCode.objects.filter(
            email=email, purpose=purpose, consumed_at__isnull=True
        ).update(consumed_at=now)
        challenge = EmailCode.objects.create(
            email=email,
            purpose=purpose,
            username=username,
            code_hash=make_password(raw_code),
            expires_at=now + CODE_TTL,
            request_ip=request_ip,
        )

    # Login challenges for unknown addresses are still recorded so the public
    # response does not reveal whether an account exists, but no mail is sent.
    should_send = purpose == EmailCode.SIGNUP or User.objects.filter(email=email).exists()
    if should_send:
        send_mail(
            subject="Your Theoria sign-in code",
            message=(
                f"Your Theoria verification code is {raw_code}.\n\n"
                "It expires in 10 minutes and can be used once."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
        )
    return challenge


def verify_email_code(challenge_id, raw_code):
    code = str(raw_code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise CodeVerificationError("Enter the six-digit code from your email.")

    now = timezone.now()
    with transaction.atomic():
        try:
            challenge = EmailCode.objects.select_for_update().get(pk=challenge_id)
        except EmailCode.DoesNotExist as exc:
            raise CodeVerificationError("That code is no longer available.") from exc

        if challenge.consumed_at or challenge.expires_at <= now:
            raise CodeVerificationError("That code has expired. Request a new one.")
        if challenge.attempts >= MAX_ATTEMPTS:
            raise CodeVerificationError("Too many attempts. Request a new code.")

        challenge.attempts += 1
        if not check_password(code, challenge.code_hash):
            challenge.save(update_fields=["attempts"])
            if challenge.attempts >= MAX_ATTEMPTS:
                raise CodeVerificationError("Too many attempts. Request a new code.")
            raise CodeVerificationError("That code is not correct.")

        challenge.consumed_at = now
        challenge.save(update_fields=["attempts", "consumed_at"])

        if challenge.purpose == EmailCode.SIGNUP:
            if User.objects.filter(email=challenge.email).exists():
                raise CodeVerificationError("That email already has an account. Sign in instead.")
            user = User.objects.create_user(
                email=challenge.email,
                username=challenge.username,
            )
            ensure_default_collections(user)
        else:
            user = User.objects.filter(email=challenge.email, is_active=True).first()
            if user is None:
                raise CodeVerificationError("That code is not correct.")
    return user


def ensure_default_collections(user):
    names = {
        Collection.LIKED: "Liked",
        Collection.WATCH_LATER: "Watch later",
        Collection.TOP: "Top",
    }
    for kind, name in names.items():
        Collection.objects.get_or_create(user=user, kind=kind, defaults={"name": name})


def _collection(user, kind):
    if kind not in {choice[0] for choice in Collection.KINDS}:
        raise ValueError("Unknown collection.")
    ensure_default_collections(user)
    return Collection.objects.get(user=user, kind=kind)


def _content_exists(content_type, content_id):
    # These imports stay inside the service so core remains independent of the
    # warehouse model module during Django's app-loading phase.
    from movies.models import Movie, Series

    model = Movie if content_type == CollectionItem.MOVIE else Series
    return model.objects.using("warehouse").filter(pk=content_id).exists()


def toggle_collection_item(user, kind, content_type, content_id):
    if content_type not in {CollectionItem.MOVIE, CollectionItem.SERIES}:
        raise ValueError("Unknown content type.")
    if not _content_exists(content_type, content_id):
        raise ValueError("That title is not in the catalogue.")
    collection = _collection(user, kind)
    with transaction.atomic():
        existing = CollectionItem.objects.select_for_update().filter(
            collection=collection,
            content_type=content_type,
            content_id=content_id,
        ).first()
        if existing:
            existing.delete()
            return False
        position = collection.items.aggregate(max_position=Max("position"))["max_position"]
        CollectionItem.objects.create(
            collection=collection,
            content_type=content_type,
            content_id=content_id,
            position=(position + 1 if position is not None else 0),
        )
        return True


def remove_collection_item(user, kind, item_id):
    collection = _collection(user, kind)
    return collection.items.filter(pk=item_id).delete()[0] > 0


def move_collection_item(user, kind, item_id, direction):
    if kind != Collection.TOP or direction not in {"up", "down"}:
        raise ValueError("Only Top items can be reordered.")
    collection = _collection(user, kind)
    with transaction.atomic():
        item = collection.items.select_for_update().get(pk=item_id)
        if direction == "up":
            sibling = collection.items.select_for_update().filter(
                position__lt=item.position
            ).order_by("-position").first()
        else:
            sibling = collection.items.select_for_update().filter(
                position__gt=item.position
            ).order_by("position").first()
        if sibling is None:
            return
        item.position, sibling.position = sibling.position, item.position
        item.save(update_fields=["position"])
        sibling.save(update_fields=["position"])


def collection_rows(user, kind):
    collection = _collection(user, kind)
    items = list(collection.items.all())
    movie_ids = [item.content_id for item in items if item.content_type == CollectionItem.MOVIE]
    series_ids = [item.content_id for item in items if item.content_type == CollectionItem.SERIES]
    from movies.models import Movie, Series

    movies = {
        obj.movie_id: obj
        for obj in Movie.objects.using("warehouse").filter(movie_id__in=movie_ids)
    }
    series = {
        obj.series_id: obj
        for obj in Series.objects.using("warehouse").filter(series_id__in=series_ids)
    }
    rows = []
    for item in items:
        item.content = (
            movies.get(item.content_id)
            if item.content_type == CollectionItem.MOVIE
            else series.get(item.content_id)
        )
        if item.content is not None:
            rows.append(item)
    return collection, rows


def collection_flags(user, content_type, content_id):
    if not user.is_authenticated:
        return {kind: False for kind, _ in Collection.KINDS}
    ensure_default_collections(user)
    selected = set(
        CollectionItem.objects.filter(
            collection__user=user,
            content_type=content_type,
            content_id=content_id,
        ).values_list("collection__kind", flat=True)
    )
    return {kind: kind in selected for kind, _ in Collection.KINDS}
