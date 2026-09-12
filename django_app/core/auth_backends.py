"""Authentication backend for passwordless and Google-verified identities.

``authenticate()`` intentionally never returns a user. Both sign-in paths
(email code verification in ``core.services.verify_email_code`` and Google's
callback in ``core.google_auth``) already have a *verified* identity by the
time they call ``django.contrib.auth.login()`` — they pass this backend's
dotted path explicitly, so Django trusts the caller and skips the
authenticate step. Giving ``authenticate()`` a real implementation here would
mean any future ``django.contrib.auth.authenticate(email=...)`` call — with
no code, no password, nothing — logs someone in as that user. This backend
exists only to resolve a session's user id back to a ``User`` via
``get_user()``.
"""

from django.contrib.auth.backends import BaseBackend
from django.contrib.auth import get_user_model


class EmailBackend(BaseBackend):
    def authenticate(self, request, **kwargs):
        return None

    def get_user(self, user_id):
        try:
            return get_user_model().objects.get(pk=user_id, is_active=True)
        except get_user_model().DoesNotExist:
            return None
