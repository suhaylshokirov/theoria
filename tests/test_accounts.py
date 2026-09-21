"""Unit tests for the accounts app.

Follows the same manual django.setup() pattern as tests/test_django_views.py
(no pytest-django yet -- that lands in Task 96, alongside the full sign-up/
sign-in/gating test suite planned there). Most of what's here covers
accounts/emails.py (Task 90), which needs no database at all:
EmailMultiAlternatives.send() just renders templates and appends to
django.core.mail.outbox, which setup_test_environment() below wires up.

The send-failure tests near the bottom are pulled forward from that Task 96
plan rather than left for it, the same call made for a Task 94 test fix
(see for_learning.md): they exercise a real bug fix (accounts/views.py
not catching a send_code_email() failure, contradicting that module's own
documented contract) made in direct response to a user report, and the fix
needs the real Client + `default` database test_django_views.py already
uses, not anything Task 96 specifically adds.
"""

import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core import mail  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

from accounts.codes import RESEND_COOLDOWN  # noqa: E402
from accounts.emails import send_code_email  # noqa: E402
from accounts.models import LoginCode  # noqa: E402

User = get_user_model()
client = Client()


def setup_module(module):
    # Points settings.EMAIL_BACKEND at locmem and resets mail.outbox, exactly
    # like pytest-django's mailoutbox fixture would once Task 96 adds it.
    setup_test_environment()


def teardown_module(module):
    teardown_test_environment()


def setup_function(function):
    mail.outbox = []
    cache.clear()
    # A completed verify signs the shared client in, and the auth pages now
    # redirect signed-in readers away -- start every test signed out.
    client.logout()


def test_send_code_email_signup_renders_code_and_purpose_subject():
    send_code_email("reader@example.com", "482913", purpose="signup")

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["reader@example.com"]
    assert message.subject == "Confirm your Theoria account"
    assert "482913" in message.body


def test_send_code_email_login_uses_a_different_subject():
    send_code_email("reader@example.com", "719284", purpose="login")

    message = mail.outbox[0]
    assert message.subject == "Your Theoria sign-in code"
    assert "719284" in message.body


def test_send_code_email_attaches_an_html_alternative():
    send_code_email("reader@example.com", "555000", purpose="login")

    message = mail.outbox[0]
    assert len(message.alternatives) == 1
    html_body, mimetype = message.alternatives[0]
    assert mimetype == "text/html"
    assert "555000" in html_body
    # Mail clients don't support webfonts or CSS custom properties -- the
    # template must be inline-styled only (see Task 90 / Task 93's rules).
    assert "@font-face" not in html_body
    assert "var(--" not in html_body


def test_send_code_email_never_leaks_the_purpose_keyword_to_the_reader():
    send_code_email("reader@example.com", "482913", purpose="signup")

    message = mail.outbox[0]
    assert "signup" not in message.body.lower()
    assert "signup" not in message.alternatives[0][0].lower()


# ---------------------------------------------------------------------------
# A send_code_email() failure must not 500 -- see accounts/views.py's
# _send_code_or_flash_error() and its docstring for the contract this
# enforces. A raising SMTP backend (bad credentials, an unreachable host, a
# timeout) is simulated by patching send_code_email itself, since exercising
# a real SMTP failure would mean actually misconfiguring EMAIL_HOST.
# ---------------------------------------------------------------------------

_SEND_FAILURE_MESSAGE = "We couldn't send that code — please try again in a moment."


def test_signup_send_failure_does_not_500_and_does_not_advance_to_verify():
    email = "send-fail-signup@example.com"
    User.objects.filter(email=email).delete()
    # Also clear any LoginCode left by a previous run of this same test: its
    # own resend cooldown would otherwise refuse this issue() within 60
    # seconds of that leftover row, well within how fast a test suite reruns.
    LoginCode.objects.filter(email=email).delete()

    with patch("accounts.views.send_code_email", side_effect=OSError("smtp unreachable")):
        response = client.post(
            "/accounts/signup/",
            {"username": "sendfailsignup", "email": email},
            follow=True,
        )

    assert response.status_code == 200
    # follow=True means a redirect would have landed on verify's 200 too --
    # the real assertion is that it stayed on signup, not that no redirect
    # error occurred.
    assert response.request["PATH_INFO"] == "/accounts/signup/"
    assert any(_SEND_FAILURE_MESSAGE in str(m) for m in response.context["messages"])
    assert not User.objects.filter(email=email).exists()

    LoginCode.objects.filter(email=email).delete()
    User.objects.filter(email=email).delete()


def test_login_send_failure_does_not_500_and_does_not_advance_to_verify():
    email = "send-fail-login@example.com"
    User.objects.filter(email=email).delete()
    LoginCode.objects.filter(email=email).delete()
    user = User.objects.create_user(email=email, username="sendfaillogin")

    with patch("accounts.views.send_code_email", side_effect=OSError("smtp unreachable")):
        response = client.post("/accounts/login/", {"email": email}, follow=True)

    assert response.status_code == 200
    assert response.request["PATH_INFO"] == "/accounts/login/"
    assert any(_SEND_FAILURE_MESSAGE in str(m) for m in response.context["messages"])

    LoginCode.objects.filter(email=email).delete()
    user.delete()


# ---------------------------------------------------------------------------
# Signup hardening: case-insensitive uniqueness, the combined "username or
# email already exists" message, SQL-injection-style input, the IP rate
# limit, and the next-page redirect after verification.
# ---------------------------------------------------------------------------


def test_signup_rejects_a_username_that_differs_only_by_case():
    User.objects.filter(username__iexact="qwerty123").delete()
    owner = User.objects.create_user(email="qwerty-owner@example.com", username="qwerty123")

    response = client.post(
        "/accounts/signup/",
        {"username": "QWerty123", "email": "someone-else@example.com"},
    )

    assert response.status_code == 200
    assert response.context["form"].errors["__all__"] == ["Username or email already exists."]
    assert not User.objects.filter(email="someone-else@example.com").exists()

    owner.delete()


def test_signup_duplicate_email_gives_the_same_combined_message_not_a_hint():
    email = "dupe-email@example.com"
    User.objects.filter(email=email).delete()
    owner = User.objects.create_user(email=email, username="dupeemailowner")

    response = client.post(
        "/accounts/signup/",
        {"username": "brandnewhandle", "email": email},
    )

    assert response.status_code == 200
    errors = response.context["form"].errors
    # One combined, non-field error -- never a field-specific "username taken"
    # or "email taken" that would tell a prober which one matched.
    assert list(errors.keys()) == ["__all__"]
    assert errors["__all__"] == ["Username or email already exists."]

    owner.delete()


def test_signup_rejects_a_username_shorter_than_three_characters():
    response = client.post(
        "/accounts/signup/", {"username": "ab", "email": "too-short@example.com"}
    )

    assert response.status_code == 200
    assert response.context["form"].errors["username"] == [
        "Usernames must be 3-30 characters: letters, numbers, and underscores only."
    ]
    assert not User.objects.filter(email="too-short@example.com").exists()


def test_signup_rejects_a_username_longer_than_thirty_characters():
    response = client.post(
        "/accounts/signup/", {"username": "a" * 31, "email": "too-long@example.com"}
    )

    assert response.status_code == 200
    # A 31-character value fails both the field's own max_length=30 (Django's
    # built-in message) and the {3,30} regex validator -- Django's
    # run_validators() collects every failing validator, not just the first.
    assert response.context["form"].errors["username"] == [
        "Usernames must be 3-30 characters: letters, numbers, and underscores only.",
        "Ensure this value has at most 30 characters (it has 31).",
    ]
    assert not User.objects.filter(email="too-long@example.com").exists()


def test_signup_rejects_a_reserved_username():
    # "browse" is a route (movies:browse), not just a word -- RESERVED_USERNAMES
    # in accounts/models.py must list it or a reader could sign up as
    # /browse/ and shadow the nav link.
    response = client.post(
        "/accounts/signup/", {"username": "browse", "email": "reserved-browse@example.com"}
    )

    assert response.status_code == 200
    assert response.context["form"].errors["username"] == [
        "browse is a reserved name and can't be used as a username."
    ]
    assert not User.objects.filter(email="reserved-browse@example.com").exists()


def test_signup_accepts_usernames_at_the_three_and_thirty_character_boundaries():
    # A valid signup redirects to the verify page rather than re-rendering
    # the form, so success here is "redirected", not "no form errors".
    User.objects.filter(username__in=["abc", "a" * 30]).delete()
    for email in ("min-boundary@example.com", "max-boundary@example.com"):
        LoginCode.objects.filter(email=email).delete()

    short_response = client.post(
        "/accounts/signup/", {"username": "abc", "email": "min-boundary@example.com"}
    )
    assert short_response.status_code == 302

    cache.clear()  # avoid the per-IP signup rate limit between these two posts

    long_response = client.post(
        "/accounts/signup/", {"username": "a" * 30, "email": "max-boundary@example.com"}
    )
    assert long_response.status_code == 302

    LoginCode.objects.filter(email__in=["min-boundary@example.com", "max-boundary@example.com"]).delete()
    User.objects.filter(username__in=["abc", "a" * 30]).delete()


def test_signup_rejects_sql_injection_style_username_safely():
    payload = "robert'); DROP TABLE accounts_user;--"
    email = "sqli-test@example.com"
    User.objects.filter(email=email).delete()

    response = client.post("/accounts/signup/", {"username": payload, "email": email})

    # Django's ORM parameterizes every query it builds, so the payload is
    # just an ordinary (invalid) string -- rejected by the username format
    # validator, never executed as SQL. The table surviving is the real
    # assertion: a working User.objects.count() call proves it's intact.
    assert response.status_code == 200
    assert not User.objects.filter(email=email).exists()
    assert User.objects.count() >= 0


def test_signup_rate_limits_repeated_posts_from_one_ip():
    for email in ("rl-1@example.com", "rl-2@example.com", "rl-3@example.com"):
        User.objects.filter(email=email).delete()
        LoginCode.objects.filter(email=email).delete()

    with patch("accounts.views.SIGNUP_RATE_LIMIT", 2):
        client.post("/accounts/signup/", {"username": "ratelimitone", "email": "rl-1@example.com"})
        client.post("/accounts/signup/", {"username": "ratelimittwo", "email": "rl-2@example.com"})
        response = client.post(
            "/accounts/signup/", {"username": "ratelimitthree", "email": "rl-3@example.com"}
        )

    assert response.status_code == 200
    assert any("Too many sign-up attempts" in str(m) for m in response.context["messages"])
    assert not User.objects.filter(email="rl-3@example.com").exists()

    for email in ("rl-1@example.com", "rl-2@example.com", "rl-3@example.com"):
        LoginCode.objects.filter(email=email).delete()
        User.objects.filter(email=email).delete()


def test_signup_then_verify_redirects_to_the_page_the_reader_came_from():
    email = "next-redirect@example.com"
    username = "nextredirectuser"
    User.objects.filter(email=email).delete()
    LoginCode.objects.filter(email=email).delete()

    response = client.post(
        "/accounts/signup/?next=/movies/42/",
        {"username": username, "email": email, "next": "/movies/42/"},
    )
    assert response.status_code == 302
    assert response.url == "/accounts/verify/"

    assert len(mail.outbox) == 1
    match = re.search(r"\b(\d{6})\b", mail.outbox[0].body)
    assert match, "no 6-digit code found in the signup email body"

    verify_response = client.post("/accounts/verify/", {"code": match.group(1)})
    assert verify_response.status_code == 302
    assert verify_response.url == "/movies/42/"
    assert User.objects.filter(email=email, username=username).exists()

    LoginCode.objects.filter(email=email).delete()
    User.objects.filter(email=email).delete()


def test_resend_send_failure_does_not_500_and_reports_the_same_error():
    email = "send-fail-resend@example.com"
    User.objects.filter(email=email).delete()
    LoginCode.objects.filter(email=email).delete()

    with patch("accounts.views.send_code_email"):
        client.post("/accounts/signup/", {"username": "sendfailresend", "email": email})

    # The signup above just issued a code, and issue()'s resend cooldown
    # would otherwise refuse a second one for the next 60 seconds -- which
    # would make the resend below hit COOLDOWN, never even reach
    # send_code_email(), and defeat the point of this test. Backdating past
    # the cooldown here is simpler than waiting it out or mocking time.
    LoginCode.objects.filter(email=email).update(
        created_at=timezone.now() - RESEND_COOLDOWN - timedelta(seconds=5)
    )

    with patch("accounts.views.send_code_email", side_effect=OSError("smtp unreachable")):
        response = client.post("/accounts/verify/", {"resend": "1"}, follow=True)

    assert response.status_code == 200
    assert any(_SEND_FAILURE_MESSAGE in str(m) for m in response.context["messages"])

    LoginCode.objects.filter(email=email).delete()
    User.objects.filter(email=email).delete()


def _signed_in_client(email: str, username: str) -> Client:
    User.objects.filter(email=email).delete()
    user = User.objects.create_user(username=username, email=email)
    signed_in = Client()
    signed_in.force_login(user)
    return signed_in


def test_signed_in_reader_is_sent_away_from_every_auth_page():
    email = "signed-in-auth-pages@example.com"
    signed_in = _signed_in_client(email, "signedinauthpages")

    for url in ("/accounts/login/", "/accounts/signup/", "/accounts/verify/"):
        response = signed_in.get(url)
        assert response.status_code == 302, url
        assert response.url == "/me/", url

        # A POST is refused the same way -- no code issued, no email sent.
        post = signed_in.post(url, {"email": email, "username": "signedinauthpages", "code": "123456"})
        assert post.status_code == 302, url

    assert mail.outbox == []
    assert not LoginCode.objects.filter(email=email).exists()

    User.objects.filter(email=email).delete()


def test_signed_in_reader_is_sent_to_a_safe_next_not_an_external_one():
    email = "signed-in-next@example.com"
    signed_in = _signed_in_client(email, "signedinnext")

    response = signed_in.get("/accounts/login/?next=/movies/42/")
    assert response.url == "/movies/42/"

    external = signed_in.get("/accounts/login/?next=https://evil.example.com/")
    assert external.url == "/me/"

    User.objects.filter(email=email).delete()


def test_auth_pages_are_never_cached_so_back_asks_the_server_again():
    for url in ("/accounts/login/", "/accounts/signup/"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert "no-store" in response["Cache-Control"], url

    # Signed-in redirects carry it too, or the browser could cache the hop.
    email = "signed-in-no-store@example.com"
    signed_in = _signed_in_client(email, "signedinnostore")
    assert "no-store" in signed_in.get("/accounts/login/")["Cache-Control"]
    User.objects.filter(email=email).delete()


def test_change_username_requires_sign_in():
    response = client.get("/accounts/username/")
    assert response.status_code == 302
    assert response.url.startswith("/accounts/login/")


def test_change_username_renames_and_returns_to_the_account_page():
    email = "rename-ok@example.com"
    User.objects.filter(username__iexact="renamedhandle").delete()
    signed_in = _signed_in_client(email, "renamebefore")

    response = signed_in.post("/accounts/username/", {"username": "renamedhandle"})

    assert response.status_code == 302
    assert response.url == "/me/"
    assert User.objects.get(email=email).username == "renamedhandle"
    assert "no-store" in response["Cache-Control"]

    User.objects.filter(email=email).delete()


def test_change_username_refuses_a_name_someone_else_has_in_any_casing():
    email = "rename-taken@example.com"
    User.objects.filter(username__iexact="takenhandle").delete()
    owner = User.objects.create_user(email="taken-owner@example.com", username="takenhandle")
    signed_in = _signed_in_client(email, "renametaken")

    response = signed_in.post("/accounts/username/", {"username": "TakenHandle"})

    assert response.status_code == 200
    assert response.context["form"].errors["username"] == ["That username is already taken."]
    assert User.objects.get(email=email).username == "renametaken"

    owner.delete()
    User.objects.filter(email=email).delete()


def test_change_username_allows_re_casing_your_own_name():
    email = "rename-case@example.com"
    signed_in = _signed_in_client(email, "recaseme")

    response = signed_in.post("/accounts/username/", {"username": "ReCaseMe"})

    assert response.status_code == 302
    assert User.objects.get(email=email).username == "ReCaseMe"
    User.objects.filter(email=email).delete()


def test_change_username_applies_the_signup_format_and_reserved_rules():
    email = "rename-rules@example.com"
    signed_in = _signed_in_client(email, "renamerules")

    for bad in ("ab", "a" * 31, "has space", "robert'); DROP TABLE accounts_user;--", "admin", "ME"):
        response = signed_in.post("/accounts/username/", {"username": bad})
        assert response.status_code == 200, bad
        assert response.context["form"].errors["username"], bad

    same = signed_in.post("/accounts/username/", {"username": "renamerules"})
    assert same.context["form"].errors["username"] == ["That's already your username."]

    assert User.objects.get(email=email).username == "renamerules"
    User.objects.filter(email=email).delete()


def test_change_username_is_rate_limited():
    email = "rename-rl@example.com"
    signed_in = _signed_in_client(email, "renamerl")

    with patch("accounts.views.USERNAME_CHANGE_RATE_LIMIT", 1):
        signed_in.post("/accounts/username/", {"username": "renamerlone"})
        response = signed_in.post("/accounts/username/", {"username": "renamerltwo"})

    assert response.status_code == 200
    assert any("Too many username changes" in str(m) for m in response.context["messages"])
    assert User.objects.get(email=email).username == "renamerlone"
    User.objects.filter(email=email).delete()


def test_account_page_links_to_change_username():
    email = "rename-link@example.com"
    signed_in = _signed_in_client(email, "renamelink")
    response = signed_in.get("/me/")
    assert b'href="/accounts/username/"' in response.content
    User.objects.filter(email=email).delete()


def test_account_page_empty_collections_link_to_browse():
    # A brand-new account has nothing in any of the three collections, so
    # /me/ renders the "Browse movies"/"Find something" CTAs (Liked, Watch
    # later) and the "Add to Top" empty rank slots -- all of them should
    # point at the combined Browse page now, not the old movies:home target.
    email = "empty-collections@example.com"
    signed_in = _signed_in_client(email, "emptycollections")
    response = signed_in.get("/me/")
    content = response.content.decode()
    assert content.count('href="/browse/"') >= 3
    User.objects.filter(email=email).delete()
