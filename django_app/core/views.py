from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_POST

from core.models import Collection, CollectionItem
from core.services import (
    ACCOUNT_SORTS,
    DEFAULT_ACCOUNT_SORT,
    account_rows,
    move_collection_item,
    remove_collection_item,
    sort_rows,
    toggle_collection_item,
)

# /me/ pages each collection independently, so a reader paging Liked never
# resets Watch later or Top back to page 1. Five is one row of the widest
# grid; the same at every width, so a URL means the same page on any screen.
ACCOUNT_ITEMS_PER_PAGE = 5

# (stored kind, URL slug, label, sortable). The slug is what the page's
# anchors and query params use (`#later`, `?later_page=2`) — shorter than the
# stored "watch_later", which stays untouched in the data. Top is ordered by
# rank, so it pages but never sorts.
ACCOUNT_SECTIONS = (
    (Collection.LIKED, "liked", "Liked", True),
    (Collection.WATCH_LATER, "later", "Watch later", True),
    (Collection.TOP, "top", "Top", False),
)

# Every param the page reads, in the order it writes them back into a URL.
_ACCOUNT_PARAMS = ("liked_sort", "liked_page", "later_sort", "later_page", "top_page")

# Pagers show every page number up to this many pages; past it they collapse
# to first, last, and the current page's neighbours.
_PAGER_FULL_UP_TO = 7


def _is_default(name, value):
    return value == (DEFAULT_ACCOUNT_SORT if name.endswith("_sort") else 1)


def _account_url(path, state, slug, **changes):
    """/me/ with `state` plus `changes`, defaults left out, landing on #slug."""
    merged = {**state, **changes}
    params = [
        (name, merged[name])
        for name in _ACCOUNT_PARAMS
        if name in merged and not _is_default(name, merged[name])
    ]
    query = f"?{urlencode(params)}" if params else ""
    return f"{path}{query}#{slug}"


def _page_window(current, total):
    """Page numbers for a pager, with None where a run is elided:
    _page_window(5, 12) -> [1, None, 4, 5, 6, None, 12]."""
    if total <= _PAGER_FULL_UP_TO:
        return list(range(1, total + 1))
    shown = sorted({1, total, current - 1, current, current + 1} & set(range(1, total + 1)))
    window = []
    for number in shown:
        if window and number - window[-1] > 1:
            window.append(None)
        window.append(number)
    return window


def _account_sections(request):
    rows_by_kind = account_rows(request.user)

    # First pass: resolve every section's sort and (clamped) page, so each
    # URL built in the second pass carries the other sections' real state.
    resolved = []
    state = {}
    for kind, slug, label, sortable in ACCOUNT_SECTIONS:
        rows = rows_by_kind[kind]
        sort = None
        if sortable:
            sort = request.GET.get(f"{slug}_sort")
            if sort not in dict(ACCOUNT_SORTS):
                sort = DEFAULT_ACCOUNT_SORT
            state[f"{slug}_sort"] = sort
            rows = sort_rows(rows, sort)
        # get_page clamps: junk -> 1, past the end -> the last page. That is
        # also what sends a reader back a page when a remove empties theirs.
        page_obj = Paginator(rows, ACCOUNT_ITEMS_PER_PAGE).get_page(
            request.GET.get(f"{slug}_page")
        )
        state[f"{slug}_page"] = page_obj.number
        resolved.append((kind, slug, label, sortable, sort, page_obj))

    path = request.path
    sections = []
    for kind, slug, label, sortable, sort, page_obj in resolved:
        count = page_obj.paginator.count
        on_page = len(page_obj.object_list)

        def page_url(number, slug=slug):
            return _account_url(path, state, slug, **{f"{slug}_page": number})

        # The dashed "room for more" panel fills out a short last page; Top
        # shows its open ranks instead, and with nothing ranked at all, the
        # first five.
        is_last = not page_obj.has_next()
        fill = on_page if sortable and count and is_last and on_page < ACCOUNT_ITEMS_PER_PAGE else 0
        rank_slots = []
        if not sortable and is_last:
            first_rank = page_obj.start_index() if count else 1  # an empty page's start_index() is 0
            rank_slots = list(range(first_rank + on_page, first_rank + ACCOUNT_ITEMS_PER_PAGE))

        sections.append({
            "kind": kind,
            "slug": slug,
            "label": label,
            "sortable": sortable,
            "sort": sort,
            "sort_options": ACCOUNT_SORTS,
            "page_obj": page_obj,
            "count": count,
            "fill": fill,
            "rank_slots": rank_slots,
            "self_url": page_url(page_obj.number),
            "prev_url": page_url(page_obj.previous_page_number()) if page_obj.has_previous() else None,
            "next_url": page_url(page_obj.next_page_number()) if page_obj.has_next() else None,
            "page_links": [
                {"number": n, "url": page_url(n), "current": n == page_obj.number} if n else {"number": None}
                for n in _page_window(page_obj.number, page_obj.paginator.num_pages)
            ],
            # The sort form's GET replaces the whole query string, so it
            # carries every other section's state as hidden fields. Its own
            # page is left out: a new sort always starts at page 1.
            "form_hidden": [
                (name, state[name])
                for name in _ACCOUNT_PARAMS
                if not name.startswith(f"{slug}_") and not _is_default(name, state[name])
            ],
        })
    return sections


def _redirect_back(request, fallback):
    """Redirect to POST['next'] if it's safe, else to `fallback`.

    /me/'s remove and reorder forms send the section's own URL (sort, page
    and #anchor included) as `next`, so a no-JS reader lands back where they
    were; the fallback only covers a POST that arrives without one.
    """
    candidate = request.POST.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(candidate)
    return redirect(fallback)


@login_required
def account(request):
    sections = _account_sections(request)

    # theoria.js re-fetches one section after a sort, page or remove and asks
    # for just that section's markup with ?_section=<slug>, the same
    # X-Requested-With handshake the catalogue's live filters use.
    wanted = request.GET.get("_section")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" and wanted:
        for section in sections:
            if section["slug"] == wanted:
                return render(request, "core/_collection_section.html", {"section": section})
        return HttpResponseBadRequest("Unknown collection.")

    return render(request, "core/account.html", {"sections": sections})


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
        removed = remove_collection_item(request.user, kind, item_id)
    except ValueError:
        return HttpResponseBadRequest("That collection could not be found.")
    # theoria.js removes the card at once and re-fetches the section itself,
    # so it only needs to hear that the delete happened — not a redirect to a
    # whole page it would throw away.
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"removed": removed})
    return _redirect_back(request, "profile")


@login_required
@require_POST
def move_item(request, kind, item_id, direction):
    try:
        move_collection_item(request.user, kind, item_id, direction)
    except (ValueError, CollectionItem.DoesNotExist):
        return HttpResponseBadRequest("That collection item could not be moved.")
    return _redirect_back(request, "profile")
