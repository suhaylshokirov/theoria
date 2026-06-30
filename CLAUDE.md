# Theoria — Claude Code Project Memory

A movie analytics platform (mini IMDb + analytics) built to learn real Data Engineering:
`TMDB API → S3 Data Lake (Bronze/Silver/Gold) → PostgreSQL warehouse (star schema) → Django UI`

---

## Quick Commands

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -c "import config"                    # verify env is set up
pytest                                       # run all tests
python manage.py runserver                   # start Django
```

---

## Current Status — UPDATE AFTER EVERY TASK

```
Last completed task   : Task 16 — DDL: Dimension tables
Currently on          : Task 17 — DDL: Fact tables
Current phase         : Phase 3 — Warehouse Modeling
Blockers / open issues: None
Last updated          : 2026-06-30
```

**After finishing any task, in this order:**
1. Check off `[ ]` → `[x]` in the Task List below.
2. Fill in that task's **Outcome** line (1–2 sentences: what now exists/works).
3. Update the status block above.
4. Write the learning entry in `for_learning.md` (see rules below).
5. Commit: `git add -A && git commit -m "Task N: short description"`

---

## for_learning.md — The Non-Negotiable Teaching Rule

After **every completed task**, append an entry to `for_learning.md` in the project root.
Never skip this. If a task is small, one paragraph is fine — but it must exist.

**Each entry must include:**

- **What was built** — plain-language summary, no jargon dump.
- **Concepts used** — name every DE/Python/SQL concept explicitly (e.g. "idempotent ingestion", "star schema", "upsert").
- **Code explained** — point to the 2–3 most important functions/lines and explain what they do and *why*.
- **What to study next** — one concrete follow-up (a concept, a docs page, a question to explore).

**Format to use:**

```markdown
## Task N — Title

### What Was Built
...

### Concepts Used
- **Concept name**: explanation in plain English.

### Key Code
`path/to/file.py` — `function_name()`:
> What it does and why it's written this way, not another way.

### What to Study Next
...
```

Keep it concrete. A first-year DS student should be able to re-explain it in an interview after reading it.

---

## Project Structure

```
theoria/
├── etl/
│   ├── tmdb_client.py          # TMDB API wrapper
│   ├── s3_utils.py             # shared S3 write helpers
│   ├── logging_config.py       # shared logging setup
│   ├── incremental.py          # watermark / incremental load logic
│   ├── bronze/
│   │   ├── ingest_genres.py
│   │   ├── ingest_movies.py
│   │   ├── ingest_movie_details.py
│   │   └── ingest_credits.py
│   ├── silver/
│   │   ├── transform_movies.py
│   │   ├── transform_people.py
│   │   ├── transform_genres.py
│   │   └── transform_credits_bridge.py
│   ├── gold/
│   │   └── build_gold_datasets.py
│   └── warehouse_loader/
│       ├── load_dimensions.py
│       └── load_facts.py
├── data_quality/
│   ├── silver_checks.py
│   ├── warehouse_checks.py
│   └── rejected/               # quarantined bad rows (never deleted)
├── warehouse/
│   ├── db.py                   # SQLAlchemy engine + get_session()
│   ├── ddl/
│   │   ├── 01_dimensions.sql
│   │   └── 02_facts.sql
│   └── queries/                # analytics SQL files
├── django_app/
│   ├── core/
│   ├── movies/
│   └── analytics/
├── docs/
│   └── architecture.md
├── tests/
│   ├── test_etl.py
│   ├── test_data_quality.py
│   └── test_django_views.py
├── scripts/
├── logs/                       # rotating log files (gitignored)
├── for_learning.md             # ← teaching log, appended after every task
├── config.py                   # loads all env vars; fails loud if missing
├── .env.example
├── requirements.txt
└── README.md
```

---

## Stack & Constraints

**Stack:** Python, SQL, PostgreSQL, AWS S3, Django + Django Templates
`requests`, `pandas`, `pyarrow`, `boto3`, `SQLAlchemy`, `psycopg2-binary`, `python-dotenv`, `pytest`

**Explicit non-goals:** No Spark, Kafka, Snowflake, Redshift, Lambda, Terraform, Kubernetes.
This is a single-machine DE learning project, not an infra project.

**Data flow:**
```
TMDB API → Bronze (S3, raw JSON) → Silver (S3, cleaned Parquet)
         → Gold (S3, aggregated Parquet) → PostgreSQL → Django
```

**S3 path convention:**
`s3://your-datalake-name/<layer>/<entity>/ingestion_date=YYYY-MM-DD/<file>.{json|parquet}`

---

## Warehouse Schema (star schema)

**Dimensions:**
- `dim_movie(movie_id PK, title, release_date, runtime, budget, revenue, original_language, status)`
- `dim_actor(actor_id PK, name, gender, popularity)`
- `dim_director(director_id PK, name, gender, popularity)`
- `dim_genre(genre_id PK, genre_name)`
- `dim_date(date_id PK, full_date, year, month, day, decade)`

**Facts:**
- `fact_movie_metrics(movie_id FK, date_id FK, genre_id FK, rating, vote_count, revenue, budget, popularity)`
- `fact_casting(movie_id FK, actor_id FK, director_id FK, role, ordering)`

---

## Coding Rules (apply always)

- **One module, one responsibility.** No business logic inside `if __name__ == "__main__"`.
- **All config from `config.py`.** No hardcoded keys, paths, or URLs anywhere.
- **Every ETL script must be idempotent.** Re-running it twice gives the same result.
- **Bronze is immutable.** Never overwrite or edit Bronze files. Append-only.
- **Silver/Gold are rebuilt from source.** Never hand-edit Parquet files.
- **Quarantine bad rows, never silently drop them.** Write rejects to `data_quality/rejected/`.
- **All DDL and analytics SQL live in `.sql` files.** Never type them only in a notebook/shell.
- **Log the what and how many, not just "done".** Include counts and duration in every run summary.
- **Never `SELECT *` in app code.** Name columns explicitly.
- **Index FK columns** used in joins (PostgreSQL).
- **One task = one commit.** Message format: `Task N: short description`

---

## Phase Map

| Phase | Name                   | Tasks  | Status      |
|-------|------------------------|--------|-------------|
| 1     | TMDB Ingestion (Bronze) | 1–8   | Not started |
| 2     | Data Lake (Silver/Gold) | 9–14  | Not started |
| 3     | Warehouse Modeling      | 15–21 | Not started |
| 4     | SQL Analytics           | 22    | Not started |
| 5     | Django UI               | 23–30 | Not started |
| 6     | Polish                  | 31–33 | Not started |

---

## Task List

> Work top to bottom. Don't skip ahead — each phase depends on data the previous one produced.

### Phase 1 — TMDB Ingestion (Bronze)

#### [x] Task 1 — Project scaffolding & environment
- **Goal:** Repo skeleton, virtual env, config, and secrets handling.
- **Files:** full `theoria/` tree, `requirements.txt`, `.env.example`, `config.py`, `.gitignore`
- **Outcome:** Full directory tree created with Python packages; `config.py` loads `.env` via python-dotenv and fails loud listing every missing required var at once; `.env`/`venv` gitignored while `.env.example` is tracked. `python -c "import config"` passes with a filled `.env` and raises a clear `ConfigError` without one. Deps pinned in `requirements.txt` and installed in `venv`.

#### [x] Task 2 — TMDB API client wrapper
- **Goal:** Single reusable client for all TMDB calls.
- **Files:** `etl/tmdb_client.py`
- **Key rules:** Centralize base URL and API key; retry-with-backoff for 429/5xx; raise `TMDBAPIError` on persistent failure; never swallow errors.
- **Outcome:** `TMDBClient` wraps a reusable `requests.Session`, reads base URL + v3 API key from `config.py`, and exposes `get()` plus convenience wrappers (`get_genres`, `get_popular_movies`, `get_movie_details`, `get_movie_credits`). Retries 429/5xx with exponential backoff (honouring `Retry-After`), fails fast on non-retryable codes, and raises `TMDBAPIError` with endpoint + status on persistent failure. 4 mocked unit tests in `tests/test_etl.py` pass; live smoke test fetched 19 genres from the real API.

#### [x] Task 3 — S3 writer utility (shared)
- **Goal:** Shared write-to-S3 logic for all ingestion scripts.
- **Files:** `etl/s3_utils.py`
- **Steps:** `write_json(bucket, key, data)`, `write_parquet(bucket, key, df)`, `build_path(layer, entity, ingestion_date, filename)`.
- **Expected output:** Bronze scripts call shared functions; path convention defined in exactly one place.
- **Outcome:** `etl/s3_utils.py` centralises S3 writes: a lazily-built, reused boto3 client (credentials/region from `config.py`); `build_path()` is the single place defining the `<layer>/<entity>/ingestion_date=YYYY-MM-DD/<file>` key convention (accepts `str` or `date`); `write_json()` uploads pretty UTF-8 JSON and `write_parquet()` serialises a DataFrame to Parquet in-memory (pyarrow, no temp files) — both return the `s3://` URI, log bytes/rows, and never swallow errors. 4 mocked unit tests added to `tests/test_etl.py` (path convention, date handling, JSON + Parquet round-trip); full suite of 8 passes with no network access.

#### [x] Task 4 — Bronze ingestion: Genres
- **Goal:** Pull genre list and write raw JSON to Bronze.
- **Files:** `etl/bronze/ingest_genres.py`
- **Expected output:** File at `bronze/genres/ingestion_date=.../genres.json`; log row count + path.
- **Outcome:** `ingest_genres()` fetches the TMDB genre list and writes the raw API payload to `s3://<bucket>/bronze/genres/ingestion_date=YYYY-MM-DD/genres.json`; logs genre count, destination URI, and elapsed time. Idempotent (same date → same key/content). Dependency-injected client + date params make it fully testable without network access; 2 new unit tests added (10/10 pass).

#### [x] Task 5 — Bronze ingestion: Movies (paginated)
- **Goal:** Pull a catalog of movies, one file per page.
- **Files:** `etl/bronze/ingest_movies.py`
- **Key rules:** Configurable `MAX_PAGES`; one JSON file per page; partial failure must not lose completed pages; collect discovered `movie_id` list.
- **Expected output:** N JSON files in S3; log summary of total movies.
- **Outcome:** `ingest_movies()` fetches up to `MAX_PAGES` pages of the TMDB popular-movies list and writes each as `bronze/movies/ingestion_date=YYYY-MM-DD/page_NNNN.json`; pages are flushed to S3 individually so a failure on page N never loses pages already written; returns the full list of discovered `movie_id`s for downstream use. Logs per-page counts and a final summary. 3 new unit tests added (13/13 pass).

#### [x] Task 6 — Bronze ingestion: Movie details
- **Goal:** Fetch full details per `movie_id`.
- **Files:** `etl/bronze/ingest_movie_details.py`
- **Key rules:** One file per movie; log specific `movie_id` on failure (not just "ingestion failed").
- **Expected output:** One JSON per `movie_id`; failures logged with the id.
- **Outcome:** `ingest_movie_details()` accepts a list of movie IDs, fetches each from TMDB, and writes `bronze/movie_details/ingestion_date=YYYY-MM-DD/<movie_id>.json` individually. Failures are caught per-ID, logged with the specific `movie_id`, and returned in a `failed_ids` list — completed movies are never discarded. Returns `(succeeded_ids, failed_ids)` so callers can retry only the failed subset. 3 new unit tests added (16/16 pass).

#### [x] Task 7 — Bronze ingestion: Credits (cast & crew)
- **Goal:** Pull cast/crew per movie.
- **Files:** `etl/bronze/ingest_credits.py`
- **Expected output:** `bronze/credits/ingestion_date=.../<movie_id>.json` per movie.
- **Outcome:** `ingest_credits()` fetches the TMDB credits endpoint per movie and writes `bronze/credits/ingestion_date=YYYY-MM-DD/<movie_id>.json` individually. Same fail-and-continue pattern as Task 6: failures logged with the specific `movie_id`, returned in `failed_ids`. 3 new unit tests added (19/19 pass).

#### [x] Task 8 — Ingestion logging & run summary
- **Goal:** Consistent logging across all ingestion scripts.
- **Files:** `etl/logging_config.py`; small edits to Tasks 4–7.
- **Expected output:** Every run logs: start time, records fetched, records written, failures, duration. One-line summary at end.
- **Outcome:** `etl/logging_config.py` created with `setup_logging(script_name)`: attaches a console handler (INFO+, timestamped) and a `RotatingFileHandler` (DEBUG+, 5 MB × 3 backups) to the root logger, writing to `logs/<script_name>.log`. All four ingestion scripts updated to call it from `__main__`. 1 new test added (20/20 pass).

---

### Phase 2 — Data Lake (Silver & Gold)

#### [x] Task 9 — Silver transform: Movies
- **Files:** `etl/silver/transform_movies.py`
- **Steps:** Read Bronze JSON → flatten → cast types → deduplicate on `movie_id` → write Parquet.
- **Outcome:** `transform_movies()` lists all Bronze movie-detail JSON files for a given date via S3 paginator, flattens each raw TMDB payload into one typed row (renaming `id` → `movie_id`, extracting `genre_ids` from nested genres), casts every field with pandas nullable types (`Int64`, coerced dates), deduplicates on `movie_id`, and writes `silver/movies/ingestion_date=YYYY-MM-DD/movies.parquet`. Raises `FileNotFoundError` on empty input. 7 new tests added (27/27 pass).

#### [x] Task 10 — Silver transform: People (actors & directors)
- **Files:** `etl/silver/transform_people.py`
- **Steps:** Read Bronze credits → split cast/crew → standardize → deduplicate on `person_id` → write `silver/actors/` and `silver/directors/` separately.
- **Outcome:** `transform_people()` reads all Bronze credits JSON for a date, extracts cast rows as actors and `job=="Director"` crew rows as directors, casts types with pandas nullable types, deduplicates each group on `person_id` across all movies, and writes `silver/actors/…/actors.parquet` and `silver/directors/…/directors.parquet`. Returns both S3 URIs. 7 new tests added (34/34 pass).

#### [x] Task 11 — Silver transform: Genres
- **Files:** `etl/silver/transform_genres.py`
- **Outcome:** `transform_genres()` reads the single Bronze `genres.json` for a given date, flattens the TMDB payload into `(genre_id, genre_name)` rows, casts types with pandas nullable types (`Int64`, `string`), deduplicates on `genre_id`, and writes `silver/genres/ingestion_date=YYYY-MM-DD/genres.parquet`. 7 new tests added (41/41 pass).

#### [x] Task 12 — Silver transform: Credits bridge
- **Files:** `etl/silver/transform_credits_bridge.py`
- **Steps:** Rows of `(movie_id, person_id, role, ordering)` → dedupe → validate referential integrity → write Parquet. Flag (don't crash on) orphan rows.
- **Outcome:** `transform_credits_bridge()` reads all Bronze credits JSON for a given date and extracts `(movie_id, person_id, credit_type, role, ordering)` rows for every cast and crew member. Deduplicates on `(movie_id, person_id, credit_type)`, drops rows with null IDs (with a warning), and optionally checks referential integrity against `known_movie_ids`/`known_person_ids` sets — orphan IDs are logged as warnings but rows are kept. Writes `silver/credits_bridge/ingestion_date=YYYY-MM-DD/credits_bridge.parquet`. 9 new tests added (50/50 pass).

#### [x] Task 13 — Silver data quality checks
- **Files:** `data_quality/silver_checks.py`, `tests/test_data_quality.py`
- **Steps:** Null checks, duplicate-key checks, schema/type validation, range checks. Write rejects to `data_quality/rejected/`. Auto-run after Tasks 9–12.
- **Outcome:** `run_silver_checks()` reads all five Silver Parquet files (movies, actors, directors, genres, credits_bridge) and runs four checks per entity: schema (expected columns present), nulls (required columns have no nulls), duplicates (PK uniqueness), ranges (vote_average 0–10, counts/popularity ≥ 0, etc.). Each check produces a `CheckResult(entity, check, passed, bad_count, message)`. Bad rows from null/duplicate/range failures are tagged with a `rejection_reason` column and written to `data_quality/rejected/<entity>_rejected_<date>.parquet`. Missing Silver files produce a load-failure result and the run continues. Exits with code 1 if any check fails. 22 new tests added (72/72 pass).

#### [x] Task 14 — Gold layer: aggregated datasets
- **Files:** `etl/gold/build_gold_datasets.py`
- **Steps:** Pre-aggregate from Silver: movie metrics per genre, counts/avg ratings per decade, actor filmography counts, director avg ratings.
- **Outcome:** `build_gold_datasets()` reads all five Silver Parquet files, computes four analytical aggregations (genre metrics, decade stats, actor filmography, director ratings) using pandas groupby, and writes each as a Parquet file to the Gold layer under `gold/<dataset>/ingestion_date=YYYY-MM-DD/`. Idempotent, raises `FileNotFoundError` on missing Silver input. 17 new tests added (89/89 pass).

---

### Phase 3 — Warehouse Modeling (PostgreSQL)

#### [x] Task 15 — PostgreSQL setup & connection layer
- **Files:** `warehouse/db.py`
- **Steps:** Create DB `theoria`; `get_session()` via SQLAlchemy engine from `DATABASE_URL`.
- **Outcome:** `warehouse/db.py` provides a lazy singleton `get_engine()` (reads `config.DATABASE_URL`, sets `pool_pre_ping=True`) and a context-manager `get_session()` that auto-commits on success and rolls back on any exception. `check_connection()` returns a boolean for health checks; `reset_engine()` disposes the pool for test isolation. 7 new tests added (96/96 pass).

#### [x] Task 16 — DDL: Dimension tables
- **Files:** `warehouse/ddl/01_dimensions.sql`
- **Steps:** `CREATE TABLE` for all five dims with `PRIMARY KEY`.
- **Outcome:** `warehouse/ddl/01_dimensions.sql` defines all five dimension tables (`dim_movie`, `dim_actor`, `dim_director`, `dim_genre`, `dim_date`) with typed columns, named PRIMARY KEY constraints, and `IF NOT EXISTS` guards for idempotency. DDL executed against the `theoria` PostgreSQL database; all five tables confirmed present in `information_schema.tables`.

#### [ ] Task 17 — DDL: Fact tables
- **Files:** `warehouse/ddl/02_facts.sql`
- **Steps:** `CREATE TABLE` for both facts; explicit `FOREIGN KEY` constraints; indexes on FK columns.
- **Outcome:** _(fill in when done)_

#### [ ] Task 18 — Loader: Dimensions
- **Files:** `etl/warehouse_loader/load_dimensions.py`
- **Steps:** Read Silver Parquet → upsert into `dim_*` using `ON CONFLICT DO UPDATE`. Populate `dim_date` as a full calendar table.
- **Outcome:** _(fill in when done)_

#### [ ] Task 19 — Loader: Facts
- **Files:** `etl/warehouse_loader/load_facts.py`
- **Steps:** Join Silver to resolve surrogate keys → insert into fact tables → quarantine rows that fail FK lookups.
- **Outcome:** _(fill in when done)_

#### [ ] Task 20 — Incremental load logic
- **Files:** `etl/incremental.py`; edits to loaders.
- **Steps:** Track watermark (last successful `ingestion_date`); process only newer partitions; facts: guard against duplicate inserts via unique constraint on `(movie_id, ingestion_date)`.
- **Outcome:** _(fill in when done)_

#### [ ] Task 21 — End-to-end data quality validation
- **Files:** `data_quality/warehouse_checks.py`
- **Steps:** FK integrity checks; row-count sanity Bronze→Silver→Gold→Warehouse; produce single pass/fail report.
- **Outcome:** _(fill in when done)_

---

### Phase 4 — SQL Analytics

#### [ ] Task 22 — Analytics SQL queries
- **Files:** `warehouse/queries/` (one `.sql` file per query or one combined file)
- **Queries:** Top-rated directors, most productive actors, revenue by genre, movies by decade, director trend over time, actor collaboration frequency (self-join on `fact_casting`), genre growth over time.
- **Outcome:** _(fill in when done)_

---

### Phase 5 — Django UI

#### [ ] Task 23 — Django project & `core` app
- **Files:** `django_app/` (project), `django_app/core/` (app), `base.html`, `settings.py`
- **Steps:** `startproject` + `startapp core`; point `DATABASES` at the warehouse (read-only); nav: Home, Movies, Analytics.
- **Outcome:** _(fill in when done)_

#### [ ] Task 24 — `movies` app: models
- **Files:** `django_app/movies/models.py`
- **Steps:** ORM models for all warehouse tables with `class Meta: managed = False`. Map FKs where useful.
- **Outcome:** _(fill in when done)_

#### [ ] Task 25 — Home page
- **Files:** `movies/views.py`, `movies/urls.py`, `movies/templates/movies/home.html`
- **Steps:** Aggregate total movies, actors/directors, avg rating. Route: `/`.
- **Outcome:** _(fill in when done)_

#### [ ] Task 26 — Movie Details page
- **Files:** `movies/views.py` (`movie_detail`), URL `/movies/<id>/`, template.
- **Steps:** Fetch movie + genres + cast via joins. Avoid N+1 queries (`select_related`/`prefetch_related` or explicit join).
- **Outcome:** _(fill in when done)_

#### [ ] Task 27 — Actor Details page
- **Files:** `movies/views.py` (`actor_detail`), URL `/actors/<id>/`, template.
- **Steps:** Filmography via `fact_casting`; compute career stats (film count, avg rating, career span) in SQL.
- **Outcome:** _(fill in when done)_

#### [ ] Task 28 — Director Details page
- **Files:** `movies/views.py` (`director_detail`), URL `/directors/<id>/`, template.
- **Steps:** Mirror of Task 27 for directors.
- **Outcome:** _(fill in when done)_

#### [ ] Task 29 — Genre Details page
- **Files:** `movies/views.py` (`genre_detail`), URL `/genres/<id>/`, template.
- **Steps:** Top-rated movies in genre; revenue trend by year. Reuse Gold-layer aggregates where possible.
- **Outcome:** _(fill in when done)_

#### [ ] Task 30 — Analytics Dashboard
- **Files:** `analytics/` app, `analytics/views.py`, `analytics/urls.py`, templates.
- **Steps:** Each panel calls one Task 22 query (via `.raw()` or `.annotate()`). Basic tables; optional Chart.js via CDN for trends. Route: `/analytics/`.
- **Outcome:** _(fill in when done)_

---

### Phase 6 — Polish

#### [ ] Task 31 — Tests
- **Files:** `tests/test_etl.py`, `tests/test_data_quality.py`, `tests/test_django_views.py`
- **Steps:** Unit tests: a Silver transform on a small fixture (3–5 rows), a DQ check catching a bad row, each view returns 200 with expected context keys.
- **Outcome:** _(fill in when done)_

#### [ ] Task 32 — Documentation
- **Files:** `README.md`, `docs/architecture.md`
- **Steps:** README covers full setup → ingest → transform → load → Django. `architecture.md` covers data flow + schema (written for an interviewer).
- **Outcome:** _(fill in when done)_

#### [ ] Task 33 — Logging, config, and dependency cleanup
- **Steps:** Grep for hardcoded paths/keys; confirm all scripts use `config.py` and `logging_config.py`; regenerate `requirements.txt` with `pip freeze`; trim unused packages.
- **Outcome:** _(fill in when done)_

---

## Additional Reference

Full design rationale and original architecture decisions: `docs/architecture.md`
Learning log (updated after every task): `for_learning.md`