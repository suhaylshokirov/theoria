from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("assistant", "0003_name_existing_chat_sessions")]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="memory",
            field=models.JSONField(blank=True, default=dict),
        )
    ]
