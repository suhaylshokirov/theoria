"""Tests for config.py's role-scoped required variables.

The roles exist so a process can start with only the variables it actually
uses: the hosted Django site boots without S3 write credentials it never
touches, and the nightly pipeline runs without a Django secret it never reads.
What must NOT change is the "fail loud" guarantee — a variable a process does
need still stops it before any work happens, listing every missing name at
once. These tests pin both halves of that, because loosening the first is
exactly how you accidentally lose the second.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest

import config

CORE = {"DATABASE_URL": "postgresql://u:p@localhost:5432/theoria"}
WEB = {"DJANGO_SECRET_KEY": "not-a-real-key"}
ETL = {
    "TMDB_API_KEY": "key",
    "AWS_ACCESS_KEY_ID": "id",
    "AWS_SECRET_ACCESS_KEY": "secret",
    "S3_BUCKET": "bucket",
}


def _load(env: dict[str, str]):
    """Re-import config against exactly `env`, ignoring any real .env file."""
    with patch.dict(os.environ, env, clear=True), patch("dotenv.load_dotenv"):
        return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_real_config():
    """Put the module back on the real environment after each test."""
    yield
    importlib.reload(config)


def test_web_role_boots_without_etl_credentials():
    """The hosted case: a web-only environment must import and pass require_web."""
    cfg = _load({**CORE, **WEB})

    cfg.require_web()  # must not raise
    assert cfg.DATABASE_URL == CORE["DATABASE_URL"]
    assert cfg.TMDB_API_KEY == ""


def test_etl_role_boots_without_django_secret():
    """The pipeline case: no Django secret is needed to run a refresh."""
    cfg = _load({**CORE, **ETL})

    cfg.require_etl()  # must not raise
    assert cfg.S3_BUCKET == "bucket"


def test_require_etl_names_every_missing_variable_at_once():
    cfg = _load({**CORE, **WEB})

    with pytest.raises(cfg.ConfigError) as exc:
        cfg.require_etl()

    message = str(exc.value)
    for name in ETL:
        assert name in message


def test_require_web_raises_when_secret_key_is_missing():
    cfg = _load({**CORE, **ETL})

    with pytest.raises(cfg.ConfigError, match="DJANGO_SECRET_KEY"):
        cfg.require_web()


def test_core_variable_is_still_enforced_at_import():
    """DATABASE_URL belongs to no role in particular, so nothing defers it.

    Matched on RuntimeError rather than config.ConfigError: the raise happens
    *during* the reload, which rebinds ConfigError to a new class object, so
    the reference captured before the reload would never match it.
    """
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _load({**WEB, **ETL})
