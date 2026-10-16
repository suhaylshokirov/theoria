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
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language, gettext

from core.datacache import cached
from core.models import Collection, CollectionItem
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
    stats: dict


def new_token() -> str:
    return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))


def share_path(token: str) -> str:
    return reverse("share:page", kwargs={"token": token})


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
    """The collection behind a live token with its titles hydrated, in
    display order, and measured -- or None: unknown, revoked, or the owner's
    account is inactive. Items whose title left the warehouse are dropped, as
    on /me.

    The hydrated list and its stats are read through the cache, keyed by the
    token, the collection's `updated_at` (which every item change and every
    note edit moves, see services._touch) and the reader's language. The
    collection row itself is read fresh each time: that is what carries
    `updated_at`, and it is how a revoked link stops working at once rather
    than when an entry expires.
    """
    collection = _live(token)
    if collection is None:
        return None

    def build():
        # Queried by id, not through `collection.items`: that would stamp every
        # item with the Collection (and its owner) and pickle them into the entry.
        rows = _hydrate(list(CollectionItem.objects.filter(collection_id=collection.pk)))
        rows = order_rows(collection.kind, rows)
        return rows, share_stats(collection.kind, rows)

    items, stats = cached(
        "share_collection", build, token, collection.updated_at.isoformat(),
        lang=get_language(),
    )
    return SharedCollection(
        collection=collection, owner=collection.user, items=items, stats=stats
    )


def own_collection(user, kind) -> SharedCollection | None:
    """The owner's own collection, hydrated and in display order, whether or
    not it is shared -- what the share sheet previews before a link exists.
    None for an unknown kind or a collection that was never created. Not
    cached: only its owner reads it, and only while the sheet is open."""
    if kind not in dict(Collection.KINDS):
        return None
    collection = Collection.objects.filter(user=user, kind=kind).first()
    if collection is None:
        return None
    rows = _hydrate(list(CollectionItem.objects.filter(collection_id=collection.pk)))
    return SharedCollection(
        collection=collection, owner=user, items=order_rows(kind, rows), stats={}
    )


def is_revoked(token) -> bool:
    """True for a token its owner stopped sharing (the page answers 410)."""
    if not token:
        return False
    return Collection.objects.filter(
        share_token=token, shared_at__isnull=True, user__is_active=True
    ).exists()


def share_stats(kind, rows) -> dict:
    """Real measurements of a hydrated collection, as plain data.

    `rows` are `_hydrate()`d items (movies carry `release_date`, `runtime` and
    an `imdb_rating` annotation; shows `first_air_date`, `number_of_episodes`
    and `imdb_rating`), so almost everything is arithmetic over what /me
    already read. The one extra warehouse read is the genre mix, and only for
    Liked, the one collection whose page leads with it: one query per content
    type.

    Keys: movies, shows, total; avg_imdb (None when nothing is rated) and
    rated; year_min / year_max (None when no dates); runtime_minutes, split
    as runtime_hours + runtime_rest (movies only: a show's length is its
    episodes, counted separately in `episodes`);
    genre ({"name", "pct"} or None); decades (oldest first, each with a count
    and `pct` of the busiest decade, ready to draw as a bar).
    """
    movies = [r.content for r in rows if r.content_type == CollectionItem.MOVIE]
    shows = [r.content for r in rows if r.content_type == CollectionItem.SERIES]

    ratings = [float(t.imdb_rating) for t in movies + shows if t.imdb_rating is not None]
    years = [t.release_date.year for t in movies if t.release_date] + [
        t.first_air_date.year for t in shows if t.first_air_date
    ]
    decades: dict[int, int] = {}
    for year in years:
        decades[year // 10 * 10] = decades.get(year // 10 * 10, 0) + 1
    busiest = max(decades.values(), default=1)
    runtime = sum(m.runtime or 0 for m in movies)

    stats = {
        "movies": len(movies),
        "shows": len(shows),
        "total": len(rows),
        "avg_imdb": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "rated": len(ratings),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "runtime_minutes": runtime,
        "runtime_hours": runtime // 60,
        "runtime_rest": runtime % 60,
        "episodes": sum(s.number_of_episodes or 0 for s in shows),
        "genre": _top_genre(movies, shows) if kind == Collection.LIKED else None,
        "decades": [
            {"decade": d, "count": n, "pct": round(100 * n / busiest)}
            for d, n in sorted(decades.items())
        ],
    }
    return stats


def _top_genre(movies, shows):
    """The genre most of the list shares: {"name", "pct"} where pct is the
    share of the whole list (movies and shows together) carrying it."""
    from movies.i18n import genre_labels
    from movies.models import MovieMetrics, SeriesGenre

    total = len(movies) + len(shows)
    if not total:
        return None
    counts: dict[str, int] = {}
    if movies:
        pairs = (
            MovieMetrics.objects.using("warehouse")
            .filter(movie_id__in=[m.movie_id for m in movies])
            .values_list("movie_id", "genre__genre_name")
            .distinct()
        )
        for _, name in pairs:
            counts[name] = counts.get(name, 0) + 1
    if shows:
        pairs = (
            SeriesGenre.objects.using("warehouse")
            .filter(series_id__in=[s.series_id for s in shows])
            .values_list("series_id", "genre__genre_name")
            .distinct()
        )
        for _, name in pairs:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    name, count = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"name": genre_labels().get(name, name), "pct": round(100 * count / total)}
