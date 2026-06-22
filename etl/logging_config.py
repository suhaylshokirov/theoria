"""Shared logging setup for all ETL scripts.

Call setup_logging(script_name) once at the top of every __main__ block.
It configures two handlers:

  Console — INFO and above, human-readable with timestamps.
  File    — DEBUG and above, rotating (5 MB × 3 backups) in config.LOGS_DIR.

Why two handlers? The console gives you a live view while a script runs;
the file keeps DEBUG-level detail (per-movie writes etc.) for post-mortem
inspection without flooding the terminal.

Usage:
    from etl.logging_config import setup_logging
    setup_logging("ingest_genres")
"""

from __future__ import annotations

import logging
import logging.handlers

import config

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per log file
_BACKUP_COUNT = 3


def setup_logging(script_name: str, level: int = logging.INFO) -> None:
    """Configure root logger with a console handler and a rotating file handler.

    Creates config.LOGS_DIR if it does not exist.

    Args:
        script_name: Used as the log filename (e.g. "ingest_genres" →
                     logs/ingest_genres.log). Should match the module name.
        level: Minimum level for the console handler. File handler always
               captures DEBUG and above so full detail is always on disk.
    """
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)

    log_file = config.LOGS_DIR / f"{script_name}.log"
    rotating = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    rotating.setLevel(logging.DEBUG)
    rotating.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(rotating)
