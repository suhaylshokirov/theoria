from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_password_auth"),
    ]

    operations = [
        migrations.AlterField(
            model_name="collection",
            name="kind",
            field=models.CharField(
                choices=[
                    ("liked", "Liked"),
                    ("disliked", "Disliked"),
                    ("watch_later", "Watch later"),
                    ("top", "Top"),
                ],
                max_length=20,
            ),
        ),
    ]
