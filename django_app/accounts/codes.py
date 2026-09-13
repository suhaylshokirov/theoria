"""Code issuing and verification rules (Task 89).

Plain functions over `email` + `purpose` -- no view, no request object, no
HTTP concerns -- so every rule here is unit-testable without a client and a
view only has to translate an enum member into a message.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import LoginCode

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_COOLDOWN = timedelta(seconds=60)
HOURLY_CAP = 5


class IssueResult(enum.Enum):
    ISSUED = "issued"
    COOLDOWN = "cooldown"
    HOURLY_CAP = "hourly_cap"


class VerifyResult(enum.Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    ALREADY_CONSUMED = "already_consumed"
    EXPIRED = "expired"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    WRONG_CODE = "wrong_code"


def _hash_code(code: str) -> str:
    # HMAC, not a slow password hash: six digits hold ~20 bits, which no work
    # factor rescues. The real defences are CODE_TTL and MAX_ATTEMPTS below.
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def issue(
    email: str, purpose: str, pending_username: str | None = None
) -> tuple[IssueResult, str | None]:
    """Issue a new code for `email` + `purpose`, or refuse under a throttle.

    Returns `(IssueResult, code)`. `code` is the plaintext six digits, handed
    back only so the caller can email it immediately -- it is never written
    to the database or a log line, and is `None` on any non-ISSUED result.
    """
    email = email.lower()
    now = timezone.now()

    with transaction.atomic():
        existing = LoginCode.objects.select_for_update().filter(email=email, purpose=purpose)

        last = existing.order_by("-created_at").first()
        if last is not None and last.created_at > now - RESEND_COOLDOWN:
            return IssueResult.COOLDOWN, None

        if existing.filter(created_at__gt=now - timedelta(hours=1)).count() >= HOURLY_CAP:
            return IssueResult.HOURLY_CAP, None

        # A new code for the same email+purpose retires whatever was still
        # outstanding, so only ever one live code exists per address+purpose.
        existing.filter(consumed_at__isnull=True).update(consumed_at=now)

        code = _generate_code()
        LoginCode.objects.create(
            email=email,
            purpose=purpose,
            code_hash=_hash_code(code),
            pending_username=pending_username,
            expires_at=now + CODE_TTL,
        )
        return IssueResult.ISSUED, code


def verify(email: str, purpose: str, submitted: str) -> tuple[VerifyResult, LoginCode | None]:
    """Verify `submitted` against the newest code for `email` + `purpose`.

    On `OK`, the row is already marked consumed -- the caller cannot forget
    to and accidentally leave a code reusable. Returns the `LoginCode` row
    only on `OK`, so the caller can read `pending_username` off it.
    """
    email = email.lower()
    now = timezone.now()

    with transaction.atomic():
        code_row = (
            LoginCode.objects.select_for_update()
            .filter(email=email, purpose=purpose)
            .order_by("-created_at")
            .first()
        )
        if code_row is None:
            return VerifyResult.NOT_FOUND, None
        if code_row.consumed_at is not None:
            return VerifyResult.ALREADY_CONSUMED, None
        if code_row.expires_at <= now:
            return VerifyResult.EXPIRED, None
        if code_row.attempts >= MAX_ATTEMPTS:
            return VerifyResult.TOO_MANY_ATTEMPTS, None

        if not hmac.compare_digest(code_row.code_hash, _hash_code(submitted)):
            code_row.attempts += 1
            code_row.save(update_fields=["attempts"])
            if code_row.attempts >= MAX_ATTEMPTS:
                return VerifyResult.TOO_MANY_ATTEMPTS, None
            return VerifyResult.WRONG_CODE, None

        code_row.consumed_at = now
        code_row.save(update_fields=["consumed_at"])
        return VerifyResult.OK, code_row
