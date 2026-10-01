from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("assistant", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="title",
            field=models.CharField(blank=True, max_length=80),
        )
    ]
