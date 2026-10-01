from django.db import migrations


def name_existing_sessions(apps, schema_editor):
    ChatSession = apps.get_model("assistant", "ChatSession")
    ChatTurn = apps.get_model("assistant", "ChatTurn")
    for session in ChatSession.objects.filter(title="").iterator():
        first_message = (
            ChatTurn.objects.filter(session_id=session.pk, role="user")
            .order_by("created_at")
            .values_list("message", flat=True)
            .first()
        )
        if not first_message:
            continue
        title = " ".join(first_message.split())
        session.title = title if len(title) <= 80 else title[:77].rstrip() + "…"
        session.save(update_fields=["title"])


class Migration(migrations.Migration):
    dependencies = [("assistant", "0002_chatsession_title")]

    operations = [migrations.RunPython(name_existing_sessions, migrations.RunPython.noop)]
