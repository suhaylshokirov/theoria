from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.models import Collection, CollectionItem
from core.services import (
    collection_rows,
    move_collection_item,
    remove_collection_item,
    toggle_collection_item,
)


def _redirect_back(request, fallback):
    """Redirect to POST['next'] if it's safe, else to `fallback`.

    account.html's forms send no `next` (unlike movie/series detail's
    collection-action forms), so the fallback is what a reader actually
    lands on after removing or reordering an item there.
    """
    candidate = request.POST.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(candidate)
    return redirect(fallback)


@login_required
def account(request):
    cards = []
    for kind, label in Collection.KINDS:
        collection, items = collection_rows(request.user, kind)
        cards.append({"collection": collection, "label": label, "items": items})
    return render(request, "core/account.html", {"cards": cards})


@login_required
@require_POST
def toggle_collection(request, kind, content_type, content_id):
    try:
        toggle_collection_item(request.user, kind, content_type, content_id)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    return _redirect_back(request, "/")


@login_required
@require_POST
def remove_item(request, kind, item_id):
    try:
        remove_collection_item(request.user, kind, item_id)
    except ValueError:
        return HttpResponseBadRequest("That collection could not be found.")
    return _redirect_back(request, "profile")


@login_required
@require_POST
def move_item(request, kind, item_id, direction):
    try:
        move_collection_item(request.user, kind, item_id, direction)
    except (ValueError, CollectionItem.DoesNotExist):
        return HttpResponseBadRequest("That collection item could not be moved.")
    return _redirect_back(request, "profile")
