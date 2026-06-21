"""Central configuration for Theoria.

Single source of truth for every environment-derived value in the project.
No other module should read os.environ directly or hardcode keys/paths/URLs.

Behaviour:
- Loads variables from a local .env file (via python-dotenv) if present.
- Fails LOUD at import time if any required variable is missing, listing
  every missing name at once (so you fix them in one pass, not one by one).

Verify your setup with:  python -c "import config"
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --- Paths -----------------------------------------------------------------
# PROJECT_ROOT is derived from this file's location, never hardcoded.
PROJECT_ROOT = Path(__file__).resolve().parent
LOGS_DIR = PROJECT_ROOT / "logs"
REJECTED_DIR = PROJECT_ROOT / "data_quality" / "rejected"

# Load .env sitting next to this file. Real env vars (e.g. in CI) win over
# .env values only if override=False, which is the default.
load_dotenv(PROJECT_ROOT / ".env")


class ConfigError(RuntimeError):
    """Raised when configuration is incomplete or invalid."""


# --- Helpers ---------------------------------------------------------------
_missing: list[str] = []


def _require(name: str) -> str:
    """Return a required env var, or record it as missing (fail loud later)."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        _missing.append(name)
        return ""
    return value


def _optional(name: str, default: str) -> str:
    """Return an env var, falling back to a sensible default."""
    value = os.getenv(name)
    return value if value not in (None, "") else default


# --- TMDB ------------------------------------------------------------------
TMDB_API_KEY = _require("TMDB_API_KEY")
TMDB_BASE_URL = _optional("TMDB_BASE_URL", "https://api.themoviedb.org/3")

# --- AWS / S3 --------------------------------------------------------------
AWS_ACCESS_KEY_ID = _require("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = _require("AWS_SECRET_ACCESS_KEY")
AWS_REGION = _optional("AWS_REGION", "eu-central-1")
S3_BUCKET = _optional("S3_BUCKET", "theoria-datalake")

# --- PostgreSQL warehouse --------------------------------------------------
DATABASE_URL = _require("DATABASE_URL")

# --- Ingestion tuning ------------------------------------------------------
MAX_PAGES = int(_optional("MAX_PAGES", "5"))

# --- Django ----------------------------------------------------------------
DJANGO_SECRET_KEY = _require("DJANGO_SECRET_KEY")
DJANGO_DEBUG = _optional("DJANGO_DEBUG", "True").lower() in ("1", "true", "yes")


# --- Fail loud -------------------------------------------------------------
if _missing:
    raise ConfigError(
        "Missing required environment variables: "
        + ", ".join(sorted(_missing))
        + ".\nCopy .env.example to .env and fill them in "
        "(see config.py for the full list)."
    )
