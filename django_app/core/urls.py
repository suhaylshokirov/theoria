from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.auth_entry, name="entry"),
    path("signup/", views.signup, name="signup"),
    path("login/", views.email_login, name="login"),
    path("logout/", views.logout_view, name="logout"),
]
