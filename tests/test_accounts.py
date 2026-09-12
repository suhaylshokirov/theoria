"""Unit tests for the accounts app.

Follows the same manual django.setup() pattern as tests/test_django_views.py
(no pytest-django yet -- that lands in Task 96, alongside a real `default`
test database for the sign-up/sign-in/gating tests planned there). These
tests only cover accounts/emails.py (Task 90), which needs no database at
all: EmailMultiAlternatives.send() just renders templates and appends to
django.core.mail.outbox, which setup_test_environment() below wires up.
"""

import os
import sys
from pathlib import Path

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from django.core import mail  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402

from accounts.emails import send_code_email  # noqa: E402


def setup_module(module):
    # Points settings.EMAIL_BACKEND at locmem and resets mail.outbox, exactly
    # like pytest-django's mailoutbox fixture would once Task 96 adds it.
    setup_test_environment()


def teardown_module(module):
    teardown_test_environment()


def setup_function(function):
    mail.outbox = []


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
