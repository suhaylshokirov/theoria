"""``manage.py warm_cache`` — fill the site's cache right after a warehouse load.

Without this, the first visitor after each nightly pays to build every cached
read, and if Neon has gone back to sleep by then that is the 20-45 s wait the
cache exists to avoid. The job that just wrote Neon has it awake, so it can fill
Redis in seconds.

It calls the same readers the views call (not the pages over HTTP -- the
dashboard needs a login), once per language, so the keys it writes are exactly
the ones a visitor will ask for. Language-free entries (stats, mosaic, dashboard
rows, genre lists) are built on the first language and are hits for the rest.
The per-show episode fragments are not warmed: ~740 pages, and the first reader
of each fills it.

Warming is best effort and never fails a nightly: no REDIS_URL, or a Redis that
stops answering mid-run, is a logged warning and exit 0 (the workflow step also
runs with continue-on-error). A reader that raises is a real bug and exits
non-zero, which shows up as an annotation without turning the job red.

    python django_app/manage.py warm_cache
    python django_app/manage.py warm_cache --langs en,ru
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import caches
from django.core.management.base import BaseCommand, CommandError
from django.utils import translation

import config
from analytics import cached_reads as analytics_reads
from core import datacache
from movies import cached_reads as movie_reads
from movies import i18n

logger = logging.getLogger(__name__)


def warm_jobs():
    """Every cached read the site has, as (label, zero-argument callable).

    Read through the modules at call time, so adding a reader here is the only
    registration a new cached read needs.
    """
    return [
        ("home_stats", movie_reads.home_stats),
        ("home_mosaic", movie_reads.home_mosaic),
        ("home_shelves", movie_reads.home_shelves),
        ("dashboard_rows", analytics_reads.dashboard_rows),
        ("genre_rows[movie]", lambda: movie_reads.genre_rows("movie")),
        ("genre_rows[series]", lambda: movie_reads.genre_rows("series")),
        ("genre_labels", i18n.genre_labels),
        ("country_labels", i18n.country_labels),
    ]


class Command(BaseCommand):
    help = "Fill the site cache (home, analytics, genre lists, labels) for every language."

    def add_arguments(self, parser):
        parser.add_argument(
            "--langs",
            default=",".join(code for code, _name in settings.LANGUAGES),
            help="Comma-separated language codes (default: every configured language).",
        )

    def handle(self, *args, **options):
        known = {code for code, _name in settings.LANGUAGES}
        langs = [code.strip() for code in options["langs"].split(",") if code.strip()]
        unknown = [code for code in langs if code not in known]
        if unknown or not langs:
            raise CommandError(
                f"Unknown language(s) {unknown or options['langs']!r}; configured: {sorted(known)}."
            )

        if not config.REDIS_URL:
            logger.warning("Cache not warmed: no REDIS_URL, so there is no shared cache to fill.")
            return

        started = time.monotonic()
        backend = caches["default"]
        version = datacache.data_version()
        if version is None:
            logger.warning(
                "No data version is published: the entries written now expire in %d minutes. "
                "Publish one first (python -m etl.data_version --bump).",
                datacache.TTL_UNVERSIONED // 60,
            )

        built = hits = 0
        for lang in langs:
            with translation.override(lang), datacache.request_scope() as scope:
                for label, read in warm_jobs():
                    try:
                        read()
                    except Exception as exc:  # noqa: BLE001 - name the read, then fail
                        raise CommandError(f"Warming {label} for {lang!r} failed: {exc}") from exc
            built += sum(1 for _name, outcome in scope.outcomes if outcome == "miss")
            hits += sum(1 for _name, outcome in scope.outcomes if outcome == "hit")
            self.stdout.write(f"{lang}: {scope.server_timing()}")

        is_down = getattr(backend, "is_down", None)
        if is_down is not None and is_down():
            # The backend swallows connection errors by design, so a dead Redis
            # looks like a clean run unless it is asked.
            logger.warning(
                "Cache not fully warmed: Redis became unreachable during the run "
                "(raise REDIS_SOCKET_TIMEOUT if the runner is far from the database)."
            )
            return

        self.stdout.write(
            f"Warmed {len(langs)} language(s) at version {version}: "
            f"{built} entries built, {hits} already cached, "
            f"{time.monotonic() - started:.1f}s"
        )
