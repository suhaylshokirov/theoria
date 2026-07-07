from django.urls import path

from movies import views

app_name = "movies"

urlpatterns = [
    path("", views.home, name="home"),
    path("movies/", views.movie_list, name="movie_list"),
    path("movies/<int:movie_id>/", views.movie_detail, name="movie_detail"),
    path("actors/", views.actor_list, name="actor_list"),
    path("actors/<int:actor_id>/", views.actor_detail, name="actor_detail"),
    path("directors/", views.director_list, name="director_list"),
    path("directors/<int:director_id>/", views.director_detail, name="director_detail"),
    path("genres/", views.genre_list, name="genre_list"),
    path("genres/<int:genre_id>/", views.genre_detail, name="genre_detail"),
]
