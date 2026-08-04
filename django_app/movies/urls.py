from django.urls import path

from movies import views

app_name = "movies"

urlpatterns = [
    path("", views.home, name="home"),
    path("movies/", views.movie_list, name="movie_list"),
    path("movies/<slug:movie_slug>/", views.movie_detail, name="movie_detail"),
    path("connect/", views.connect, name="connect"),
    path("people/", views.person_list, name="person_list"),
    path("people/<slug:person_slug>/", views.person_detail, name="person_detail"),
    # /actors/ and /directors/ survive as *indexes* — they're now filtered views
    # of dim_person by the credits someone holds. Their detail URLs 301 to the
    # single person page rather than 404ing, so existing links still resolve.
    path("actors/", views.actor_list, name="actor_list"),
    path("actors/<slug:actor_slug>/", views.actor_detail, name="actor_detail"),
    path("directors/", views.director_list, name="director_list"),
    path("directors/<slug:director_slug>/", views.director_detail, name="director_detail"),
    path("franchises/", views.collection_list, name="collection_list"),
    path("franchises/<slug:collection_slug>/", views.collection_detail, name="collection_detail"),
    path("genres/", views.genre_list, name="genre_list"),
    path("genres/<int:genre_id>/", views.genre_detail, name="genre_detail"),
]
