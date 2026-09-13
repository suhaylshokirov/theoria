"""``manage.py serve`` — freshen the local replica, then run the dev server.

The nightly refresh job writes Neon while the laptop is off, so the local
Postgres copy Django reads can lag a day. This command makes "start the site"
also mean "make sure the data is current" — but it only pays the ~60s sync when
Neon actually has a newer ``ingestion_date``; a normal restart checks one date
and moves on.

It also checks the *application* database (accounts, sessions, collections —
``default``, separate from the warehouse) is reachable and migrated, and
fails with one clear message instead of letting the first sign-up request
500 on a missing table.

    python manage.py serve
    python manage.py serve 0.0.0.0:8001
    python manage.py serve --no-sync        # skip the freshness check entirely
"""

from __future__ import annotations

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Sync the local warehouse replica from Neon if stale, then runserver."

    def add_arguments(self, parser):
        parser.add_argument("addrport", nargs="?", default="")
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Start the server without checking whether the replica is stale.",
        )

    def handle(self, *args, **options):
        # runserver's autoreloader re-execs this process on every file change,
        # with RUN_MAIN=true set. Only the first (parent) invocation should touch
        # the network — otherwise every edit triggers a freshness check.
        first_run = os.environ.get("RUN_MAIN") != "true"
        if first_run:
            self._check_app_database()
            if not options["no_sync"]:
                from scripts.sync_warehouse_from_neon import sync_if_stale

                sync_if_stale()

        runserver_args = [options["addrport"]] if options["addrport"] else []
        call_command("runserver", *runserver_args)

    def _check_app_database(self):
        """Fail loud, before runserver binds a port, if ``default`` (accounts,
        sessions, collections) either can't be reached or hasn't had
        migrations applied — the state a fresh ``APP_DATABASE_URL`` is in
        until someone runs ``migrate``. Left unchecked, this surfaces instead
        as an OperationalError traceback on the first page that touches a
        session or the auth tables (i.e. every page).
        """
        connection = connections["default"]
        try:
            executor = MigrationExecutor(connection)
        except Exception as exc:
            raise CommandError(
                "Could not reach the application database (APP_DATABASE_URL "
                f"in .env): {exc}\n"
                "Create it and apply migrations — see README 'Run the site':\n"
                "    createdb theoria_app\n"
                "    python manage.py migrate"
            ) from exc

        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if plan:
            pending = ", ".join(f"{m.app_label}.{m.name}" for m, _ in plan)
            raise CommandError(
                "The application database (APP_DATABASE_URL) has unapplied "
                f"migrations: {pending}.\nRun `python manage.py migrate` "
                "before starting the server — see README 'Run the site'."
            )
