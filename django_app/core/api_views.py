"""Private account-data endpoints for the account UI and future recommender."""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods

from core.models import Collection, CollectionItem
from core.services import collection_flags, collection_rows, taste_profile, toggle_collection_item


API_KINDS = {Collection.LIKED, Collection.DISLIKED, Collection.WATCH_LATER}


def _valid_content_type(content_type):
    return content_type in {CollectionItem.MOVIE, CollectionItem.SERIES}


def _read_changes(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Send a JSON object with preference values.")
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Send at least one preference value.")

    changes = {}
    for kind, selected in payload.items():
        if kind not in API_KINDS:
            raise ValueError("Unknown preference.")
        if not isinstance(selected, bool):
            raise ValueError("Preference values must be true or false.")
        changes[kind] = selected
    if changes.get(Collection.LIKED) and changes.get(Collection.DISLIKED):
        raise ValueError("A title cannot be both liked and disliked.")
    return changes


def _item_payload(item):
    content = item.content
    return {
        "item_id": item.pk,
        "content_type": item.content_type,
        "content_id": item.content_id,
        "title": content.title if item.content_type == CollectionItem.MOVIE else content.name,
        "slug": content.slug,
        "position": item.position,
        "added_at": item.added_at.isoformat(),
    }


@login_required
@require_GET
def preference_lists(request):
    """Return only the signed-in reader's saved titles and taste signals."""
    collections = {}
    for kind, label in Collection.KINDS:
        collection, items = collection_rows(request.user, kind)
        collections[kind] = {"label": label, "items": [_item_payload(item) for item in items]}
    return JsonResponse({"collections": collections, "taste_profile": taste_profile(request.user)})


@login_required
@require_http_methods(["GET", "PATCH"])
def preference_detail(request, content_type, content_id):
    """Read or update one title's explicit like, dislike, and watch-later signals."""
    if not _valid_content_type(content_type):
        return HttpResponseBadRequest("Unknown content type.")
    if request.method == "GET":
        return JsonResponse(
            {
                "content_type": content_type,
                "content_id": content_id,
                "preferences": collection_flags(request.user, content_type, content_id),
            }
        )

    try:
        changes = _read_changes(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    flags = collection_flags(request.user, content_type, content_id)
    try:
        for kind, selected in changes.items():
            if flags[kind] != selected:
                toggle_collection_item(request.user, kind, content_type, content_id)
                flags[kind] = selected
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    return JsonResponse(
        {
            "content_type": content_type,
            "content_id": content_id,
            "preferences": collection_flags(request.user, content_type, content_id),
        }
    )
