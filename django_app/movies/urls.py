from django.urls import path

from movies import views

app_name = "movies"

urlpatterns = [
    path("", views.home, name="home"),
]
