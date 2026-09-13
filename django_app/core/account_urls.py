from django.urls import path

from core import views

app_name = "account"

urlpatterns = [
    path("", views.account, name="index"),
    path(
        "collections/<str:kind>/<str:content_type>/<int:content_id>/toggle/",
        views.toggle_collection,
        name="toggle_collection",
    ),
    path(
        "collections/<str:kind>/<int:item_id>/remove/",
        views.remove_item,
        name="remove_item",
    ),
    path(
        "collections/<str:kind>/<int:item_id>/move/<str:direction>/",
        views.move_item,
        name="move_item",
    ),
]
