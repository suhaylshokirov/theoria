from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("login/", views.login_view, name="login"),
    path("verify/", views.verify, name="verify"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
]
