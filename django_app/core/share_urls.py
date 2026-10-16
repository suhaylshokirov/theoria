from django.urls import path

from core import views

app_name = "share"

urlpatterns = [
    path("<str:token>/", views.shared_page, name="page"),
    path("<str:token>/og.png", views.shared_og, name="og"),
    path("<str:token>/card/", views.shared_card, name="card"),
]
