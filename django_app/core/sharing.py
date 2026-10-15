"""Sharing a collection by public, unlisted link.

A collection is private until its owner enables sharing, which mints a random
token; `/s/<token>/` (Task 137) then shows the *live* list read-only. Rotating
replaces the token, and stopping keeps the dead token so the old link can say
"gone" (410) rather than "never existed" (404) -- `shared_at` is what says
whether a token is live.

Everything here touches the `default` database only; the warehouse is read
through `core.services._hydrate`, exactly as /me does.
"""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext

from core.models import Collection
from core.services import _hydrate, order_rows

TOKEN_LENGTH = 10
NOTE_MAX_LENGTH = 140
# Letters and digits only: a token is pasted into chats and read aloud, and
# `-`/`_` from token_urlsafe get eaten by line wraps and markdown.
_TOKEN_ALPHABET = string.ascii_letters + string.digits
_MAX_TOKEN_ATTEMPTS = 5


class ShareError(ValueError):
    """A share request the owner can fix; the message is shown to them."""


@dataclass(frozen=True)
class SharedCollection:
    collection: Collection
    owner: object
    items: list


def new_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def share_path(token: str) -> str:
    return f"/s/{token}/"


def clean_note(note) -> str:
    """One line, trimmed, at most NOTE_MAX_LENGTH characters.

    A too-long note is refused, not silently cut -- the owner would otherwise
    publish a sentence that stops mid-word.
    """
    text = re.sub(r"\s+", " ", note or "").strip()
    if len(text) > NOTE_MAX_LENGTH:
        raise ShareError(
            gettext("Keep the note to %(max)d characters.") % {"max": NOTE_MAX_LENGTH}
        )
    return text


def _owned(user, kind) -> Collection:
    if kind not in dict(Collection.KINDS):
        raise ShareError(gettext("Unknown collection."))
    collection = Collection.objects.filter(user=user, kind=kind).first()
    if collection is None:
        raise ShareError(gettext("Add something to this collection before sharing it."))
    return collection


def _save_with_new_token(collection: Collection, **fields) -> None:
    """Assign a fresh token and save. The unique constraint is the arbiter:
    on a (vanishingly rare) collision, draw again."""
    for _ in range(_MAX_TOKEN_ATTEMPTS):
        collection.share_token = new_token()
        collection.shared_at = timezone.now()
        for name, value in fields.items():
            setattr(collection, name, value)
        try:
            with transaction.atomic():
                collection.save()
            return
        except IntegrityError:
            continue
    raise ShareError(gettext("Could not create a link just now. Please try again."))


def enable_share(user, kind, note=None) -> Collection:
    """Make the collection public. Idempotent: an already-shared collection
    keeps its token (so a double click doesn't break a link already pasted
    somewhere) and only takes the new note. `note=None` leaves the note be."""
    collection = _owned(user, kind)
    text = collection.share_note if note is None else clean_note(note)
    if not collection.items.exists():
        raise ShareError(gettext("Add something to this collection before sharing it."))
    if collection.share_token and collection.shared_at:
        if text != collection.share_note:
            collection.share_note = text
            collection.save(update_fields=["share_note", "updated_at"])
        return collection
    # First share, or sharing again after a stop: always a fresh token, so a
    # revoked link can never come back to life.
    _save_with_new_token(collection, share_note=text)
    return collection


def rotate_share(user, kind, note=None) -> Collection:
    """Replace the live link with a new one; the old link then answers 404."""
    collection = _owned(user, kind)
    if not (collection.share_token and collection.shared_at):
        raise ShareError(gettext("This collection is not shared."))
    fields = {} if note is None else {"share_note": clean_note(note)}
    _save_with_new_token(collection, **fields)
    return collection


def disable_share(user, kind) -> Collection:
    """Stop sharing. The token is kept (not shown anywhere) so the old link
    answers 410; the note stays for the next time the owner shares."""
    collection = _owned(user, kind)
    if collection.shared_at is not None:
        collection.shared_at = None
        collection.save(update_fields=["shared_at", "updated_at"])
    return collection


def _live(token):
    if not token:
        return None
    return (
        Collection.objects.select_related("user")
        .filter(share_token=token, shared_at__isnull=False, user__is_active=True)
        .first()
    )


def shared_collection(token) -> SharedCollection | None:
    """The collection behind a live token with its titles hydrated and in
    display order, or None -- unknown, revoked, or the owner's account is
    inactive. Items whose title left the warehouse are dropped, as on /me."""
    collection = _live(token)
    if collection is None:
        return None
    rows = _hydrate(list(collection.items.all()))
    return SharedCollection(
        collection=collection,
        owner=collection.user,
        items=order_rows(collection.kind, rows),
    )


def is_revoked(token) -> bool:
    """True for a token its owner stopped sharing (the page answers 410)."""
    if not token:
        return False
    return Collection.objects.filter(
        share_token=token, shared_at__isnull=True, user__is_active=True
    ).exists()
