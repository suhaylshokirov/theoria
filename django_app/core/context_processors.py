from django.conf import settings


def auth_options(request):
    return {
        "google_enabled": bool(
            settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET
        ),
    }
