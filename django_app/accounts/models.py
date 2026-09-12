"""The identity layer's own tables, in the `default` database (Task 87).

Unlike every model in `movies`/`analytics`, `User` is real and managed: it is
Django's `AUTH_USER_MODEL`, backed by an actual migration, in a database
`scripts/sync_warehouse_from_neon.py` never touches. Nothing here ever sets or
checks a real password -- sign-in is a mailed 6-digit code (see
`accounts/codes.py`, Task 89), so `password` is always left unusable via
`set_unusable_password()`.
"""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

# A future `/@username/` route (sketched for the Phase F collections feature)
# must never collide with a real path on the site, so these are refused at
# the model layer rather than left to be discovered as a routing bug later.
RESERVED_USERNAMES = frozenset({
    "me", "admin", "accounts", "login", "signup", "logout", "static",
    "movies", "tv", "people", "actors", "directors", "studios", "analytics",
})

validate_username_characters = RegexValidator(
    regex=r"^[A-Za-z0-9_]{3,30}\Z",
    message="Usernames must be 3-30 characters: letters, numbers, and underscores only.",
)


def validate_username_not_reserved(value: str) -> None:
    if value.lower() in RESERVED_USERNAMES:
        raise ValidationError(
            "%(value)s is a reserved name and can't be used as a username.",
            params={"value": value},
        )


class UserManager(BaseUserManager):
    """`create_user`/`create_superuser` never take or set a real password --
    every account here authenticates by emailed code, not by password."""

    use_in_migrations = True

    def _create_user(self, email: str, username: str, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        if not username:
            raise ValueError("Users must have a username.")
        user = self.model(email=self.normalize_email(email), username=username, **extra_fields)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email: str, username: str, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, username, **extra_fields)

    def create_superuser(self, email: str, username: str, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superusers must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusers must have is_superuser=True.")
        return self._create_user(email, username, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    username = models.CharField(
        max_length=30,
        validators=[validate_username_characters, validate_username_not_reserved],
        help_text="3-30 characters. Letters, numbers, and underscores only.",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        # A plain unique=True on `username` would only forbid an exact repeat,
        # letting "Ada" and "ada" both exist -- functional uniqueness over
        # Lower() is what actually enforces "one handle per casing family".
        constraints = [
            models.UniqueConstraint(Lower("username"), name="uq_user_username_ci"),
        ]

    def __str__(self) -> str:
        return self.username

    def save(self, *args, **kwargs):
        # Stored lowercased regardless of entry path (signup form, admin,
        # shell) so `USERNAME_FIELD` lookups never have to case-fold at query
        # time.
        self.email = self.email.lower()
        super().save(*args, **kwargs)


PURPOSE_SIGNUP = "signup"
PURPOSE_LOGIN = "login"
PURPOSE_CHOICES = [(PURPOSE_SIGNUP, "Sign up"), (PURPOSE_LOGIN, "Log in")]


class LoginCode(models.Model):
    """One row per 6-digit code issued. Keyed by `email`, not by `User` --
    at sign-up no user row exists yet, since Task 91 deliberately refuses to
    create one before the address is proven.

    All rule enforcement (expiry, attempt cap, throttles, hashing) lives in
    `accounts/codes.py`; this model is just the row shape.
    """

    email = models.EmailField()
    purpose = models.CharField(max_length=10, choices=PURPOSE_CHOICES)
    # HMAC-SHA256 hex digest of the code under SECRET_KEY -- the code itself
    # is never stored in plaintext anywhere, including here.
    code_hash = models.CharField(max_length=64)
    pending_username = models.CharField(max_length=30, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    # Doubles as "this row was superseded by a newer code" (Task 89's
    # issue() sets it on the prior outstanding row) and "this row was
    # successfully verified" -- both mean the same thing to verify(): dead,
    # never reusable.
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["email", "purpose", "-created_at"], name="idx_logincode_lookup"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(purpose__in=[PURPOSE_SIGNUP, PURPOSE_LOGIN]),
                name="ck_logincode_purpose",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.purpose})"
