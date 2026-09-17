from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_POST

from core.models import Collection, CollectionItem
from core.services import (
    collection_rows,
    move_collection_item,
    remove_collection_item,
    toggle_collection_item,
)

# /me/ pages each collection independently, so a reader paging Liked never
# resets Watch later or Top back to page 1.
ACCOUNT_ITEMS_PER_PAGE = 10


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
    all_kinds = [kind for kind, _ in Collection.KINDS]
    rows_by_kind = {kind: collection_rows(request.user, kind) for kind in all_kinds}

    sections = []
    counts = {}
    for kind, label in Collection.KINDS:
        collection, rows = rows_by_kind[kind]
        page_obj = Paginator(rows, ACCOUNT_ITEMS_PER_PAGE).get_page(request.GET.get(kind))
        counts[kind] = page_obj.paginator.count
        # Paging Liked keeps whatever page Watch later/Top happen to be on —
        # each section's pager only needs to know about the OTHER two.
        other_params = {
            other: request.GET[other]
            for other in all_kinds
            if other != kind and request.GET.get(other)
        }
        sections.append({
            "kind": kind,
            "label": label,
            "collection": collection,
            "page_obj": page_obj,
            "base_query": urlencode(other_params),
        })

    return render(
        request,
        "core/account.html",
        {"sections": sections, "counts": counts},
    )


@login_required
@require_POST
def toggle_collection(request, kind, content_type, content_id):
    try:
        selected = toggle_collection_item(request.user, kind, content_type, content_id)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    # The icon-fill bloom (theoria.css/theoria.js) only has something to
    # animate if the pill doesn't reload out from under it -- so a caller
    # that says it can read JSON gets the new state back instead of a
    # redirect, and theoria.js falls back to a real form submit for
    # anything else (no-JS, a network error, the anonymous-user login
    # redirect this same request would otherwise 302 into).
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"selected": selected})
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
