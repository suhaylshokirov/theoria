"""Application services for personal lists (Liked / Watch later / Top).

Identity (sign-up, sign-in, email codes) lives in the `accounts` app — see
`accounts/codes.py` and `accounts/views.py`. This module only manages a
signed-in user's `Collection`/`CollectionItem` rows.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from core.models import Collection, CollectionItem, TitleFeedback

_UNSET = object()


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


def _touch(collection):
    """Move `Collection.updated_at` after an item-level change.

    `auto_now` only fires when the Collection row itself is saved, and adding,
    removing or reordering an item saves a CollectionItem -- so without this
    the timestamp a shared page's cache key trusts would never move.
    """
    Collection.objects.filter(pk=collection.pk).update(updated_at=timezone.now())


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
            _touch(collection)
            return False
        position = collection.items.aggregate(max_position=Max("position"))["max_position"]
        CollectionItem.objects.create(
            collection=collection,
            content_type=content_type,
            content_id=content_id,
            position=(position + 1 if position is not None else 0),
        )
        _touch(collection)
        return True


def remove_collection_item(user, kind, item_id):
    collection = _collection(user, kind)
    removed = collection.items.filter(pk=item_id).delete()[0] > 0
    if removed:
        _touch(collection)
    return removed


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
        _touch(collection)


def place_collection_item(user, kind, item_id, *, before=None, after=None):
    """Drop one Top item next to another: just before `before`, or just after
    `after` (item ids, exactly one of the two).

    The /me/ shelf drags a card onto a neighbour, so the move is named by
    that neighbour rather than by a rank number: a displayed rank skips any
    title that has left the warehouse, and an id is the same on either side.
    The anchor can sit on another page -- dropping on the pager places the
    card beside the nearest title of the page before or after.

    Positions are rewritten 0..n-1 in the new order; only rows whose
    position actually changed are saved.
    """
    if kind != Collection.TOP or (before is None) == (after is None):
        raise ValueError("Only Top items can be reordered.")
    anchor_id = before if before is not None else after
    collection = _collection(user, kind)
    with transaction.atomic():
        items = list(collection.items.select_for_update())
        by_id = {item.pk: item for item in items}
        moving, anchor = by_id.get(item_id), by_id.get(anchor_id)
        if moving is None or anchor is None:
            raise CollectionItem.DoesNotExist("That collection item could not be found.")
        if moving is anchor:
            return
        items.remove(moving)
        items.insert(items.index(anchor) + (0 if before is not None else 1), moving)
        changed = []
        for position, item in enumerate(items):
            if item.position != position:
                item.position = position
                changed.append(item)
        if changed:
            CollectionItem.objects.bulk_update(changed, ["position"])
            _touch(collection)


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
    for kind, kind_rows in by_kind.items():
        by_kind[kind] = order_rows(kind, kind_rows)
    return by_kind


def order_rows(kind, rows):
    """Display order for one collection: Top keeps rank order (position, as
    the items arrived); Liked and Watch later are newest first."""
    if kind in (Collection.LIKED, Collection.WATCH_LATER):
        return sorted(rows, key=lambda r: (r.added_at, r.id), reverse=True)
    return rows


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
    from movies.i18n import localize_movies, localize_series
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
        localize_series(Series.objects.using("warehouse"))
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
            item.title = item.content.display_title or ""
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


def record_title_feedback(
    user,
    content_type,
    content_id,
    *,
    watched=None,
    disliked=None,
    not_interested=None,
    personal_rating=_UNSET,
):
    """Save an explicit watch/preference signal for a signed-in user."""
    if content_type not in {TitleFeedback.MOVIE, TitleFeedback.SERIES}:
        raise ValueError("Unknown content type.")
    if not _content_exists(content_type, content_id):
        raise ValueError("That title is not in the catalogue.")
    if personal_rating is not _UNSET and personal_rating is not None:
        if not isinstance(personal_rating, int) or not 1 <= personal_rating <= 5:
            raise ValueError("Personal rating must be a whole number from 1 to 5.")

    feedback, _ = TitleFeedback.objects.get_or_create(
        user=user,
        content_type=content_type,
        content_id=content_id,
    )
    changed = []
    for field, value in {
        "watched": watched,
        "disliked": disliked,
        "not_interested": not_interested,
    }.items():
        if value is not None:
            setattr(feedback, field, bool(value))
            changed.append(field)
    if personal_rating is not _UNSET:
        feedback.personal_rating = personal_rating
        changed.append("personal_rating")
    if changed:
        feedback.save(update_fields=[*changed, "updated_at"])
    return feedback


def _catalogue_title_lookup(items):
    """Return the catalogue facts needed to describe saved list items.

    Personal lists live in the main Django database, while title details live
    in the read-only warehouse. Keeping this lookup in one helper makes that
    boundary explicit and lets the recommendation code stay database-agnostic.
    """
    movie_ids = {item.content_id for item in items if item.content_type == CollectionItem.MOVIE}
    series_ids = {item.content_id for item in items if item.content_type == CollectionItem.SERIES}
    from movies.models import Movie, Series

    titles = {}
    for movie in Movie.objects.using("warehouse").filter(movie_id__in=movie_ids):
        titles[(CollectionItem.MOVIE, movie.movie_id)] = {
            "content_id": movie.movie_id,
            "content_type": CollectionItem.MOVIE,
            "title": movie.title,
            "runtime": movie.runtime,
            "year": movie.release_date.year if movie.release_date else None,
        }
    for series in Series.objects.using("warehouse").filter(series_id__in=series_ids):
        titles[(CollectionItem.SERIES, series.series_id)] = {
            "content_id": series.series_id,
            "content_type": CollectionItem.SERIES,
            "title": series.name,
            "runtime": None,
            "year": series.first_air_date.year if series.first_air_date else None,
        }
    return titles


def build_taste_summary(user):
    """Read a signed-in user's saved lists into a recommendation-ready summary.

    This is deliberately the first small AI building block: it only reports
    facts Theoria already owns. It does not call an AI provider, infer hidden
    preferences, or create any user data. Future steps can add watched and
    disliked feedback to this same summary.
    """
    summary = {
        **{kind: [] for kind, _ in Collection.KINDS},
        "watched": [],
        "disliked": [],
        "not_interested": [],
    }
    if not user.is_authenticated:
        return summary

    items = list(
        CollectionItem.objects.filter(collection__user=user)
        .select_related("collection")
        .order_by("collection__kind", "position", "-added_at")
    )
    feedback_rows = list(TitleFeedback.objects.filter(user=user).order_by("-updated_at"))
    titles = _catalogue_title_lookup([*items, *feedback_rows])
    for item in items:
        title = titles.get((item.content_type, item.content_id))
        if title is None:
            continue
        summary[item.collection.kind].append({**title, "position": item.position})
    for feedback in feedback_rows:
        title = titles.get((feedback.content_type, feedback.content_id))
        if title is None:
            continue
        feedback_title = {**title, "personal_rating": feedback.personal_rating}
        if feedback.watched:
            summary["watched"].append(feedback_title)
        if feedback.disliked:
            summary["disliked"].append(feedback_title)
        if feedback.not_interested:
            summary["not_interested"].append(feedback_title)
    return summary
