"""Task 114: the pipelines publish the cache data version."""

from __future__ import annotations

import datetime as dt
import pickle
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache.backends.redis import RedisSerializer
from redis.exceptions import ConnectionError as RedisConnectionError

import config
from etl import data_version
from scripts import run_pipeline, run_refresh

# core.cache_backend lives in the Django app; this test pins its key contract.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "django_app"))

DATE = dt.date(2026, 10, 7)


# --- the value ----------------------------------------------------------------


def test_version_is_the_date_plus_publish_time():
    assert data_version.make_version(DATE, now=1760000000.9) == "2026-10-07.1760000000"


def test_two_loads_on_one_date_get_different_versions():
    # The nightly (03:12) and the Monday discovery (04:20) share an ingestion_date.
    assert data_version.make_version(DATE, now=1760000000) != data_version.make_version(
        DATE, now=1760003600
    )


def test_key_is_the_one_the_site_reads():
    from core.cache_backend import make_cache_key

    assert make_cache_key("data_version", "theoria", 1) == data_version.VERSION_KEY


def test_the_published_bytes_are_what_djangos_redis_cache_reads_back():
    # Django unpickles every Redis value except plain integers, so a raw string
    # would raise on the site and leave it in short-TTL mode for good.
    client = MagicMock()
    with patch.object(config, "REDIS_URL", "rediss://x"), patch.object(
        data_version, "_client", return_value=client
    ):
        version = data_version.publish_data_version(DATE)
    key, raw = client.set.call_args.args
    assert key == "theoria:data_version"
    assert RedisSerializer().loads(raw) == version
    assert client.set.call_args.kwargs == {}  # no expiry


# --- never fails a run ------------------------------------------------------------


def test_without_redis_url_nothing_is_published(caplog):
    with patch.object(config, "REDIS_URL", ""), patch.object(data_version, "_client") as client, caplog.at_level(
        "INFO", logger="etl.data_version"
    ):
        assert data_version.publish_data_version(DATE) is None
    client.assert_not_called()
    assert "no REDIS_URL" in caplog.text


def test_a_redis_error_is_swallowed_and_logged(caplog):
    client = MagicMock()
    client.set.side_effect = RedisConnectionError("Connection refused")
    with patch.object(config, "REDIS_URL", "rediss://x"), patch.object(
        data_version, "_client", return_value=client
    ), caplog.at_level("WARNING", logger="etl.data_version"):
        assert data_version.publish_data_version(DATE) is None
    assert "not published" in caplog.text
    assert "Connection refused" in caplog.text


def test_a_malformed_url_is_swallowed_too(caplog):
    with patch.object(config, "REDIS_URL", "not a url"), caplog.at_level("WARNING", logger="etl.data_version"):
        assert data_version.publish_data_version(DATE) is None
    assert "not published" in caplog.text


def test_the_published_value_is_logged(caplog):
    with patch.object(config, "REDIS_URL", "rediss://x"), patch.object(
        data_version, "_client", return_value=MagicMock()
    ), caplog.at_level("INFO", logger="etl.data_version"):
        version = data_version.publish_data_version(DATE)
    assert version in caplog.text


def test_read_returns_what_was_published():
    client = MagicMock()
    client.get.return_value = pickle.dumps("2026-10-07.1760000000")
    with patch.object(config, "REDIS_URL", "rediss://x"), patch.object(
        data_version, "_client", return_value=client
    ):
        assert data_version.read_data_version() == "2026-10-07.1760000000"
    client.get.return_value = None
    with patch.object(config, "REDIS_URL", "rediss://x"), patch.object(
        data_version, "_client", return_value=client
    ):
        assert data_version.read_data_version() is None


# --- orchestrators --------------------------------------------------------------------


def _patched_stages(module):
    """Replace every pipeline stage in `module` with a mock on one parent.

    Returns (parent, patches). Stages are the functions the module imported
    from etl/ and data_quality/ plus its private helpers; the parent records
    their call order.
    """
    parent = MagicMock()
    patches = []
    for name, obj in list(vars(module).items()):
        owner = getattr(obj, "__module__", "") or ""
        if not callable(obj) or isinstance(obj, type):
            continue
        if owner.startswith(("etl.", "data_quality.")) or name.startswith("_extract_") or name == "_warehouse_episode_counts":
            mock = getattr(parent, name)
            # Stages that return (succeeded, failed) pairs get a 2-tuple.
            mock.return_value = [] if name.startswith("run_") and name.endswith("_checks") else ([], [])
            patches.append(patch.object(module, name, mock))
    return parent, patches


def _run(module, runner):
    parent, patches = _patched_stages(module)
    for p in patches:
        p.start()
    try:
        runner(parent)
    finally:
        for p in patches:
            p.stop()
    return parent


@pytest.mark.parametrize(
    "module,call",
    [
        (run_refresh, lambda: run_refresh.run_refresh(ingestion_date=DATE)),
        (run_pipeline, lambda: run_pipeline.run_pipeline(ingestion_date=DATE)),
    ],
    ids=["refresh", "pipeline"],
)
def test_publish_comes_right_after_both_loaders(module, call):
    with patch.object(config, "require_etl"):
        parent = _run(module, lambda _p: call())
    names = [c[0] for c in parent.mock_calls if "." not in c[0]]
    assert "publish_data_version" in names
    assert names.index("load_dimensions") < names.index("load_facts") < names.index("publish_data_version")
    parent.publish_data_version.assert_called_once_with(DATE)


@pytest.mark.parametrize("loader", ["load_dimensions", "load_facts"])
@pytest.mark.parametrize(
    "module,call",
    [
        (run_refresh, lambda: run_refresh.run_refresh(ingestion_date=DATE)),
        (run_pipeline, lambda: run_pipeline.run_pipeline(ingestion_date=DATE)),
    ],
    ids=["refresh", "pipeline"],
)
def test_nothing_is_published_when_a_loader_raises(module, call, loader):
    def runner(parent):
        getattr(parent, loader).side_effect = RuntimeError("Neon went away")
        with pytest.raises(RuntimeError):
            call()

    with patch.object(config, "require_etl"):
        parent = _run(module, runner)
    parent.publish_data_version.assert_not_called()


def test_failing_warehouse_checks_do_not_block_the_publish():
    def runner(parent):
        failed = MagicMock(passed=False)
        parent.run_warehouse_checks.return_value = [failed]
        parent.run_silver_checks.return_value = [failed]
        run_refresh.run_refresh(ingestion_date=DATE)

    with patch.object(config, "require_etl"):
        parent = _run(run_refresh, runner)
    parent.publish_data_version.assert_called_once_with(DATE)
