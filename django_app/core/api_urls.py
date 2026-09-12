from django.urls import path

from core import api_views

app_name = "account_api"

urlpatterns = [
    path("preferences/", api_views.preference_lists, name="preference_lists"),
    path(
        "preferences/<str:content_type>/<int:content_id>/",
        api_views.preference_detail,
        name="preference_detail",
    ),
]
