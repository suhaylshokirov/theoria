from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.validators import validate_email
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from core.models import Collection, CollectionItem, User
from core.services import (
    collection_rows,
    ensure_default_collections,
    generate_username,
    move_collection_item,
    normalize_email,
    remove_collection_item,
    safe_next,
    taste_profile,
    toggle_collection_item,
    validate_name,
)

def _next_url(request):
    return safe_next(
        request.GET.get("next")
        or request.POST.get("next")
        or "/"
    )


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

    first_name = request.POST.get("first_name", "").strip()
    last_name = request.POST.get("last_name", "").strip()
    email = normalize_email(request.POST.get("email", ""))
    password = request.POST.get("password", "")
    context["values"] = {"first_name": first_name, "last_name": last_name, "email": email}
    errors = []
    try:
        first_name = validate_name(first_name, "first name")
    except ValueError as exc:
        errors.append(str(exc))
    try:
        last_name = validate_name(last_name, "last name")
    except ValueError as exc:
        errors.append(str(exc))
    try:
        email = normalize_email(email)
        validate_email(email)
    except Exception:
        errors.append("Enter a valid email address.")
    if email and User.objects.filter(email=email).exists():
        errors.append("That email already has an account. Sign in instead.")
    try:
        validate_password(password)
    except Exception as exc:
        errors.extend(exc.messages)
    if errors:
        context["errors"] = errors
        return render(request, "core/auth.html", context, status=400)
    user = User.objects.create_user(
        email=email,
        username=generate_username(first_name, last_name, email),
        password=password,
        first_name=first_name,
        last_name=last_name,
    )
    ensure_default_collections(user)
    login(request, user, backend="core.auth_backends.EmailBackend")
    return redirect(next_url)


@require_http_methods(["GET", "POST"])
def email_login(request):
    next_url = _next_url(request)
    context = {"mode": "login", "next_url": next_url, "values": {}}
    if request.method == "GET":
        return render(request, "core/auth.html", context)

    email = normalize_email(request.POST.get("email", ""))
    password = request.POST.get("password", "")
    context["values"] = {"email": email}
    try:
        validate_email(email)
    except Exception:
        context["errors"] = ["Enter a valid email address."]
        return render(request, "core/auth.html", context, status=400)

    user = authenticate(request, email=email, password=password)
    if user is None:
        context["errors"] = ["Email or password is not correct."]
        return render(request, "core/auth.html", context, status=400)
    login(request, user)
    return redirect(next_url)


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
    return render(
        request,
        "core/account.html",
        {"cards": cards, "taste_profile": taste_profile(request.user)},
    )


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
