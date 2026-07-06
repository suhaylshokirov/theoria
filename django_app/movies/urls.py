from django.urls import path

from movies import views

app_name = "movies"

urlpatterns = [
    path("", views.home, name="home"),
    path("movies/<int:movie_id>/", views.movie_detail, name="movie_detail"),
    path("actors/<int:actor_id>/", views.actor_detail, name="actor_detail"),
    path("directors/<int:director_id>/", views.director_detail, name="director_detail"),
]
