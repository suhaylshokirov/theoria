# Generated manually for the AI Movie Companion's explicit title feedback.

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TitleFeedback",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content_type", models.CharField(choices=[("movie", "Movie"), ("series", "TV show")], max_length=10)),
                ("content_id", models.PositiveIntegerField()),
                ("watched", models.BooleanField(default=False)),
                ("disliked", models.BooleanField(default=False)),
                ("not_interested", models.BooleanField(default=False)),
                ("personal_rating", models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)])),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="title_feedback", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="titlefeedback",
            constraint=models.UniqueConstraint(fields=("user", "content_type", "content_id"), name="one_title_feedback_per_user"),
        ),
        migrations.AddIndex(
            model_name="titlefeedback",
            index=models.Index(fields=["user", "content_type", "content_id"], name="core_titlef_user_id_675c3d_idx"),
        ),
        migrations.AddIndex(
            model_name="titlefeedback",
            index=models.Index(fields=["user", "watched"], name="core_titlef_user_id_ef0a87_idx"),
        ),
    ]
