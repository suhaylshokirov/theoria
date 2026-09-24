"""Forms for the sign-up / sign-in / verify flows (Tasks 91-92)."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from .models import validate_username_characters, validate_username_not_reserved

User = get_user_model()


class SignupForm(forms.Form):
    username = forms.CharField(
        max_length=30,
        validators=[validate_username_characters, validate_username_not_reserved],
    )
    email = forms.EmailField()

    def clean_email(self):
        return self.cleaned_data["email"].lower()

    def clean(self):
        # A single combined error, not "username taken" / "email taken"
        # separately -- either one existing is reported the same way, so a
        # reader probing for registered addresses learns nothing more than
        # "something here is already registered."
        cleaned = super().clean()
        username = cleaned.get("username")
        email = cleaned.get("email")
        if username and email:
            taken = (
                User.objects.filter(username__iexact=username).exists()
                or User.objects.filter(email=email).exists()
            )
            if taken:
                raise forms.ValidationError(_("Username or email already exists."))
        return cleaned


class UsernameChangeForm(forms.Form):
    """Rename a signed-in account. The same field and validators as
    SignupForm's username, and the same case-insensitive uniqueness check --
    minus the reader's own row, so "ada" -> "Ada" is a legal re-casing.

    Unlike signup, "taken" is reported plainly: there's no email in play to
    hide, and the view rate-limits the endpoint the same way signup is."""

    username = forms.CharField(
        max_length=30,
        validators=[validate_username_characters, validate_username_not_reserved],
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_username(self):
        username = self.cleaned_data["username"]
        if username == self.user.username:
            raise forms.ValidationError(_("That's already your username."))
        taken = User.objects.filter(username__iexact=username).exclude(pk=self.user.pk).exists()
        if taken:
            raise forms.ValidationError(_("That username is already taken."))
        return username


class EmailOnlyForm(forms.Form):
    """Sign-in: just the address. Whether an account exists for it is
    decided by the view, not the form -- an unknown address is a valid,
    well-formed email, just not one anything should be issued for."""

    email = forms.EmailField()

    def clean_email(self):
        return self.cleaned_data["email"].lower()


class VerifyForm(forms.Form):
    code = forms.RegexField(
        regex=r"^\d{6}$",
        error_messages={"invalid": _("Enter the 6-digit code.")},
    )
