"""Authentication backend for passwordless and Google-verified identities."""

from django.contrib.auth import get_user_model


class EmailBackend:
    def authenticate(self, request, email=None, **kwargs):
        if not email:
            return None
        try:
            return get_user_model().objects.get(email=email.strip().casefold(), is_active=True)
        except get_user_model().DoesNotExist:
            return None
        except get_user_model().MultipleObjectsReturned:
            return None

    def get_user(self, user_id):
        try:
            return get_user_model().objects.get(pk=user_id, is_active=True)
        except get_user_model().DoesNotExist:
            return None
