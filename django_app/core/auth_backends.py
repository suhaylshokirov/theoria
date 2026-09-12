"""Email-and-password authentication for Theoria accounts."""

from django.contrib.auth import get_user_model


class EmailBackend:
    def authenticate(self, request, email=None, password=None, **kwargs):
        if not email or not password:
            return None
        try:
            user = get_user_model().objects.get(email=email.strip().casefold(), is_active=True)
        except get_user_model().DoesNotExist:
            return None
        except get_user_model().MultipleObjectsReturned:
            return None
        return user if user.check_password(password) else None

    def get_user(self, user_id):
        try:
            return get_user_model().objects.get(pk=user_id, is_active=True)
        except get_user_model().DoesNotExist:
            return None
