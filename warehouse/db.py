"""SQLAlchemy engine and session factory for the Theoria warehouse.

Usage:
    from warehouse.db import get_engine, get_session

    with get_session() as session:
        session.execute(text("SELECT 1"))

All connection parameters come from config.DATABASE_URL — never hardcoded here.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import config

_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None


def get_engine() -> Engine:
    """Return the shared SQLAlchemy engine, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            config.DATABASE_URL,
            # Keep a small pool — this is a single-machine learning project.
            pool_size=5,
            max_overflow=2,
            pool_pre_ping=True,  # drop stale connections before use
        )
    return _engine


def _get_session_factory() -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context-manager that yields a transactional Session and auto-commits.

    Rolls back on exception; always closes the session on exit.

    Example:
        with get_session() as s:
            s.execute(text("SELECT 1"))
    """
    factory = _get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection() -> bool:
    """Return True if the database is reachable, False otherwise."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def reset_engine() -> None:
    """Dispose the engine and clear the cache (useful in tests)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
