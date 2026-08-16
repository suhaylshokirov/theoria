# Theoria

**A film analytics platform built on a real data engineering stack** — TMDB ingestion, a
three-layer S3 data lake, a dimensional PostgreSQL warehouse, and a Django front end that reads
it. Every layer is idempotent, quality-gated, and rebuildable from immutable raw data.

```
TMDB API  →  Bronze (raw JSON)  →  Silver (typed Parquet)  →  Gold (aggregates)  →  PostgreSQL  →  Django
                        S3 data lake, partitioned by ingestion_date                  star schema
```

<sub>Python 3.11 · PostgreSQL · AWS S3 · Django 5.1 · pandas · SQLAlchemy · pytest</sub>

---

## The warehouse, as it stands

| | |
|---|---|
| Films | **1,215** spanning 1930–2026 |
| Credited people | **122,685** — every person in every department, not just cast and directors |
| Credits | **237,454** across 13 departments and 858 distinct job titles |
| Collaboration edges | **193,064** repeat working relationships, derived in Gold |
| Film series | **358** |
| Warehouse tables | **9** — 5 dimensions, 3 facts, 1 operational |
| Test suite | **217** tests, no network or live database required |

The corpus is deliberate rather than incidental. TMDB's `movie/popular` endpoint returns whatever
is trending at call time, which produced a catalog that was 69% films from the 2020s. Switching
extraction to `discover/movie`, windowed one release year at a time, rebuilt it as an even
~200 films per decade — a time axis no downstream transform could have recovered had the
extraction step never collected it.

## Screenshots

| Home | Analytics |
|---|---|
| ![Home — catalog totals over a poster wall of the collection](docs/screenshots/homepage.png) | ![Analytics — total revenue by genre, charted and ranked](docs/screenshots/analytics.png) |

| Film catalog | Film detail |
|---|---|
| ![Film list — search and sort by newest, rating, revenue or title](docs/screenshots/movies.png) | ![Film detail — poster plate and the record list of measures and links](docs/screenshots/movie_detail.png) |

---

## Architecture

### The three lake layers

| Layer | Format | Contract |
|---|---|---|
| **Bronze** | Raw JSON, one file per API response | Immutable and append-only. Never edited, never overwritten. It is the system of record every other layer can be rebuilt from. |
| **Silver** | Typed Parquet | Owns correctness — flattening, type casting, deduplication at the true grain. Bad rows are **quarantined**, never silently dropped. |
| **Gold** | Aggregated Parquet | Pre-computed analytical datasets, including the collaboration graph loaded into the warehouse. |

Everything is partitioned by `ingestion_date=YYYY-MM-DD`. That single convention is what makes
re-runs idempotent and incremental loads possible: a partition is a unit of work that can be
reprocessed in isolation without touching anything else.

### The star schema

```
                  dim_genre        dim_date        dim_collection
                      │               │                  │
                      └───────┬───────┘                  │
                              ▼                          ▼
   dim_person ──► fact_credit ──► dim_movie ◄── fact_movie_metrics
        │                                              (rating, revenue,
        └──────► fact_collaboration                     budget, popularity)
                 (derived in Gold)
```

**Dimensions** — `dim_movie`, `dim_person`, `dim_genre`, `dim_collection`, `dim_date`
**Facts** — `fact_movie_metrics`, `fact_credit`, `fact_collaboration`
**Operational** — `etl_watermarks`

Two grain decisions carry most of the weight:

- **`fact_credit` is keyed `(movie_id, person_id, department, job)`** — the grain TMDB actually
  publishes. A director who also wrote and produced is three credits, not one. Getting this wrong
  earlier in the project silently destroyed the "Director" row for 65 of 99 films, because those
  people were usually credited as producers too and the dedup key couldn't tell the jobs apart.
- **`fact_movie_metrics` is keyed `(movie_id, date_id, genre_id)`**, so a multi-genre film repeats
  its movie-level measures once per genre. Every query that aggregates one must collapse it with
  `SELECT DISTINCT movie_id, …` first — a documented consequence of the schema, guarded in each of
  the analytics queries and views that touch it.

### Data quality as a gate, not a report

Two check suites run as part of the pipeline, both exiting non-zero on failure:

- **`data_quality/silver_checks.py`** — schema, null, uniqueness and range checks per Silver
  entity. Offending rows are tagged with a `rejection_reason` and written to
  `data_quality/rejected/`, so a failure is investigable rather than just counted.
- **`data_quality/warehouse_checks.py`** — foreign-key anti-joins across every fact→dimension
  relationship, plus row-count reconciliation Bronze → Silver → Gold → warehouse.

A lesson the project paid for: a check written by mirroring the transform's assumptions confirms
bugs instead of catching them. Check configs are now written from the source payload shape.

### Reading the warehouse from Django

Read-only access is enforced at three independent levels: a database router that refuses
migrations against the warehouse, `managed = False` on every model, and a separate SQLite database
for Django's own auth and session tables. The analytics dashboard executes the `.sql` files in
`warehouse/queries/` directly rather than re-expressing them through the ORM, so the queries stay
reviewable as SQL.

Full decision log, with the alternatives considered and the measurements behind each choice:
[`docs/architecture.md`](docs/architecture.md).

---

## Getting started

### Prerequisites

- Python 3.11+
- An S3 bucket for the data lake
- A PostgreSQL server with an empty database (e.g. `theoria`)
- A [TMDB API key](https://www.themoviedb.org/settings/api) (v3 auth)

### 1. Install and configure

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # fill in API key, AWS credentials, DATABASE_URL
python -c "import config"         # fails loud, listing every missing variable at once
pytest                            # full suite; no network or database needed
```

`config.py` is the only place that reads the environment. No script hardcodes a key, path or URL.

### 2. Create the warehouse schema

Apply the three bootstrap files in order against an empty database. Together they build the
**current** schema; all statements are `IF NOT EXISTS`, so re-running is safe.

```bash
psql "$DATABASE_URL_WITHOUT_DRIVER_PREFIX" -f warehouse/ddl/01_dimensions.sql
psql "$DATABASE_URL_WITHOUT_DRIVER_PREFIX" -f warehouse/ddl/02_facts.sql
psql "$DATABASE_URL_WITHOUT_DRIVER_PREFIX" -f warehouse/ddl/03_watermark.sql
```

> **Do not run `04`–`12` on a fresh database.** Those are the historical migrations that brought an
> already-live warehouse to this shape, and they are only correct applied in order to a database
> that predates them — `11_drop_legacy_person_tables.sql` drops tables `01` no longer creates. Once
> a migration drops something, "run every DDL file in order" stops being the same instruction as
> "build the current schema". Use `04`–`12` only to migrate an existing Theoria warehouse.

`slug` columns are declared empty by the DDL and populated by `load_dimensions()`.

(`DATABASE_URL` uses SQLAlchemy's `postgresql+psycopg2://…` form; strip the `+psycopg2` suffix when
passing it to plain `psql`.)

### 3. Run the pipeline

```bash
python -m scripts.run_pipeline --source discover        # multi-decade catalog (recommended)
python -m scripts.run_pipeline --date 2026-07-06 --max-pages 5   # or today's popular list
```

For a given `ingestion_date`, this runs:

1. **Bronze** — `ingest_genres`, then `ingest_discover` (year-partitioned) or `ingest_movies`
   (paginated), then `ingest_movie_details` and `ingest_credits` for every discovered film. Failures
   are logged per film ID and returned for retry; completed work is never discarded.
2. **Silver** — `transform_movies`, `transform_people`, `transform_genres`,
   `transform_credits_bridge`, then `run_silver_checks` as a gate.
3. **Gold** — `build_gold_datasets` (genre metrics, decade stats, filmography, director ratings,
   collaboration edges).
4. **Warehouse** — `load_dimensions`, `load_facts`, `load_gold`, then `run_warehouse_checks`.

Every stage logs record counts and duration, and the run ends with a one-line summary. Re-running
the same date is safe: loads upsert via `ON CONFLICT DO UPDATE`.

To load only partitions newer than the recorded watermark:

```bash
python -m etl.warehouse_loader.load_dimensions --incremental
python -m etl.warehouse_loader.load_facts --incremental
```

The `DISCOVER_*` variables in `.env` are a **definition of the dataset**, not tuning knobs —
lowering `DISCOVER_MIN_VOTES` doesn't make the pipeline slower, it makes the warehouse describe a
different population.

### 4. Run the site

```bash
cd django_app && python manage.py runserver
```

| Route | What it serves |
|---|---|
| `/` | Catalog overview and the contact-sheet hero |
| `/movies/` · `/movies/<slug>/` | Search, sort and paginate the catalog; per-film detail with full cast and crew |
| `/people/` · `/people/<slug>/` | Everyone holding any credit; per-person filmography, credits by department, and repeat collaborators |
| `/actors/` · `/directors/` | Scopes of `/people/`, filtered by the credits someone holds — not separate tables |
| `/analytics/` | Dashboard panels driven by the SQL in `warehouse/queries/` |

Films and people are addressed by slug (`/people/tom-hanks/`), never by warehouse surrogate key.
Slugs are recomputed for the whole table on every load, with collisions numbered in ascending id
order — which is what makes them stable across re-runs rather than reassigned. Legacy
`/actors/<slug>/` and `/directors/<slug>/` URLs 301 to the unified person page.

---

## Testing

```bash
pytest
```

217 tests covering the ETL transforms, data quality checks, warehouse loaders and Django views.
The suite mocks S3, TMDB and PostgreSQL **at the boundary** — no live infrastructure, no fixtures
loaded into a real database, no network. Django views are driven through their real URLs with the
managers patched, so routing and template rendering are genuinely exercised.

## Project layout

```
etl/
  tmdb_client.py          retrying API wrapper
  s3_utils.py             the one place the S3 key convention is defined
  incremental.py          watermarks and partition discovery
  bronze/ silver/ gold/   one module per entity, per layer
  warehouse_loader/       upsert loaders for dimensions, facts and Gold
data_quality/             Silver and warehouse check suites; rejected/ holds quarantined rows
warehouse/
  db.py                   engine and session management
  ddl/                    01–03 bootstrap, 04–12 migrations
  queries/                analytics SQL — never inline in application code
django_app/               core (settings, router) · movies · analytics
scripts/run_pipeline.py   end-to-end orchestration
tests/                    ETL, data quality and view tests
docs/architecture.md      design decisions and their evidence
```

## Scope and non-goals

Theoria runs on one machine on purpose. There is no Spark, Kafka, Airflow, Snowflake, Lambda,
Terraform or Kubernetes, and adding them is explicitly out of scope. The goal is the *shape* of a
production analytics stack — layered storage, dimensional modelling, idempotent loads, quality
gates, incremental processing — at a scale where every stage can be read, run and debugged end to
end. The engineering decisions are documented as if the infrastructure were there; the
infrastructure isn't, and that is the trade being made.
