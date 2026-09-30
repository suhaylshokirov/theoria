# Generated manually for the conversation-aware movie companion.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="ChatSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assistant_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="ChatTurn",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("user", "User"), ("assistant", "Assistant")], max_length=10)),
                ("message", models.TextField()),
                ("intent", models.CharField(blank=True, max_length=30)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="turns", to="assistant.chatsession")),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.CreateModel(
            name="RecommendationEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content_type", models.CharField(max_length=10)),
                ("content_id", models.PositiveIntegerField()),
                ("title", models.CharField(max_length=255)),
                ("shown_at", models.DateTimeField(auto_now_add=True)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recommendation_events", to="assistant.chatsession")),
            ],
        ),
        migrations.AddIndex(
            model_name="chatsession",
            index=models.Index(fields=["user", "-updated_at"], name="assistant_c_user_id_19b5fd_idx"),
        ),
        migrations.AddIndex(
            model_name="chatturn",
            index=models.Index(fields=["session", "created_at"], name="assistant_c_session_31f595_idx"),
        ),
        migrations.AddIndex(
            model_name="recommendationevent",
            index=models.Index(fields=["session", "shown_at"], name="assistant_r_session_845d66_idx"),
        ),
        migrations.AddIndex(
            model_name="recommendationevent",
            index=models.Index(fields=["session", "content_type", "content_id"], name="assistant_r_session_b6265d_idx"),
        ),
    ]
