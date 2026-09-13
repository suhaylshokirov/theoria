"""Forms for the sign-up / sign-in / verify flows (Tasks 91-92)."""

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

from .models import validate_username_characters, validate_username_not_reserved

User = get_user_model()


class SignupForm(forms.Form):
    username = forms.CharField(
        max_length=30,
        validators=[validate_username_characters, validate_username_not_reserved],
    )
    email = forms.EmailField()

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("That username is taken.")
        return username

    def clean_email(self):
        return self.cleaned_data["email"].lower()


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
        error_messages={"invalid": "Enter the 6-digit code."},
    )
