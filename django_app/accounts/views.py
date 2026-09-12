"""Sign-up, sign-in and code verification views.

Task 91 builds signup()/verify() (and, out of necessity -- signup redirects
here on a duplicate email -- the minimal login_view()). Task 92 completes
the picture: sign-out, the LOGIN_URL/LOGIN_REDIRECT_URL/LOGOUT_REDIRECT_URL
settings that let Django's own auth machinery (e.g. @login_required in Task
94) find this flow, and the account-enumeration trade-off this file takes.

Every POST redirects on success (never re-renders the same URL) so the back
button and a refresh stay safe. `next` is carried in the session, never
re-echoed from the query string straight into a form -- see `_safe_next()`.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from . import codes
from .emails import send_code_email
from .forms import EmailOnlyForm, SignupForm, VerifyForm
from .models import PURPOSE_LOGIN, PURPOSE_SIGNUP

User = get_user_model()

# Session keys carrying state between step 1 (signup/login) and step 2
# (verify) of the flow -- never trusted from the query string or a hidden
# form field, since either would let a reader submit a code for an address
# they never actually requested one for.
SESSION_EMAIL = "accounts_email"
SESSION_PURPOSE = "accounts_purpose"
SESSION_NEXT = "accounts_next"
SESSION_BOUNCE_TO = "accounts_bounce_to"

_VERIFY_ERROR_MESSAGES = {
    codes.VerifyResult.NOT_FOUND: "Request a new code to continue.",
    codes.VerifyResult.ALREADY_CONSUMED: "That code has already been used — request a new one.",
    codes.VerifyResult.EXPIRED: "That code has expired — request a new one.",
    codes.VerifyResult.TOO_MANY_ATTEMPTS: "Too many attempts — request a new code.",
    codes.VerifyResult.WRONG_CODE: "That code isn't right.",
}

_ISSUE_ERROR_MESSAGES = {
    codes.IssueResult.COOLDOWN: "A code was just sent — wait a moment before requesting another.",
    codes.IssueResult.HOURLY_CAP: "Too many codes requested for that address recently. Try again later.",
}


def _safe_next(request) -> str:
    candidate = request.GET.get("next") or request.POST.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}):
        return candidate
    return ""


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return email
    visible = local[:1] or "*"
    return f"{visible}…@{domain}"


def signup(request):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            username = form.cleaned_data["username"]

            if User.objects.filter(email=email).exists():
                messages.info(
                    request,
                    "An account already exists for that address — sign in instead.",
                )
                return redirect(f"{reverse('accounts:login')}?email={email}")

            result, code = codes.issue(email, PURPOSE_SIGNUP, pending_username=username)
            if result is codes.IssueResult.ISSUED:
                send_code_email(email, code, purpose=PURPOSE_SIGNUP)
                request.session[SESSION_EMAIL] = email
                request.session[SESSION_PURPOSE] = PURPOSE_SIGNUP
                request.session[SESSION_NEXT] = _safe_next(request)
                return redirect("accounts:verify")

            messages.error(request, _ISSUE_ERROR_MESSAGES[result])
    else:
        form = SignupForm()

    return render(request, "accounts/signup.html", {"form": form})


def login_view(request):
    if request.method == "POST":
        form = EmailOnlyForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]

            if not User.objects.filter(email=email).exists():
                # Deliberate account-enumeration trade-off (recorded properly
                # in tasks.md under Task 92): the asset being protected is a
                # film catalogue, and a generic "we've sent a code if an
                # account exists" message would strand every reader who
                # mistypes their address.
                messages.error(request, "No account for that address.")
            else:
                result, code = codes.issue(email, PURPOSE_LOGIN)
                if result is codes.IssueResult.ISSUED:
                    send_code_email(email, code, purpose=PURPOSE_LOGIN)
                    request.session[SESSION_EMAIL] = email
                    request.session[SESSION_PURPOSE] = PURPOSE_LOGIN
                    request.session[SESSION_NEXT] = _safe_next(request)
                    return redirect("accounts:verify")
                messages.error(request, _ISSUE_ERROR_MESSAGES[result])
    else:
        form = EmailOnlyForm(initial={"email": request.GET.get("email", "")})

    return render(request, "accounts/login.html", {"form": form, "next": _safe_next(request)})


def verify(request):
    email = request.session.get(SESSION_EMAIL)
    purpose = request.session.get(SESSION_PURPOSE)
    if not email or not purpose:
        return redirect("accounts:login")

    if request.method == "POST" and "resend" in request.POST:
        pending_username = None
        if purpose == PURPOSE_SIGNUP:
            last = (
                codes.LoginCode.objects.filter(email=email, purpose=purpose)
                .order_by("-created_at")
                .first()
            )
            pending_username = last.pending_username if last else None
        result, code = codes.issue(email, purpose, pending_username=pending_username)
        if result is codes.IssueResult.ISSUED:
            send_code_email(email, code, purpose=purpose)
            messages.success(request, "A new code is on its way.")
        else:
            messages.error(request, _ISSUE_ERROR_MESSAGES[result])
        return redirect("accounts:verify")

    if request.method == "POST":
        form = VerifyForm(request.POST)
        if form.is_valid():
            result, code_row = codes.verify(email, purpose, form.cleaned_data["code"])

            if result is codes.VerifyResult.OK:
                user = _complete_verification(request, email, purpose, code_row)
                if user is None:
                    # _complete_verification already queued a message and
                    # knows where to send the reader back to.
                    return redirect(request.session.pop(SESSION_BOUNCE_TO, "accounts:login"))

                login(request, user)
                request.session.pop(SESSION_EMAIL, None)
                request.session.pop(SESSION_PURPOSE, None)
                next_url = request.session.pop(SESSION_NEXT, "") or reverse("movies:home")
                messages.success(request, f"Signed in as {user.username}.")
                return redirect(next_url)

            form.add_error("code", _VERIFY_ERROR_MESSAGES[result])
    else:
        form = VerifyForm()

    return render(
        request,
        "accounts/verify.html",
        {"form": form, "masked_email": _mask_email(email), "purpose": purpose},
    )


def _complete_verification(request, email: str, purpose: str, code_row):
    """Create the user (signup) or fetch the existing one (login).

    Returns the `User` on success, or `None` if verification succeeded but
    the account couldn't actually be created/found -- in which case a
    message and a bounce target are already queued in the session.
    """
    if purpose == PURPOSE_LOGIN:
        try:
            return User.objects.get(email=email)
        except User.DoesNotExist:
            messages.error(request, "No account for that address.")
            request.session.pop(SESSION_EMAIL, None)
            request.session.pop(SESSION_PURPOSE, None)
            request.session[SESSION_BOUNCE_TO] = "accounts:login"
            return None

    username = code_row.pending_username
    try:
        with transaction.atomic():
            # Re-check: the username may have been taken by someone else
            # during the 10-minute window this code was live for.
            if User.objects.filter(username__iexact=username).exists():
                raise IntegrityError("username taken during verification window")
            return User.objects.create_user(email=email, username=username)
    except IntegrityError:
        messages.error(
            request,
            "That username was taken while you were verifying it — please choose another.",
        )
        request.session.pop(SESSION_EMAIL, None)
        request.session.pop(SESSION_PURPOSE, None)
        request.session[SESSION_BOUNCE_TO] = "accounts:signup"
        return None
