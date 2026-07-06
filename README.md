# Theoria

A movie analytics platform (mini IMDb + analytics) built to learn real Data Engineering:

```
TMDB API → S3 Data Lake (Bronze/Silver/Gold) → PostgreSQL warehouse (star schema) → Django UI
```

Full design rationale: [`docs/architecture.md`](docs/architecture.md). Task-by-task roadmap and
rules: [`CLAUDE.md`](CLAUDE.md). Running learning log: [`for_learning.md`](for_learning.md).

## Prerequisites

- Python 3.11+
- An AWS account with an S3 bucket (data lake)
- A PostgreSQL server (the warehouse) and a database created for it, e.g. `theoria`
- A [TMDB](https://www.themoviedb.org/settings/api) API key (v3 auth)

## 1. Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then fill in real values (API key, AWS creds, DATABASE_URL, ...)
python -c "import config"         # verify env is set up — fails loud listing every missing var
pytest                            # run the full test suite (no network/DB required — see below)
```

`config.py` is the single source of truth for all configuration — every script reads secrets and
paths through it, never hardcoded.

## 2. Create the warehouse schema

With `DATABASE_URL` pointing at an empty database, apply the DDL once (idempotent, safe to re-run):

```bash
psql "$DATABASE_URL_WITHOUT_DRIVER_PREFIX" -f warehouse/ddl/01_dimensions.sql
psql "$DATABASE_URL_WITHOUT_DRIVER_PREFIX" -f warehouse/ddl/02_facts.sql
psql "$DATABASE_URL_WITHOUT_DRIVER_PREFIX" -f warehouse/ddl/03_watermark.sql
```

(`DATABASE_URL` in `.env` uses the SQLAlchemy `postgresql+psycopg2://...` form; strip the
`+psycopg2` driver suffix when passing the URL to plain `psql`.)

## 3. Run the pipeline

The whole ETL — Bronze ingest → Silver transform → Gold aggregate → warehouse load — is chained
by a single script:

```bash
python -m scripts.run_pipeline --date 2026-07-06 --max-pages 5
```

This calls, in order, for the given `ingestion_date`:

1. **Bronze** — `ingest_genres`, `ingest_movies` (paginated, `max_pages` pages), then
   `ingest_movie_details` + `ingest_credits` for every movie ID discovered.
2. **Silver** — `transform_movies`, `transform_people`, `transform_genres`,
   `transform_credits_bridge`, followed by `run_silver_checks` (data quality gate; bad rows are
   quarantined to `data_quality/rejected/`, never dropped).
3. **Gold** — `build_gold_datasets` (genre metrics, decade stats, actor filmography, director
   ratings).
4. **Warehouse** — `load_dimensions` then `load_facts` (upsert via `ON CONFLICT DO UPDATE`,
   safe to re-run for the same date), followed by `run_warehouse_checks` (FK integrity +
   row-count sanity across every layer).

Every stage logs record counts and duration; the script ends with a one-line run summary.
Re-running for the same `--date` is safe — every stage is idempotent.

To load only *new* partitions found in S3 without re-specifying dates:

```bash
python -m etl.warehouse_loader.load_dimensions --incremental
python -m etl.warehouse_loader.load_facts --incremental
```

## 4. Run the Django UI

```bash
cd django_app
python manage.py runserver
```

Pages: `/` (home stats), `/movies/<id>/`, `/actors/<id>/`, `/directors/<id>/`, `/genres/<id>/`,
`/analytics/` (7-panel dashboard built on the Task 22 SQL queries in `warehouse/queries/`).

Django never writes to the warehouse — models are `managed = False` and a custom router
(`core/routers.py`) blocks migrations against it. Django's own auth/session tables live in a
separate local SQLite database.

## Tests

```bash
pytest
```

The full suite (159 tests) runs against mocked S3/TMDB/Postgres boundaries only — no network
access or live database is required. It covers ETL transforms, data quality checks, warehouse
loaders, and Django views.

## Project layout

See the tree in [`CLAUDE.md`](CLAUDE.md#project-structure) for the full annotated directory
layout, and [`docs/architecture.md`](docs/architecture.md) for the schema and data-flow design.
