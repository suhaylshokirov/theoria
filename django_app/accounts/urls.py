from django.contrib.auth.views import LogoutView
from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("login/", views.login_view, name="login"),
    path("verify/", views.verify, name="verify"),
    path("username/", views.change_username, name="change_username"),
    # Django's own LogoutView already refuses GET (since 4.1) and falls back
    # to settings.LOGOUT_REDIRECT_URL -- no reason to subclass it just to
    # flash a "Signed out." message no longer shown.
    path("logout/", LogoutView.as_view(), name="logout"),
]
