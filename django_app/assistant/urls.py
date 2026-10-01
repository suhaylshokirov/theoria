from django.urls import path

from assistant import views

app_name = "assistant"

urlpatterns = [
    path("conversations/", views.conversations, name="conversations"),
    path("conversations/new/", views.new_conversation, name="new_conversation"),
    path("conversations/<uuid:session_id>/", views.conversation_detail, name="conversation_detail"),
    path("chat/", views.chat, name="chat"),
    path("feedback/", views.feedback, name="feedback"),
]
