"""Application services for personal lists (Liked / Watch later / Top).

Identity (sign-up, sign-in, email codes) lives in the `accounts` app — see
`accounts/codes.py` and `accounts/views.py`. This module only manages a
signed-in user's `Collection`/`CollectionItem` rows.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max, Q

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


def account_rows(user):
    """Every item in all three of `user`'s collections, hydrated and grouped
    by kind: {kind: [CollectionItem, ...]}. Top is in rank order (position,
    CollectionItem.Meta.ordering); Liked and Watch later are newest first.

    Three queries whatever the collection sizes: one on the application
    database for the items themselves, one per content type on the warehouse
    for the titles they point at. A kind with no items maps to [].

    Items whose title has left the warehouse are dropped, so len() of each
    list is the count the page shows.
    """
    items = list(
        CollectionItem.objects.filter(collection__user=user).select_related("collection")
    )
    rows = _hydrate(items)
    by_kind = {kind: [] for kind, _ in Collection.KINDS}
    for item in rows:
        by_kind[item.collection.kind].append(item)
    for kind in (Collection.LIKED, Collection.WATCH_LATER):
        by_kind[kind].sort(key=lambda r: (r.added_at, r.id), reverse=True)
    return by_kind


def _hydrate(items):
    """Attach each item's warehouse title as `.content`, plus its name as a
    flat `.title` (a movie's `title`, a show's `name`) for the page's labels.
    Items whose title is no longer in the warehouse are dropped."""
    movie_ids = [item.content_id for item in items if item.content_type == CollectionItem.MOVIE]
    series_ids = [item.content_id for item in items if item.content_type == CollectionItem.SERIES]
    # Imported here, not at module level: movies.views imports core.services
    # (for collection_flags), so an eager import back the other way would be
    # circular. By the time this function runs, movies.views has already
    # finished importing, so this just fetches it from sys.modules.
    from movies.i18n import localize_movies
    from movies.models import Movie, Series
    from movies.views import _series_year_span

    # Same filtered annotation movies/views.py puts on every poster-card
    # queryset (Task 68) — a card rendered from this list needs its rating
    # badge to match what the same title shows everywhere else on the site.
    movies = {
        obj.movie_id: obj
        for obj in localize_movies(Movie.objects.using("warehouse"))
        .filter(movie_id__in=movie_ids)
        .annotate(imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb")))
    }
    series = {}
    for obj in (
        Series.objects.using("warehouse")
        .filter(series_id__in=series_ids)
        .annotate(imdb_rating=Max("seriesrating__rating", filter=Q(seriesrating__source="imdb")))
    ):
        obj.year_span = _series_year_span(obj)
        series[obj.series_id] = obj
    rows = []
    for item in items:
        if item.content_type == CollectionItem.MOVIE:
            item.content = movies.get(item.content_id)
            if item.content is None:
                continue
            item.title = item.content.display_title or ""
        else:
            item.content = series.get(item.content_id)
            if item.content is None:
                continue
            item.title = item.content.name or ""
        rows.append(item)
    return rows


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
