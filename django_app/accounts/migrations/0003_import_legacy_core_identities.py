"""Move the pre-accounts identity data forward without changing its IDs.

``core.0001_initial`` originally owned ``core_user`` and the collections
foreign key. That migration was applied before the accounts app became the
canonical identity app, so an upgrade must retain user primary keys: existing
sessions and ``core_collection.user_id`` values both rely on them.

The legacy tables intentionally remain in place on upgraded databases. Their
applied initial migration cannot safely be rewritten in place, and the old
collection foreign key continues to be valid because the copied account rows
keep exactly the same IDs. Fresh installs create the collection foreign key
against accounts from the outset.

The legacy email-code hashes deliberately are not copied. They used Django's
password-hash format, whereas accounts uses an HMAC digest; treating either
format as the other would make a code unverifiable. Their ten-minute lifetime
makes expiration safer than carrying a misleading, unusable challenge over.
"""

from __future__ import annotations

from django.db import migrations


LEGACY_USER_TABLE = "core_user"
ACCOUNT_USER_TABLE = "accounts_user"
LEGACY_SESSION_BACKEND = "core.auth_backends.EmailBackend"
CURRENT_SESSION_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _quote(connection, name: str) -> str:
    return connection.ops.quote_name(name)


def _copy_memberships(connection, table_names: set[str], source: str, target: str, user_ids: set[int]):
    """Copy group/permission memberships for the users transferred here."""
    if source not in table_names or target not in table_names or not user_ids:
        return

    source_table = _quote(connection, source)
    target_table = _quote(connection, target)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT user_id, group_id FROM {source_table}")
        memberships = cursor.fetchall()
        cursor.execute(f"SELECT user_id, group_id FROM {target_table}")
        existing = set(cursor.fetchall())
        for user_id, group_id in memberships:
            if user_id not in user_ids or (user_id, group_id) in existing:
                continue
            cursor.execute(
                f"INSERT INTO {target_table} (user_id, group_id) VALUES (%s, %s)",
                [user_id, group_id],
            )
            existing.add((user_id, group_id))


def _copy_permissions(connection, table_names: set[str], source: str, target: str, user_ids: set[int]):
    """Copy direct permission assignments for transferred staff users."""
    if source not in table_names or target not in table_names or not user_ids:
        return

    source_table = _quote(connection, source)
    target_table = _quote(connection, target)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT user_id, permission_id FROM {source_table}")
        assignments = cursor.fetchall()
        cursor.execute(f"SELECT user_id, permission_id FROM {target_table}")
        existing = set(cursor.fetchall())
        for user_id, permission_id in assignments:
            if user_id not in user_ids or (user_id, permission_id) in existing:
                continue
            cursor.execute(
                f"INSERT INTO {target_table} (user_id, permission_id) VALUES (%s, %s)",
                [user_id, permission_id],
            )
            existing.add((user_id, permission_id))


def _reset_postgres_sequence(connection):
    """Explicit primary-key inserts do not advance a PostgreSQL sequence."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT setval(pg_get_serial_sequence(%s, %s), "
            "COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM accounts_user",
            [ACCOUNT_USER_TABLE, "id"],
        )


def _rewrite_legacy_session_backends(apps, database: str):
    """Keep sessions issued by the removed backend valid after the upgrade."""
    Session = apps.get_model("sessions", "Session")
    from django.contrib.sessions.backends.db import SessionStore

    session_store = SessionStore()
    for session in Session.objects.using(database).iterator():
        data = session_store.decode(session.session_data)
        if data.get("_auth_user_backend") != LEGACY_SESSION_BACKEND:
            continue
        data["_auth_user_backend"] = CURRENT_SESSION_BACKEND
        session.session_data = session_store.encode(data)
        session.save(using=database, update_fields=["session_data"])


def import_legacy_core_identities(apps, schema_editor):
    connection = schema_editor.connection
    database = connection.alias
    table_names = set(connection.introspection.table_names())

    # Fresh installs never created core_user, so their accounts tables are
    # already canonical and there is nothing to transfer.
    if LEGACY_USER_TABLE not in table_names:
        return

    legacy_table = _quote(connection, LEGACY_USER_TABLE)
    account_table = _quote(connection, ACCOUNT_USER_TABLE)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, password, last_login, is_superuser, email, username, "
            f"date_joined, is_active, is_staff FROM {legacy_table} ORDER BY id"
        )
        legacy_users = cursor.fetchall()
        cursor.execute(f"SELECT id, email, username FROM {account_table}")
        account_users = cursor.fetchall()

    accounts_by_id = {user_id: (email.lower(), username) for user_id, email, username in account_users}
    accounts_by_email = {email.lower(): user_id for user_id, email, _ in account_users}
    accounts_by_username = {username.casefold(): user_id for user_id, _, username in account_users}
    migrated_ids: set[int] = set()

    with connection.cursor() as cursor:
        for user_id, password, last_login, is_superuser, email, username, date_joined, is_active, is_staff in legacy_users:
            email = email.lower()
            username_key = username.casefold()
            account_at_id = accounts_by_id.get(user_id)

            if account_at_id is not None:
                if account_at_id[0] != email:
                    raise RuntimeError(
                        "Cannot migrate legacy core users: accounts_user already uses "
                        f"primary key {user_id} for a different email. Resolve the ID "
                        "collision before deploying this release."
                    )
                migrated_ids.add(user_id)
                continue

            if email in accounts_by_email:
                raise RuntimeError(
                    "Cannot migrate legacy core users: the email "
                    f"{email!r} already exists at a different accounts_user ID. "
                    "Resolve that duplicate before deploying this release."
                )
            if username_key in accounts_by_username:
                raise RuntimeError(
                    "Cannot migrate legacy core users: the username "
                    f"{username!r} conflicts case-insensitively with an accounts user. "
                    "Resolve that duplicate before deploying this release."
                )

            cursor.execute(
                f"INSERT INTO {account_table} "
                "(id, password, last_login, is_superuser, email, username, date_joined, is_active, is_staff) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    user_id,
                    password,
                    last_login,
                    is_superuser,
                    email,
                    username,
                    date_joined,
                    is_active,
                    is_staff,
                ],
            )
            accounts_by_id[user_id] = (email, username)
            accounts_by_email[email] = user_id
            accounts_by_username[username_key] = user_id
            migrated_ids.add(user_id)

    _copy_memberships(
        connection, table_names, "core_user_groups", "accounts_user_groups", migrated_ids
    )
    _copy_permissions(
        connection,
        table_names,
        "core_user_user_permissions",
        "accounts_user_user_permissions",
        migrated_ids,
    )
    _reset_postgres_sequence(connection)
    _rewrite_legacy_session_backends(apps, database)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_logincode"),
        ("core", "0001_initial"),
        ("sessions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(import_legacy_core_identities, migrations.RunPython.noop),
    ]
