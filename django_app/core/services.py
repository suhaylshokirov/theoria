"""Application services for account profiles and personal lists."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, Max
from django.utils.text import slugify

from core.models import Collection, CollectionItem, User

def normalize_email(value: str) -> str:
    return value.strip().casefold()


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


def validate_name(value: str, label: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError(f"Enter your {label.lower()}.")
    if len(name) > 150:
        raise ValueError(f"Your {label.lower()} must be 150 characters or fewer.")
    return name


def generate_username(first_name: str, last_name: str, email: str) -> str:
    base = slugify(f"{first_name}-{last_name}") or slugify(email.split("@", 1)[0]) or "member"
    base = base[:30].strip("-") or "member"
    candidate = base
    suffix = 2
    while User.objects.filter(username__iexact=candidate).exists():
        suffix_text = f"-{suffix}"
        candidate = f"{base[:30 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate


def ensure_default_collections(user):
    names = {
        Collection.LIKED: "Liked",
        Collection.DISLIKED: "Disliked",
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

        if kind in {Collection.LIKED, Collection.DISLIKED}:
            opposite = (
                Collection.DISLIKED if kind == Collection.LIKED else Collection.LIKED
            )
            CollectionItem.objects.filter(
                collection=_collection(user, opposite),
                content_type=content_type,
                content_id=content_id,
            ).delete()

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


def _genre_counts(items):
    """Count unique saved titles by genre across the read-only warehouse."""
    movie_ids = {item.content_id for item in items if item.content_type == CollectionItem.MOVIE}
    series_ids = {item.content_id for item in items if item.content_type == CollectionItem.SERIES}
    if not movie_ids and not series_ids:
        return []

    from movies.models import MovieMetrics, SeriesGenre

    counts = {}
    if movie_ids:
        for row in (
            MovieMetrics.objects.using("warehouse")
            .filter(movie_id__in=movie_ids)
            .values("genre__genre_name")
            .annotate(title_count=Count("movie_id", distinct=True))
        ):
            counts[row["genre__genre_name"]] = row["title_count"]
    if series_ids:
        for row in (
            SeriesGenre.objects.using("warehouse")
            .filter(series_id__in=series_ids)
            .values("genre__genre_name")
            .annotate(title_count=Count("series_id", distinct=True))
        ):
            name = row["genre__genre_name"]
            counts[name] = counts.get(name, 0) + row["title_count"]
    return [
        {"name": name, "title_count": title_count}
        for name, title_count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def taste_profile(user):
    """Return explicit saved-title signals for the account and future recommender."""
    ensure_default_collections(user)
    items_by_kind = {
        kind: list(
            CollectionItem.objects.filter(collection__user=user, collection__kind=kind)
        )
        for kind, _ in Collection.KINDS
    }
    positive_items = {
        (item.content_type, item.content_id): item
        for kind in (Collection.LIKED, Collection.TOP)
        for item in items_by_kind[kind]
    }.values()
    return {
        "counts": {kind: len(items) for kind, items in items_by_kind.items()},
        "preferred_genres": _genre_counts(positive_items),
        "avoided_genres": _genre_counts(items_by_kind[Collection.DISLIKED]),
        "watch_later_genres": _genre_counts(items_by_kind[Collection.WATCH_LATER]),
    }
