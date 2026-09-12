"""Sends the one email the whole accounts feature depends on (Task 90).

A plain function, no view logic: it either sends or raises. The caller (a
view in Phase C) is responsible for catching a send failure and showing the
reader a retry line instead of letting it 500 -- this module doesn't decide
what the reader sees, only whether the mail went out.
"""

from __future__ import annotations

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def send_code_email(email: str, code: str, purpose: str) -> None:
    subject = (
        "Confirm your Theoria account" if purpose == "signup" else "Your Theoria sign-in code"
    )
    context = {"code": code, "purpose": purpose}

    # Plain text is the real message; HTML is the enhancement most inboxes
    # will actually render.
    text_body = render_to_string("accounts/email/code.txt", context)
    html_body = render_to_string("accounts/email/code.html", context)

    message = EmailMultiAlternatives(subject=subject, body=text_body, to=[email])
    message.attach_alternative(html_body, "text/html")
    message.send()
