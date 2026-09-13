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

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme

from movies.models import Movie

from . import codes, ratelimit
from .emails import send_code_email
from .forms import EmailOnlyForm, SignupForm, VerifyForm
from .models import PURPOSE_LOGIN, PURPOSE_SIGNUP

User = get_user_model()

logger = logging.getLogger(__name__)

# How many posters the auth pages' decorative gallery draws. At the
# gallery's 116px tile floor (auth.css), its column comfortably fits
# ~25-30 visible tiles -- this is sized with a small buffer over that, not
# a "whole catalog" claim the way MOSAIC_LIMIT (movies/views.py) is. Kept
# deliberately short: too many tiles at a legible size is exactly the
# "can't see them" crowding this replaced.
AUTH_GALLERY_LIMIT = 36


def _gallery_posters():
    """A small slice of recent posters for the auth pages' visual column.

    Same shape as home()'s mosaic query -- newest films with a poster --
    reused at a much smaller size. Decorative only (the template renders it
    aria-hidden), so no ordering guarantee beyond "recent" is needed.
    """
    return list(
        Movie.objects.using("warehouse")
        .filter(poster_path__isnull=False)
        .order_by("-release_date")
        .values_list("poster_path", flat=True)[:AUTH_GALLERY_LIMIT]
    )

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

# Every POST to /accounts/signup/ counts against this, valid or not -- the
# thing being throttled is hitting the endpoint at all (scripted username/email
# probing), not just successful signups. Generous enough that a shared office
# IP or a reader who mistypes a few times won't get caught in it.
SIGNUP_RATE_LIMIT = 20
SIGNUP_RATE_WINDOW_SECONDS = 60 * 60


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


def _send_code_or_flash_error(request, email: str, code: str, purpose: str) -> bool:
    """Send the code email, returning whether it went out.

    `emails.send_code_email()` either sends or raises -- by its own
    docstring's contract, the caller decides what the reader sees on
    failure. A raised SMTP error (bad credentials, an unreachable host, a
    timeout) must not 500 a reader who did nothing wrong; it gets logged
    here and turned into a retry line instead, and the caller stays on its
    current page rather than advancing to a verify step for a code that
    never actually reached anyone.
    """
    try:
        send_code_email(email, code, purpose=purpose)
    except Exception:
        logger.exception("Failed to send %s code email to %s", purpose, _mask_email(email))
        messages.error(request, "We couldn't send that code — please try again in a moment.")
        return False
    return True


# Phrased for a reader arriving at the sign-in page because @login_required
# bounced them off a gated page (Task 94) -- never in terms of views, routes,
# or table names. Falls back to a generic line for a `next` that matches
# none of these (or none at all, i.e. a reader who came here on their own).
_GATE_SUB_LINES = (
    ("/movies/", "Sign in to open a film's record."),
    ("/tv/", "Sign in to open a show's record."),
    ("/people/", "Sign in to open a person's record."),
    ("/analytics/", "Sign in to see the analytics."),
)


def _login_sub_line(next_url: str) -> str:
    for prefix, sub in _GATE_SUB_LINES:
        if next_url.startswith(prefix):
            return sub
    return "We'll email you a code."


def signup(request):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if ratelimit.is_rate_limited(
            request, scope="signup", limit=SIGNUP_RATE_LIMIT, window_seconds=SIGNUP_RATE_WINDOW_SECONDS
        ):
            messages.error(request, "Too many sign-up attempts — please try again later.")
        elif form.is_valid():
            email = form.cleaned_data["email"]
            username = form.cleaned_data["username"]

            result, code = codes.issue(email, PURPOSE_SIGNUP, pending_username=username)
            if result is codes.IssueResult.ISSUED:
                if _send_code_or_flash_error(request, email, code, PURPOSE_SIGNUP):
                    request.session[SESSION_EMAIL] = email
                    request.session[SESSION_PURPOSE] = PURPOSE_SIGNUP
                    request.session[SESSION_NEXT] = _safe_next(request)
                    return redirect("accounts:verify")
            else:
                messages.error(request, _ISSUE_ERROR_MESSAGES[result])
    else:
        form = SignupForm()

    return render(request, "accounts/signup.html", {"form": form, "gallery_posters": _gallery_posters()})


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
                    if _send_code_or_flash_error(request, email, code, PURPOSE_LOGIN):
                        request.session[SESSION_EMAIL] = email
                        request.session[SESSION_PURPOSE] = PURPOSE_LOGIN
                        request.session[SESSION_NEXT] = _safe_next(request)
                        return redirect("accounts:verify")
                else:
                    messages.error(request, _ISSUE_ERROR_MESSAGES[result])
    else:
        form = EmailOnlyForm(initial={"email": request.GET.get("email", "")})

    next_url = _safe_next(request)
    return render(
        request,
        "accounts/login.html",
        {"form": form, "next": next_url, "sub": _login_sub_line(next_url), "gallery_posters": _gallery_posters()},
    )


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
            _send_code_or_flash_error(request, email, code, purpose)
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
                next_url = request.session.pop(SESSION_NEXT, "") or resolve_url(settings.LOGIN_REDIRECT_URL)
                return redirect(next_url)

            form.add_error("code", _VERIFY_ERROR_MESSAGES[result])
    else:
        form = VerifyForm()

    return render(
        request,
        "accounts/verify.html",
        {
            "form": form,
            "masked_email": _mask_email(email),
            "purpose": purpose,
            "gallery_posters": _gallery_posters(),
        },
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
            # Re-check both: either the username or the email may have been
            # taken by someone else during the 10-minute window this code
            # was live for.
            taken = (
                User.objects.filter(username__iexact=username).exists()
                or User.objects.filter(email=email).exists()
            )
            if taken:
                raise IntegrityError("username or email taken during verification window")
            return User.objects.create_user(email=email, username=username)
    except IntegrityError:
        messages.error(
            request,
            "That username or email was taken while you were verifying — please start over.",
        )
        request.session.pop(SESSION_EMAIL, None)
        request.session.pop(SESSION_PURPOSE, None)
        request.session[SESSION_BOUNCE_TO] = "accounts:signup"
        return None
