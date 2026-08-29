"""Tests for scripts/sync_warehouse_from_neon.py.

Only the endpoint guard is unit-tested: it is the one place a bug is dangerous
(truncating the wrong database). The COPY streaming is covered by running the
script against real Neon + local Postgres, not here.
"""

from __future__ import annotations

import pytest

from scripts.sync_warehouse_from_neon import _guard, _host_of, _to_libpq

REMOTE = "postgresql+psycopg2://u:p@ep-x.eu-central-1.aws.neon.tech/neondb?sslmode=require"
LOCAL = "postgresql+psycopg2://postgres:pw@localhost:5432/theoria"
LOCAL_IP = "postgresql://postgres:pw@127.0.0.1:5432/theoria"


def test_to_libpq_strips_sqlalchemy_driver():
    assert _to_libpq(REMOTE).startswith("postgresql://")
    assert "+psycopg2" not in _to_libpq(REMOTE)


def test_host_of_extracts_hostname():
    assert _host_of(REMOTE) == "ep-x.eu-central-1.aws.neon.tech"
    assert _host_of(LOCAL) == "localhost"
    assert _host_of(LOCAL_IP) == "127.0.0.1"


def test_guard_accepts_remote_source_and_local_target():
    _guard(REMOTE, LOCAL)
    _guard(REMOTE, LOCAL_IP)


def test_guard_rejects_empty_source():
    with pytest.raises(RuntimeError, match="NEON_DATABASE_URL is not set"):
        _guard("", LOCAL)


def test_guard_rejects_local_source():
    with pytest.raises(RuntimeError, match="local host"):
        _guard(LOCAL, LOCAL)


def test_guard_rejects_non_local_target():
    with pytest.raises(RuntimeError, match="non-local host"):
        _guard(REMOTE, REMOTE)
