"""``manage.py serve`` — freshen the local replica, then run the dev server.

The nightly refresh job writes Neon while the laptop is off, so the local
Postgres copy Django reads can lag a day. This command makes "start the site"
also mean "make sure the data is current" — but it only pays the ~60s sync when
Neon actually has a newer ``ingestion_date``; a normal restart checks one date
and moves on.

    python manage.py serve
    python manage.py serve 0.0.0.0:8001
    python manage.py serve --no-sync        # skip the freshness check entirely
"""

from __future__ import annotations

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand


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
        if first_run and not options["no_sync"]:
            from scripts.sync_warehouse_from_neon import sync_if_stale

            sync_if_stale()

        runserver_args = [options["addrport"]] if options["addrport"] else []
        call_command("runserver", *runserver_args)
