from django.urls import path

from core import views

app_name = "share"

urlpatterns = [
    path("<str:token>/", views.shared_page, name="page"),
]
