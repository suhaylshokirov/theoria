"""Central configuration for Theoria.

Single source of truth for every environment-derived value in the project.
No other module should read os.environ directly or hardcode keys/paths/URLs.

Behaviour:
- Loads variables from a local .env file (via python-dotenv) if present.
- Fails LOUD if a required variable is missing, listing every missing name at
  once (so you fix them in one pass, not one by one).

A variable is required by a *role*, not by the project as a whole, because
three different processes import this file and they need different things:

    core   every process        DATABASE_URL
    web    the Django site      DJANGO_SECRET_KEY
    etl    the pipeline         TMDB_API_KEY, AWS_*, S3_BUCKET

Only the core set is enforced at import. The other two are enforced by
require_web() / require_etl(), called at the point that role actually starts:
Django's settings module, and the pipeline orchestrators. Nothing gets
quieter -- a missing key still stops the process before any work happens --
but the roles stop demanding each other's secrets. That is what lets the
hosted web function boot without holding S3 write credentials it never uses,
and lets the nightly job run without a Django secret it never reads.

Verify your setup with:
    python -c "import config"                      # core
    python -c "import config; config.require_web()"
    python -c "import config; config.require_etl()"
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
# Missing names are collected per role rather than raised on sight, so one run
# reports every gap at once instead of one per fix-and-rerun cycle.
_missing: dict[str, list[str]] = {"core": [], "web": [], "etl": []}


def _require(name: str, role: str = "core") -> str:
    """Return a required env var, or record it as missing for `role`."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        _missing[role].append(name)
        return ""
    return value


def _optional(name: str, default: str) -> str:
    """Return an env var, falling back to a sensible default."""
    value = os.getenv(name)
    return value if value not in (None, "") else default


# --- TMDB ------------------------------------------------------------------
TMDB_API_KEY = _require("TMDB_API_KEY", role="etl")
TMDB_BASE_URL = _optional("TMDB_BASE_URL", "https://api.themoviedb.org/3")
TMDB_IMAGE_BASE_URL = _optional("TMDB_IMAGE_BASE_URL", "https://image.tmdb.org/t/p")

# --- AWS / S3 --------------------------------------------------------------
AWS_ACCESS_KEY_ID = _require("AWS_ACCESS_KEY_ID", role="etl")
AWS_SECRET_ACCESS_KEY = _require("AWS_SECRET_ACCESS_KEY", role="etl")
AWS_REGION = _optional("AWS_REGION", "eu-central-1")
S3_BUCKET = _require("S3_BUCKET", role="etl")

# --- PostgreSQL warehouse --------------------------------------------------
# DATABASE_URL is the warehouse this process reads and writes:
#   * locally, the fast on-machine replica Django serves from;
#   * in the nightly GitHub Actions job, the Neon instance (the source of truth).
DATABASE_URL = _require("DATABASE_URL")

# NEON_DATABASE_URL is only set locally, and only used by
# scripts/sync_warehouse_from_neon.py to pull Neon -> the local replica. The
# cloud job never sets it (it writes Neon directly via DATABASE_URL), so it is
# optional, not required.
NEON_DATABASE_URL = _optional("NEON_DATABASE_URL", "")

# --- Ingestion tuning ------------------------------------------------------
MAX_PAGES = int(_optional("MAX_PAGES", "5"))

# Corpus design for the `discover/movie` source (etl/bronze/ingest_discover.py).
# Unlike MAX_PAGES, which just caps the popular-list crawl, these define *which
# films exist* in the warehouse: the most-voted titles of each year in the range
# that clear a vote-count floor. Widening the years deepens the historical
# corpus; raising the floor trades breadth for recognisability.
DISCOVER_START_YEAR = int(_optional("DISCOVER_START_YEAR", "1970"))
DISCOVER_END_YEAR = int(_optional("DISCOVER_END_YEAR", "2026"))
DISCOVER_PAGES_PER_YEAR = int(_optional("DISCOVER_PAGES_PER_YEAR", "1"))
DISCOVER_MIN_VOTES = int(_optional("DISCOVER_MIN_VOTES", "300"))

# --- IMDb datasets -----------------------------------------------------------
# Public bulk export, refreshed daily by IMDb — no auth, no key, no quota.
# https://datasets.imdbws.com/title.ratings.tsv.gz (tconst/averageRating/numVotes).
IMDB_RATINGS_URL = _optional("IMDB_RATINGS_URL", "https://datasets.imdbws.com/title.ratings.tsv.gz")

# --- Django ----------------------------------------------------------------
DJANGO_SECRET_KEY = _require("DJANGO_SECRET_KEY", role="web")
DJANGO_DEBUG = _optional("DJANGO_DEBUG", "True").lower() in ("1", "true", "yes")


# --- Fail loud -------------------------------------------------------------
def _check(role: str, needed_by: str) -> None:
    """Raise if any variable required by `role` is missing."""
    if _missing[role]:
        raise ConfigError(
            f"Missing environment variables required by {needed_by}: "
            + ", ".join(sorted(_missing[role]))
            + ".\nCopy .env.example to .env and fill them in "
            "(see config.py for the full list)."
        )


def require_web() -> None:
    """Assert the Django site has everything it needs. Called by settings.py."""
    _check("web", "the Django site")


def require_etl() -> None:
    """Assert the pipeline has everything it needs.

    Called by the orchestrators (scripts/run_pipeline.py, scripts/run_refresh.py)
    and, as a backstop, at the point a TMDB or S3 client is actually built --
    so an ETL module run directly still fails before its first request, not
    midway through one.
    """
    _check("etl", "the ETL pipeline")


# The core set is the only one every process needs, so it is the only one
# enforced on import.
_check("core", "every process")
