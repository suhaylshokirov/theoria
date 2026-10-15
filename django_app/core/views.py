from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.translation import gettext, gettext_lazy as _
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts import ratelimit
from core import sharing
from core.models import Collection, CollectionItem
from core.services import (
    account_rows,
    move_collection_item,
    remove_collection_item,
    toggle_collection_item,
)

# /me/ pages each collection independently, so a reader paging Liked never
# resets Watch later or Top back to page 1. Five is one row of the widest
# grid; the same at every width, so a URL means the same page on any screen.
ACCOUNT_ITEMS_PER_PAGE = 5

# (stored kind, URL slug, label, ranked). The slug is what the page's anchors
# and query params use (`#later`, `?later_page=2`) — shorter than the stored
# "watch_later", which stays untouched in the data. Top is the ranked one:
# it shows each title's rank and its open rank slots.
ACCOUNT_SECTIONS = (
    (Collection.LIKED, "liked", _("Liked"), False),
    (Collection.WATCH_LATER, "later", _("Watch later"), False),
    (Collection.TOP, "top", _("Top"), True),
)

# Every param the page reads, in the order it writes them back into a URL.
_ACCOUNT_PARAMS = ("liked_page", "later_page", "top_page")

# Pagers show every page number up to this many pages; past it they collapse
# to first, last, and the current page's neighbours.
_PAGER_FULL_UP_TO = 7


def _account_url(path, state, slug, **changes):
    """/me/ with `state` plus `changes`, defaults left out, landing on #slug."""
    merged = {**state, **changes}
    params = [
        (name, merged[name])
        for name in _ACCOUNT_PARAMS
        if name in merged and merged[name] != 1
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

    # First pass: resolve every section's (clamped) page, so each URL built
    # in the second pass carries the other sections' real state.
    resolved = []
    state = {}
    for kind, slug, label, ranked in ACCOUNT_SECTIONS:
        # get_page clamps: junk -> 1, past the end -> the last page. That is
        # also what sends a reader back a page when a remove empties theirs.
        page_obj = Paginator(rows_by_kind[kind], ACCOUNT_ITEMS_PER_PAGE).get_page(
            request.GET.get(f"{slug}_page")
        )
        state[f"{slug}_page"] = page_obj.number
        resolved.append((kind, slug, label, ranked, page_obj))

    path = request.path
    sections = []
    for kind, slug, label, ranked, page_obj in resolved:
        count = page_obj.paginator.count
        on_page = len(page_obj.object_list)

        def page_url(number, slug=slug):
            return _account_url(path, state, slug, **{f"{slug}_page": number})

        # The dashed "room for more" panel fills out a short last page; Top
        # shows its open ranks instead, and with nothing ranked at all, the
        # first five.
        is_last = not page_obj.has_next()
        fill = on_page if not ranked and count and is_last and on_page < ACCOUNT_ITEMS_PER_PAGE else 0
        rank_slots = []
        if ranked and is_last:
            first_rank = page_obj.start_index() if count else 1  # an empty page's start_index() is 0
            rank_slots = list(range(first_rank + on_page, first_rank + ACCOUNT_ITEMS_PER_PAGE))

        sections.append({
            "kind": kind,
            "slug": slug,
            "label": label,
            "ranked": ranked,
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
        })
    return sections


def _redirect_back(request, fallback):
    """Redirect to POST['next'] if it's safe, else to `fallback`.

    /me/'s remove and reorder forms send the section's own URL (page and
    #anchor included) as `next`, so a no-JS reader lands back where they
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

    # theoria.js re-fetches one section after a page change or remove and asks
    # for just that section's markup with ?_section=<slug>, the same
    # X-Requested-With handshake the catalogue's live filters use.
    wanted = request.GET.get("_section")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" and wanted:
        for section in sections:
            if section["slug"] == wanted:
                return render(request, "core/_collection_section.html", {"section": section})
        return HttpResponseBadRequest(gettext("Unknown collection."))

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
        return HttpResponseBadRequest(gettext("That collection could not be found."))
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
        return HttpResponseBadRequest(gettext("That collection item could not be moved."))
    return _redirect_back(request, "profile")


SHARE_RATE_LIMIT = 30
SHARE_RATE_WINDOW_SECONDS = 10 * 60


def _share_payload(request, collection):
    """The link and note the share sheet shows; url is None while unshared."""
    live = collection.share_token and collection.shared_at
    return {
        "url": request.build_absolute_uri(sharing.share_path(collection.share_token)) if live else None,
        "note": collection.share_note,
    }


@login_required
@require_POST
def share_collection(request, kind):
    """Enable, rotate or disable the public link for one of the user's
    collections. A JSON caller gets {url, note} back; a plain form POST is
    redirected to where it came from (/me/ by default)."""
    wants_json = request.headers.get("Accept") == "application/json"
    if ratelimit.is_rate_limited(
        request, scope="share", limit=SHARE_RATE_LIMIT, window_seconds=SHARE_RATE_WINDOW_SECONDS
    ):
        message = gettext("Too many share changes — please try again in a few minutes.")
        if wants_json:
            return JsonResponse({"error": message}, status=429)
        return HttpResponse(message, status=429, content_type="text/plain; charset=utf-8")

    action = request.POST.get("action")
    # An absent note field means "leave the note", an empty one clears it.
    note = request.POST.get("note")
    try:
        if action == "enable":
            collection = sharing.enable_share(request.user, kind, note)
        elif action == "rotate":
            collection = sharing.rotate_share(request.user, kind, note)
        elif action == "disable":
            collection = sharing.disable_share(request.user, kind)
        else:
            raise sharing.ShareError(gettext("Unknown share action."))
    except sharing.ShareError as exc:
        if wants_json:
            return JsonResponse({"error": str(exc)}, status=400)
        return HttpResponseBadRequest(str(exc))
    if wants_json:
        return JsonResponse(_share_payload(request, collection))
    return _redirect_back(request, "profile")


# Four rows of the widest grid (the same 5 columns /me/ uses).
SHARE_ITEMS_PER_PAGE = 20


def _share_kind_copy():
    """Stored kind -> (the /me/ anchor to manage it, heading, the fixed line
    under it), in the reader's language. The owner's note goes after it."""
    return {
        Collection.TOP: (
            "top", gettext("Top"), gettext("Picked by hand and ranked in order."),
        ),
        Collection.LIKED: (
            "liked", gettext("Liked"), gettext("Everything they liked, newest first."),
        ),
        Collection.WATCH_LATER: (
            "later", gettext("Watch later"), gettext("Saved for a free evening."),
        ),
    }


def _share_page_links(request, page_obj):
    def url(number):
        return request.path if number == 1 else f"{request.path}?page={number}"

    return {
        "prev_url": url(page_obj.previous_page_number()) if page_obj.has_previous() else None,
        "next_url": url(page_obj.next_page_number()) if page_obj.has_next() else None,
        "page_links": [
            {"number": n, "url": url(n), "current": n == page_obj.number} if n else {"number": None}
            for n in _page_window(page_obj.number, page_obj.paginator.num_pages)
        ],
    }


def shared_page(request, token):
    """The public, read-only page behind a share link: 404 for a token that
    never existed (or whose owner is gone), 410 for one its owner switched off.

    Language is the *viewer's* cookie, as everywhere. Nothing on this page
    reads the owner's email; the owner appears as a handle and a join date.
    """
    shared = sharing.shared_collection(token)
    if shared is None:
        if sharing.is_revoked(token):
            response = render(request, "core/share/gone.html", status=410)
            response["X-Robots-Tag"] = "noindex, nofollow"
            response["Referrer-Policy"] = "same-origin"
            return response
        raise Http404("No such shared collection.")

    collection = shared.collection
    slug, label, fixed_line = _share_kind_copy()[collection.kind]
    page_obj = Paginator(shared.items, SHARE_ITEMS_PER_PAGE).get_page(request.GET.get("page"))
    stats = shared.stats

    is_owner = request.user.is_authenticated and request.user.pk == collection.user_id
    owner = shared.owner
    if stats["movies"] and stats["shows"]:
        summary = "mixed"
    else:
        summary = "shows" if stats["shows"] else "movies"

    page_url = request.build_absolute_uri(request.path)
    if collection.share_note:
        og_description = collection.share_note
    else:
        og_description = fixed_line
    context = {
        "kind": collection.kind,
        "slug": slug,
        "label": label,
        "fixed_line": fixed_line,
        "note": collection.share_note,
        "owner_name": owner.username,
        "owner_since": owner.date_joined,
        "stats": stats,
        "summary": summary,
        "ranked": collection.kind == Collection.TOP,
        "page_obj": page_obj,
        "count": page_obj.paginator.count,
        "is_owner": is_owner,
        "manage_url": f"{reverse('profile')}#{slug}",
        "og_title": f"{label} · @{owner.username}",
        "og_description": og_description,
        "og_url": page_url,
        "og_image": request.build_absolute_uri(static("img/og-default.png")),
        **_share_page_links(request, page_obj),
    }
    response = render(request, "core/share/page.html", context)
    # The link is unlisted: keep it out of search indexes and out of the
    # Referer header any outbound link would send the token in.
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "same-origin"
    return response


def set_language_cookie(response, code):
    """Write the language cookie exactly as Django's set_language does."""
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        code,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )


def legacy_language_redirect(request, lang, rest):
    """Old /ru/... and /uz/... links: set the language, go to the bare URL.

    302, not 301: a cached 301 would skip the server next time and never set
    the cookie.
    """
    target = "/" + rest
    query = request.META.get("QUERY_STRING")
    if query:
        target += "?" + query
    response = redirect(target)
    set_language_cookie(response, lang)
    return response
