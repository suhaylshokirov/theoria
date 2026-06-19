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
Last completed task   : None
Currently on          : None
Current phase         : None
Blockers / open issues: None
Last updated          : None
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
│   ├── tmdb_client.py          # TMDB API wrapper (Task 2 ✅)
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
`s3://theoria-datalake/<layer>/<entity>/ingestion_date=YYYY-MM-DD/<file>.{json|parquet}`

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
| 1     | TMDB Ingestion (Bronze) | 1–8   | In progress |
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
- **Outcome:** Root-level scaffold exists; `pip install -r requirements.txt` works; `python -c "import config"` runs cleanly when `.env` is filled in.

#### [x] Task 2 — TMDB API client wrapper
- **Goal:** Single reusable client for all TMDB calls.
- **Files:** `etl/tmdb_client.py`
- **Key rules:** Centralize base URL and API key; retry-with-backoff for 429/5xx; raise `TMDBAPIError` on persistent failure; never swallow errors.
- **Outcome:** `TMDBClient` with `get_genres()`, `get_popular_movies(page)`, `get_movie_details(movie_id)`, `get_movie_credits(movie_id)`. Retry/backoff verified; real TMDB smoke test passed.

#### [ ] Task 3 — S3 writer utility (shared)
- **Goal:** Shared write-to-S3 logic for all ingestion scripts.
- **Files:** `etl/s3_utils.py`
- **Steps:** `write_json(bucket, key, data)`, `write_parquet(bucket, key, df)`, `build_path(layer, entity, ingestion_date, filename)`.
- **Expected output:** Bronze scripts call shared functions; path convention defined in exactly one place.
- **Outcome:** _(fill in when done)_

#### [ ] Task 4 — Bronze ingestion: Genres
- **Goal:** Pull genre list and write raw JSON to Bronze.
- **Files:** `etl/bronze/ingest_genres.py`
- **Expected output:** File at `bronze/genres/ingestion_date=.../genres.json`; log row count + path.
- **Outcome:** _(fill in when done)_

#### [ ] Task 5 — Bronze ingestion: Movies (paginated)
- **Goal:** Pull a catalog of movies, one file per page.
- **Files:** `etl/bronze/ingest_movies.py`
- **Key rules:** Configurable `MAX_PAGES`; one JSON file per page; partial failure must not lose completed pages; collect discovered `movie_id` list.
- **Expected output:** N JSON files in S3; log summary of total movies.
- **Outcome:** _(fill in when done)_

#### [ ] Task 6 — Bronze ingestion: Movie details
- **Goal:** Fetch full details per `movie_id`.
- **Files:** `etl/bronze/ingest_movie_details.py`
- **Key rules:** One file per movie; log specific `movie_id` on failure (not just "ingestion failed").
- **Expected output:** One JSON per `movie_id`; failures logged with the id.
- **Outcome:** _(fill in when done)_

#### [ ] Task 7 — Bronze ingestion: Credits (cast & crew)
- **Goal:** Pull cast/crew per movie.
- **Files:** `etl/bronze/ingest_credits.py`
- **Expected output:** `bronze/credits/ingestion_date=.../<movie_id>.json` per movie.
- **Outcome:** _(fill in when done)_

#### [ ] Task 8 — Ingestion logging & run summary
- **Goal:** Consistent logging across all ingestion scripts.
- **Files:** `etl/logging_config.py`; small edits to Tasks 4–7.
- **Expected output:** Every run logs: start time, records fetched, records written, failures, duration. One-line summary at end.
- **Outcome:** _(fill in when done)_

---

### Phase 2 — Data Lake (Silver & Gold)

#### [ ] Task 9 — Silver transform: Movies
- **Files:** `etl/silver/transform_movies.py`
- **Steps:** Read Bronze JSON → flatten → cast types → deduplicate on `movie_id` → write Parquet.
- **Outcome:** _(fill in when done)_

#### [ ] Task 10 — Silver transform: People (actors & directors)
- **Files:** `etl/silver/transform_people.py`
- **Steps:** Read Bronze credits → split cast/crew → standardize → deduplicate on `person_id` → write `silver/actors/` and `silver/directors/` separately.
- **Outcome:** _(fill in when done)_

#### [ ] Task 11 — Silver transform: Genres
- **Files:** `etl/silver/transform_genres.py`
- **Outcome:** _(fill in when done)_

#### [ ] Task 12 — Silver transform: Credits bridge
- **Files:** `etl/silver/transform_credits_bridge.py`
- **Steps:** Rows of `(movie_id, person_id, role, ordering)` → dedupe → validate referential integrity → write Parquet. Flag (don't crash on) orphan rows.
- **Outcome:** _(fill in when done)_

#### [ ] Task 13 — Silver data quality checks
- **Files:** `data_quality/silver_checks.py`
- **Steps:** Null checks, duplicate-key checks, schema/type validation, range checks. Write rejects to `data_quality/rejected/`. Auto-run after Tasks 9–12.
- **Outcome:** _(fill in when done)_

#### [ ] Task 14 — Gold layer: aggregated datasets
- **Files:** `etl/gold/build_gold_datasets.py`
- **Steps:** Pre-aggregate from Silver: movie metrics per genre, counts/avg ratings per decade, actor filmography counts, director avg ratings.
- **Outcome:** _(fill in when done)_

---

### Phase 3 — Warehouse Modeling (PostgreSQL)

#### [ ] Task 15 — PostgreSQL setup & connection layer
- **Files:** `warehouse/db.py`
- **Steps:** Create DB `theoria`; `get_session()` via SQLAlchemy engine from `DATABASE_URL`.
- **Outcome:** _(fill in when done)_

#### [ ] Task 16 — DDL: Dimension tables
- **Files:** `warehouse/ddl/01_dimensions.sql`
- **Steps:** `CREATE TABLE` for all five dims with `PRIMARY KEY`.
- **Outcome:** _(fill in when done)_

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