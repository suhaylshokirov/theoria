from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.models import Collection, CollectionItem
from core.services import (
    collection_rows,
    ensure_default_collections,
    move_collection_item,
    remove_collection_item,
    safe_next,
    toggle_collection_item,
)


@login_required
def account(request):
    ensure_default_collections(request.user)
    cards = []
    for kind, label in Collection.KINDS:
        collection, items = collection_rows(request.user, kind)
        cards.append({"collection": collection, "label": label, "items": items})
    return render(request, "core/account.html", {"cards": cards})


@login_required
@require_POST
def toggle_collection(request, kind, content_type, content_id):
    try:
        selected = toggle_collection_item(
            request.user, kind, content_type, content_id
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    if "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"selected": selected, "kind": kind})
    return redirect(safe_next(request.POST.get("next")))


@login_required
@require_POST
def remove_item(request, kind, item_id):
    try:
        remove_collection_item(request.user, kind, item_id)
    except ValueError:
        return HttpResponseBadRequest("That collection could not be found.")
    return redirect(safe_next(request.POST.get("next")))


@login_required
@require_POST
def move_item(request, kind, item_id, direction):
    try:
        move_collection_item(request.user, kind, item_id, direction)
    except (ValueError, CollectionItem.DoesNotExist):
        return HttpResponseBadRequest("That collection item could not be moved.")
    return redirect(safe_next(request.POST.get("next")))
