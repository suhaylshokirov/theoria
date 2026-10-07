"""Task 119: manage.py warm_cache fills the cache after a load."""

import io
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

import pytest  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.core.management.base import CommandError  # noqa: E402
from django.test import override_settings  # noqa: E402

import config  # noqa: E402
from core import datacache  # noqa: E402
from core.cache_backend import build_caches  # noqa: E402
from core.management.commands import warm_cache  # noqa: E402
from movies.i18n import current_lang  # noqa: E402


@pytest.fixture(autouse=True)
def _redis_configured():
    # The command refuses to run without REDIS_URL; the cache itself stays the
    # per-test LocMem from conftest, so nothing here touches a network.
    with patch.object(config, "REDIS_URL", "rediss://example:6379"):
        yield


def _run(*args):
    out = io.StringIO()
    call_command("warm_cache", *args, stdout=out)
    return out.getvalue()


def _recording_jobs():
    """Replace every registered read with a mock that records the active language."""
    seen = {}

    def make(label):
        def read():
            seen.setdefault(label, []).append(current_lang())
            # Go through the real cache path, as a real reader does.
            return datacache.cached(label, lambda: label, lang=current_lang())

        return read

    labels = [label for label, _read in warm_cache.warm_jobs()]
    return seen, [(label, make(label)) for label in labels]


def test_every_registered_read_runs_once_per_language():
    seen, jobs = _recording_jobs()
    with patch.object(warm_cache, "warm_jobs", return_value=jobs):
        _run()
    assert set(seen) == {label for label, _ in jobs}
    for label, languages in seen.items():
        assert languages == ["en", "ru", "uz"], label


def test_langs_option_limits_the_run():
    seen, jobs = _recording_jobs()
    with patch.object(warm_cache, "warm_jobs", return_value=jobs):
        _run("--langs", "ru")
    assert all(languages == ["ru"] for languages in seen.values())


def test_unknown_language_is_rejected_before_anything_runs():
    seen, jobs = _recording_jobs()
    with patch.object(warm_cache, "warm_jobs", return_value=jobs), pytest.raises(CommandError):
        _run("--langs", "ru,xx")
    assert seen == {}


def test_job_list_covers_every_cached_read_the_site_has():
    labels = [label for label, _ in warm_cache.warm_jobs()]
    assert labels == [
        "home_stats", "home_mosaic", "home_shelves", "dashboard_rows",
        "genre_rows[movie]", "genre_rows[series]", "genre_labels", "country_labels",
    ]


def test_the_entries_it_writes_are_the_ones_a_visitor_reads():
    cache.set("data_version", "v1")
    _seen, jobs = _recording_jobs()
    with patch.object(warm_cache, "warm_jobs", return_value=jobs):
        _run("--langs", "ru")
    # A later request builds the same key and finds the value already there.
    from django.utils import translation

    builder = MagicMock()
    with translation.override("ru"):
        assert datacache.cached("home_shelves", builder, lang="ru") == "home_shelves"
    builder.assert_not_called()


def test_without_redis_url_it_warns_and_does_nothing(caplog):
    seen, jobs = _recording_jobs()
    with patch.object(config, "REDIS_URL", ""), patch.object(
        warm_cache, "warm_jobs", return_value=jobs
    ), caplog.at_level("WARNING", logger="core.management.commands.warm_cache"):
        _run()
    assert seen == {}
    assert "no REDIS_URL" in caplog.text


def test_an_unreachable_redis_is_a_warning_and_exit_zero(caplog):
    # A real ResilientRedisCache pointed at a closed port: the connection error
    # is swallowed by design, so the command has to ask the backend afterwards.
    dead = build_caches("redis://127.0.0.1:1/0", socket_timeout=0.2)
    _seen, jobs = _recording_jobs()
    with override_settings(CACHES=dead), patch.object(
        warm_cache, "warm_jobs", return_value=jobs
    ), caplog.at_level("WARNING", logger="core.management.commands.warm_cache"):
        output = _run()  # must not raise: exit 0
    assert "became unreachable" in caplog.text
    assert "Warmed" not in output  # and must not claim success


def test_a_missing_data_version_is_flagged(caplog):
    _seen, jobs = _recording_jobs()
    with patch.object(warm_cache, "warm_jobs", return_value=jobs), caplog.at_level(
        "WARNING", logger="core.management.commands.warm_cache"
    ):
        _run("--langs", "en")
    assert "No data version is published" in caplog.text


def test_a_failing_read_is_named_and_fails_the_command():
    def broken():
        raise RuntimeError("Neon went away")

    with patch.object(warm_cache, "warm_jobs", return_value=[("home_stats", broken)]), pytest.raises(
        CommandError, match="home_stats.*'en'.*Neon went away"
    ):
        _run()


def test_a_second_run_finds_everything_already_warm():
    cache.set("data_version", "v1")
    _seen, jobs = _recording_jobs()
    with patch.object(warm_cache, "warm_jobs", return_value=jobs):
        first = _run()
        second = _run()
    assert "0 entries built" not in first
    assert "0 entries built" in second
