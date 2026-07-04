from django.urls import path

from movies import views

app_name = "movies"

urlpatterns = [
    path("", views.home, name="home"),
    path("movies/<int:movie_id>/", views.movie_detail, name="movie_detail"),
]
