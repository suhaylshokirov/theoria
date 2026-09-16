"""Application services for personal lists (Liked / Watch later / Top).

Identity (sign-up, sign-in, email codes) lives in the `accounts` app — see
`accounts/codes.py` and `accounts/views.py`. This module only manages a
signed-in user's `Collection`/`CollectionItem` rows.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max

from core.models import Collection, CollectionItem


_DEFAULT_NAMES = {
    Collection.LIKED: "Liked",
    Collection.WATCH_LATER: "Watch later",
    Collection.TOP: "Top",
}


def _collection(user, kind):
    # Only the one collection being touched is fetched (or lazily created).
    # This used to get_or_create all three on every call -- 3 extra round-trips
    # to the auth database on each Like click, for rows the call never read.
    if kind not in _DEFAULT_NAMES:
        raise ValueError("Unknown collection.")
    collection, _ = Collection.objects.get_or_create(
        user=user, kind=kind, defaults={"name": _DEFAULT_NAMES[kind]}
    )
    return collection


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
    selected = set(
        CollectionItem.objects.filter(
            collection__user=user,
            content_type=content_type,
            content_id=content_id,
        ).values_list("collection__kind", flat=True)
    )
    return {kind: kind in selected for kind, _ in Collection.KINDS}
