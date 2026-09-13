from django.urls import path

from core import google_auth, views

app_name = "core"

urlpatterns = [
    path("", views.auth_entry, name="entry"),
    path("signup/", views.signup, name="signup"),
    path("login/", views.email_login, name="login"),
    path("verify/", views.verify_code, name="verify_code"),
    path("verify/resend/", views.resend_code, name="resend_code"),
    path("logout/", views.logout_view, name="logout"),
    path("google/", google_auth.google_login, name="google_login"),
    path("google/callback/", google_auth.google_callback, name="google_callback"),
    path("google/logout/", google_auth.google_logout, name="google_logout"),
]
