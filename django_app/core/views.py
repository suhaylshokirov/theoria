from __future__ import annotations

from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.validators import validate_email
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from core.models import Collection, CollectionItem, EmailCode, User
from core.services import (
    CodeRequestError,
    CodeVerificationError,
    collection_rows,
    ensure_default_collections,
    issue_email_code,
    move_collection_item,
    normalize_email,
    remove_collection_item,
    safe_next,
    toggle_collection_item,
    validate_username,
    verify_email_code,
)

CHALLENGE_SESSION_KEY = "email_auth_challenge"
NEXT_SESSION_KEY = "email_auth_next"
MODE_SESSION_KEY = "email_auth_mode"
RESEND_NOTE_SESSION_KEY = "email_auth_resend_note"


def _next_url(request):
    return safe_next(
        request.GET.get("next")
        or request.POST.get("next")
        or request.session.get(NEXT_SESSION_KEY)
        or "/"
    )


def _request_ip(request):
    return request.META.get("REMOTE_ADDR")


def _begin_code_flow(request, *, purpose, email, username="", next_url="/"):
    challenge = issue_email_code(
        email=email,
        purpose=purpose,
        username=username,
        request_ip=_request_ip(request),
    )
    request.session[CHALLENGE_SESSION_KEY] = str(challenge.pk)
    request.session[NEXT_SESSION_KEY] = next_url
    request.session[MODE_SESSION_KEY] = purpose
    return redirect("core:verify_code")


@require_http_methods(["GET"])
def auth_entry(request):
    if request.user.is_authenticated:
        return redirect("account:index")
    return render(
        request,
        "core/auth.html",
        {"mode": "entry", "next_url": _next_url(request)},
    )


@require_http_methods(["GET", "POST"])
def signup(request):
    next_url = _next_url(request)
    context = {"mode": "signup", "next_url": next_url, "values": {}}
    if request.method == "GET":
        return render(request, "core/auth.html", context)

    username = request.POST.get("username", "").strip()
    email = normalize_email(request.POST.get("email", ""))
    context["values"] = {"username": username, "email": email}
    errors = []
    try:
        username = validate_username(username)
    except ValueError as exc:
        errors.append(str(exc))
    try:
        validate_email(email)
    except Exception:
        errors.append("Enter a valid email address.")
    if email and request.user.is_authenticated:
        errors.append("You are already signed in.")
    if email and User.objects.filter(email=email).exists():
        errors.append("That email already has an account. Sign in instead.")
    if errors:
        context["errors"] = errors
        return render(request, "core/auth.html", context, status=400)
    try:
        return _begin_code_flow(
            request,
            purpose=EmailCode.SIGNUP,
            email=email,
            username=username,
            next_url=next_url,
        )
    except CodeRequestError as exc:
        context["errors"] = [str(exc)]
        return render(request, "core/auth.html", context, status=429)


@require_http_methods(["GET", "POST"])
def email_login(request):
    next_url = _next_url(request)
    context = {"mode": "login", "next_url": next_url, "values": {}}
    if request.method == "GET":
        return render(request, "core/auth.html", context)

    email = normalize_email(request.POST.get("email", ""))
    context["values"] = {"email": email}
    try:
        validate_email(email)
    except Exception:
        context["errors"] = ["Enter a valid email address."]
        return render(request, "core/auth.html", context, status=400)

    try:
        response = _begin_code_flow(
            request,
            purpose=EmailCode.LOGIN,
            email=email,
            next_url=next_url,
        )
    except CodeRequestError as exc:
        context["errors"] = [str(exc)]
        return render(request, "core/auth.html", context, status=429)
    return response


@require_http_methods(["GET", "POST"])
def verify_code(request):
    challenge_id = request.session.get(CHALLENGE_SESSION_KEY)
    if not challenge_id:
        return redirect(f"{reverse('core:login')}?next={next_url_for_query(_next_url(request))}")
    challenge = get_object_or_404(EmailCode, pk=challenge_id)
    next_url = _next_url(request)
    context = {
        "mode": "verify",
        "next_url": next_url,
        "email": challenge.email,
        "purpose": challenge.purpose,
        "username": challenge.username,
    }
    if request.method == "GET":
        if request.session.pop(RESEND_NOTE_SESSION_KEY, None):
            context["info"] = "We already sent that code — check your inbox."
        return render(request, "core/auth.html", context)

    try:
        user = verify_email_code(challenge_id, request.POST.get("code"))
    except CodeVerificationError as exc:
        context["errors"] = [str(exc)]
        return render(request, "core/auth.html", context, status=400)

    for key in (CHALLENGE_SESSION_KEY, NEXT_SESSION_KEY, MODE_SESSION_KEY):
        request.session.pop(key, None)
    login(request, user, backend="core.auth_backends.EmailBackend")
    return redirect(next_url)


@require_POST
def resend_code(request):
    challenge_id = request.session.get(CHALLENGE_SESSION_KEY)
    if not challenge_id:
        return redirect(f"{reverse('core:login')}?next={next_url_for_query(_next_url(request))}")
    challenge = get_object_or_404(EmailCode, pk=challenge_id)
    next_url = safe_next(request.session.get(NEXT_SESSION_KEY))
    try:
        new_challenge = issue_email_code(
            email=challenge.email,
            purpose=challenge.purpose,
            username=challenge.username,
            request_ip=_request_ip(request),
        )
    except CodeRequestError as exc:
        return render(
            request,
            "core/auth.html",
            {
                "mode": "verify",
                "next_url": next_url,
                "email": challenge.email,
                "purpose": challenge.purpose,
                "username": challenge.username,
                "errors": [str(exc)],
            },
            status=429,
        )
    # issue_email_code() returns the existing challenge, unchanged, when one
    # is still active within the resend cooldown — no new code was mailed.
    if new_challenge.pk == challenge.pk:
        request.session[RESEND_NOTE_SESSION_KEY] = True
    request.session[CHALLENGE_SESSION_KEY] = str(new_challenge.pk)
    return redirect("core:verify_code")


def next_url_for_query(value):
    from urllib.parse import quote

    return quote(value, safe="")


@require_POST
def logout_view(request):
    logout(request)
    return redirect(safe_next(request.POST.get("next")))


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
