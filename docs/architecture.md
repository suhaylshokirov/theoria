# Theoria — Architecture

This document explains *why* Theoria is built the way it is, written for someone evaluating the
design (e.g. an interviewer) rather than someone modifying the code line-by-line. For the
task-by-task build log, see `CLAUDE.md`; for line-level teaching notes, see `for_learning.md`.

## 1. Goal

Theoria is a single-machine data engineering project that mimics a real analytics stack, end to
end, without any of the distributed-systems infrastructure (Spark, Kafka, Kubernetes, Terraform)
that a production version would eventually need. The constraint is deliberate: the point is to
practice the *shape* of a DE pipeline — bronze/silver/gold layering, a dimensional warehouse,
idempotent loads, data quality gates — on a scale where every stage can be read, run, and debugged
by one person on a laptop.

## 2. End-to-end data flow

```
TMDB API
   │  (requests, retry/backoff)
   ▼
Bronze (S3, raw JSON, immutable, append-only)
   │  ingestion_date=YYYY-MM-DD partitions
   ▼
Silver (S3, cleaned & typed Parquet)
   │  flatten, dedupe, cast types, quarantine bad rows
   ▼
Gold (S3, pre-aggregated Parquet)
   │  genre metrics, decade stats, filmography, director ratings
   ▼
PostgreSQL warehouse (star schema)
   │  dimensions + facts, upserted, watermark-tracked
   ▼
Django UI (read-only)
   movie/actor/director/genre pages + analytics dashboard
```

Each arrow is a separate, independently testable stage with its own module
(`etl/bronze/*`, `etl/silver/*`, `etl/gold/*`, `etl/warehouse_loader/*`). `scripts/run_pipeline.py`
sequences all of them in-process for one `ingestion_date` — but every stage function can also be
imported and run on its own, which is what the unit tests do (mocking S3/TMDB/Postgres at the
boundary rather than mocking business logic).

### Why S3, and why three layers instead of loading straight into Postgres

- **Bronze is the immutable source of truth.** Raw API responses are never edited in place. If a
  transform bug is discovered later, Silver/Gold can be rebuilt from Bronze without re-hitting the
  TMDB API. This is the standard "raw zone" pattern in a data lake.
- **Silver is where correctness lives.** Flattening nested JSON, casting types, deduplication, and
  the data-quality gate (`data_quality/silver_checks.py`) all happen here — once, in one place —
  rather than being re-implemented ad hoc by every downstream consumer.
- **Gold exists for read patterns that don't map cleanly onto the star schema**, or that are
  expensive to recompute per-request (e.g. actor filmography counts). In this project the
  warehouse ends up being the primary read path for Django (Gold's Parquet output isn't currently
  loaded into Postgres — see §5), so Gold mainly demonstrates the aggregation step you'd wire into
  a warehouse load in a larger system.

### Why partitioning by `ingestion_date`

Every layer's S3 key is `s3://<bucket>/<layer>/<entity>/ingestion_date=YYYY-MM-DD/<file>`. This
gives:
- **Idempotent re-runs** — re-running a stage for a date overwrites only that date's partition.
- **Incremental processing** — `etl/incremental.py` lists partitions present in S3 and compares
  them against a stored watermark to find only the *new* ones (see §4).
- **Natural backfills** — if TMDB data for a past date needs reprocessing, only that partition is
  touched.

## 3. Warehouse schema (star schema)

```
                     ┌───────────┐
                     │ dim_date  │
                     └─────┬─────┘
                           │
┌───────────┐        ┌─────┴──────────────┐        ┌────────────┐
│ dim_genre │───────▶│ fact_movie_metrics │◀───────│ dim_movie  │
└───────────┘        └────────────────────┘        └─────┬──────┘
                                                          │
                          ┌────────────┐                  │
┌────────────┐            │ fact_cast  │◀─────────────────┤
│ dim_actor  │───────────▶│            │                  │
└────────────┘            └────────────┘                  │
                                                          │
                          ┌────────────┐                  │
┌────────────┐            │ fact_crew  │◀─────────────────┘
│ dim_director│──────────▶│            │
└────────────┘            └────────────┘
```

**Dimensions** (`warehouse/ddl/01_dimensions.sql`): `dim_movie`, `dim_actor`, `dim_director`,
`dim_genre`, `dim_date`. All use a natural TMDB integer ID as primary key, except `dim_date`,
which uses a generated `YYYYMMDD` surrogate key and is populated as a full calendar table
(1900–2035 by default) independent of any Silver data.

**Facts** (`warehouse/ddl/02_facts.sql`):
- `fact_movie_metrics(movie_id, date_id, genre_id, rating, vote_count, revenue, budget,
  popularity, ingestion_date)` — one row per `(movie, genre)` pair, because a movie can belong to
  multiple genres and TMDB doesn't give per-genre metrics, so the same movie-level metrics are
  repeated once per genre. **This has one consequence every analytics query must account for**:
  aggregating `rating`/`revenue`/`popularity` directly would double-count multi-genre movies. Every
  query in `warehouse/queries/` that touches these columns first collapses to
  `SELECT DISTINCT movie_id, ...` in a CTE before aggregating.
- `fact_cast(movie_id, actor_id, role, ordering, ingestion_date)` — one row per credited actor per
  movie.
- `fact_crew(movie_id, director_id, ingestion_date)` — one row per credited director per movie
  (restricted to director credits, mirroring `dim_director`).

All fact tables carry named foreign keys to every dimension they reference, and an index on each
FK column (PostgreSQL does not auto-index FKs). All also carry an `ingestion_date` column purely
for audit/traceability — it is *not* part of a uniqueness constraint, because legitimate data has
multiple rows per `(movie_id, ingestion_date)` (one per genre, one per actor, or one per director).
Duplicate-guarding instead comes from the composite primary key plus `ON CONFLICT DO UPDATE`
upserts in the loaders — idempotent by construction, not by an extra constraint.

### Resolved: `fact_cast`/`fact_crew` replace the earlier `fact_casting` cross-join

TMDB's credits endpoint returns cast and crew as two separate flat lists per movie — it never
pairs a given actor with "their" director. An earlier version of this schema modeled casting as a
single `fact_casting(movie_id, actor_id, director_id, role, ordering, ingestion_date)` table with
both `actor_id` and `director_id` non-null, populated by cross-joining, per movie, every credited
actor with every credited director (crew rows where `job == "Director"`).

That coupling meant a movie with **no** credited director (TMDB metadata gaps, or a
documentary/short with no "Director" job tag) produced **zero** casting rows for *all* of its
actors — losing its entire cast, not just its director. In the sample data this affected roughly
46% of candidate rows.

The fix (Task 35 / Workstream A) splits the actor and director sides into two independent fact
tables: `fact_cast` and `fact_crew`. Neither loader (`etl/warehouse_loader/load_facts.py`,
`_build_cast_rows`/`_build_crew_rows`) looks at the other dimension at all, so a movie's cast no
longer depends on whether it has a resolvable director, and vice versa. `fact_crew` currently
models director credits only (mirroring `dim_director`, which itself only contains people credited
as director) — modeling other crew roles would need a new person-role dimension, out of scope for
this fix.

## 4. Idempotency & incremental loads

Every ETL stage is idempotent by design:
- **Bronze**: writing the same `ingestion_date` twice overwrites the same S3 keys with the same
  content (TMDB data for a given day is stable).
- **Silver/Gold**: fully rebuilt from their source layer each run — no incremental merge logic,
  since Parquet files are cheap to regenerate.
- **Warehouse**: every dimension and fact load is an `INSERT ... ON CONFLICT (pk) DO UPDATE`
  upsert (`etl/warehouse_loader/common.py`), so re-running a load for the same partition changes
  nothing.

`etl/incremental.py` adds a small watermark mechanism on top of this idempotency, purely as an
optimization — to avoid *re-processing* partitions that are already loaded, not because
re-processing them would be unsafe:
- `etl_watermarks(loader_name PK, last_ingestion_date, updated_at)` stores, per loader, the last
  successfully processed date.
- `pending_partitions()` lists S3 partitions newer than the watermark.
- Both loaders expose a `*_incremental()` entry point that processes pending partitions in
  ascending order and **advances the watermark after each individual date**, so a failure
  mid-run leaves the watermark at the last fully-completed partition rather than losing all
  progress in the run.

## 5. Data quality: quarantine, never drop

Two quality gates run at different layers, both following the same pattern: check → tag failing
rows with a `rejection_reason` → write them to `data_quality/rejected/<entity>_rejected_<date>.parquet`
→ continue with the clean rows. Bad data is never silently discarded, which matters for debugging
("why is this movie missing?" should always be answerable by looking in `rejected/`, not by
guessing).

- **`data_quality/silver_checks.py`** — runs after the Silver transforms: schema (expected
  columns present), nulls (required columns), duplicate primary keys, and range checks (e.g.
  `vote_average` between 0–10, counts/popularity non-negative).
- **`data_quality/warehouse_checks.py`** — runs after the warehouse load: FK integrity
  (anti-join `LEFT JOIN ... WHERE dim.pk IS NULL`, a defense-in-depth check since the FK
  constraints should already prevent this) and row-count sanity across every layer
  (Bronze ≥ Silver ≥ nothing-missing-in-Gold ≥ facts-exist-for-this-partition).

Both produce a flat list of `CheckResult(entity, check, passed, bad_count, message)` and a
single pass/fail CLI summary, and both exit non-zero on any failure so they can gate a pipeline
run.

## 6. Django UI: read-only by construction

Django never writes to the warehouse. This is enforced at three levels, not just by convention:

1. **`core/routers.py` (`WarehouseRouter`)** refuses `allow_migrate` on the `warehouse` database
   in both directions — Django's own migrations can't touch it, and warehouse-mapped models can't
   accidentally get a migration generated against it.
2. **`movies/models.py`** marks every model `managed = False` — Django's ORM will never try to
   `CREATE`/`ALTER`/`DROP` these tables.
3. **Two separate databases** — `default` (local SQLite) holds Django's own auth/session/admin
   tables; `warehouse` (Postgres) holds only the star schema. Views explicitly call
   `.using("warehouse")`.

All three composite-PK fact tables (`fact_movie_metrics`, `fact_cast`, `fact_crew`) don't fit
Django's one-primary-key-per-model assumption. Each model marks its `movie` FK as
`primary_key=True` purely to satisfy that constraint — the real uniqueness lives only in the
database's actual composite PK, and the resulting `fields.W342` warning is intentionally silenced
in `settings.py` with a comment explaining why, rather than worked around with a fake single-column
surrogate key that doesn't exist in the table.

The **analytics dashboard** (`analytics/views.py`) takes a different approach from the `movies`
app: instead of expressing the Task 22 SQL queries through the ORM, it reads the `.sql` files in
`warehouse/queries/` directly and executes them via a raw cursor. This avoids maintaining the same
logic twice (once in `.sql`, once as ORM query-building) for queries — like the self-join for actor
collaboration frequency — that are naturally SQL-shaped.

## 7. Testing philosophy

The full suite (182 tests, `pytest`) never touches a real network, S3 bucket, or Postgres
instance. Every ETL/loader test mocks the boundary (the `boto3` client, the `requests` session, the
SQLAlchemy session) and asserts on the transformation logic itself. Django view tests construct
real (unsaved) ORM model instances and patch each model's `.objects` manager, using
`django.test.Client` against real URLs — this exercises real view/template code without a live
`warehouse` connection. This keeps the suite fast and runnable anywhere (CI, a fresh laptop) with
zero external dependencies, at the cost of not catching integration issues between the mocked
boundary and the real service — those are instead caught by the periodic live pipeline run
(`scripts/run_pipeline.py`, see its Task 30.5 outcome in `CLAUDE.md` for the first such run).

## 8. Explicit non-goals

No Spark, Kafka, Snowflake, Redshift, Lambda, Terraform, or Kubernetes. This project intentionally
stays single-machine: the pipeline processes a few hundred movies per run, values in the
hundreds-of-megabytes-to-low-gigabytes range that fit comfortably in pandas DataFrames. The
architecture patterns (layered lake, star schema, idempotent upserts, watermark-based incremental
loads, quarantine-based data quality) are the same ones a distributed version would use — swapping
pandas for Spark and a single Postgres instance for Redshift/Snowflake would be a scaling exercise,
not a redesign.
