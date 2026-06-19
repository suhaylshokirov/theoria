# Theoria — Claude Code Project Memory

A movie analytics platform (mini IMDb + analytics) built to learn real Data Engineering:
`TMDB API → S3 Data Lake (Bronze/Silver/Gold) → PostgreSQL warehouse (star schema) → Django UI`

\---

## ⚡ Quick Commands

```bash
python -m venv venv \&\& source venv/bin/activate
pip install -r requirements.txt
python -c "import config"                    # verify env is set up
pytest                                       # run all tests
python manage.py runserver                   # start Django
```

\---

## 📍 Current Status — UPDATE AFTER EVERY TASK

```
Last completed task   : None
Currently on          : None
Current phase         : None
Blockers / open issues: None
Last updated          : None
```

**After finishing any task, in this order:**

1. Check off `\[ ]` → `\[x]` in the Task List below.
2. Fill in that task's **Outcome** line (1–2 sentences: what now exists/works).
3. Update the status block above.
4. Write the learning entry in `for\_learning.md` (see rules below).
5. Commit: `git add -A \&\& git commit -m "Task N: short description"`

\---

## 📚 for\_learning.md — The Non-Negotiable Teaching Rule

After **every completed task**, append an entry to `for\_learning.md` in the project root.
Never skip this. If a task is small, one paragraph is fine — but it must exist.

**Each entry must include:**

* **What was built** — plain-language summary, no jargon dump.
* **Concepts used** — name every DE/Python/SQL concept explicitly (e.g. "idempotent ingestion", "star schema", "upsert").
* **Code explained** — point to the 2–3 most important functions/lines and explain what they do and *why*.
* **What to study next** — one concrete follow-up (a concept, a docs page, a question to explore).

**Format to use:**

```markdown
## Task N — Title

### What Was Built
...

### Concepts Used
- \*\*Concept name\*\*: explanation in plain English.

### Key Code
`path/to/file.py` — `function\_name()`:
> What it does and why it's written this way, not another way.

### What to Study Next
...
```

Keep it concrete. A first-year DS student should be able to re-explain it in an interview after reading it.

\---

## 🗂️ Project Structure

```
theoria/
├── etl/
│   ├── tmdb\_client.py          # TMDB API wrapper (Task 2 ✅)
│   ├── s3\_utils.py             # shared S3 write helpers
│   ├── logging\_config.py       # shared logging setup
│   ├── incremental.py          # watermark / incremental load logic
│   ├── bronze/
│   │   ├── ingest\_genres.py
│   │   ├── ingest\_movies.py
│   │   ├── ingest\_movie\_details.py
│   │   └── ingest\_credits.py
│   ├── silver/
│   │   ├── transform\_movies.py
│   │   ├── transform\_people.py
│   │   ├── transform\_genres.py
│   │   └── transform\_credits\_bridge.py
│   ├── gold/
│   │   └── build\_gold\_datasets.py
│   └── warehouse\_loader/
│       ├── load\_dimensions.py
│       └── load\_facts.py
├── data\_quality/
│   ├── silver\_checks.py
│   ├── warehouse\_checks.py
│   └── rejected/               # quarantined bad rows (never deleted)
├── warehouse/
│   ├── db.py                   # SQLAlchemy engine + get\_session()
│   ├── ddl/
│   │   ├── 01\_dimensions.sql
│   │   └── 02\_facts.sql
│   └── queries/                # analytics SQL files
├── django\_app/
│   ├── core/
│   ├── movies/
│   └── analytics/
├── docs/
│   └── architecture.md
├── tests/
│   ├── test\_etl.py
│   ├── test\_data\_quality.py
│   └── test\_django\_views.py
├── scripts/
├── logs/                       # rotating log files (gitignored)
├── for\_learning.md             # ← teaching log, appended after every task
├── config.py                   # loads all env vars; fails loud if missing
├── .env.example
├── requirements.txt
└── README.md
```

\---

## 🔧 Stack \& Constraints

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
`s3://theoria-datalake/<layer>/<entity>/ingestion\_date=YYYY-MM-DD/<file>.{json|parquet}`

\---

## 🏗️ Warehouse Schema (star schema)

**Dimensions:**

* `dim\_movie(movie\_id PK, title, release\_date, runtime, budget, revenue, original\_language, status)`
* `dim\_actor(actor\_id PK, name, gender, popularity)`
* `dim\_director(director\_id PK, name, gender, popularity)`
* `dim\_genre(genre\_id PK, genre\_name)`
* `dim\_date(date\_id PK, full\_date, year, month, day, decade)`

**Facts:**

* `fact\_movie\_metrics(movie\_id FK, date\_id FK, genre\_id FK, rating, vote\_count, revenue, budget, popularity)`
* `fact\_casting(movie\_id FK, actor\_id FK, director\_id FK, role, ordering)`

\---

## 📋 Coding Rules (apply always)

* **One module, one responsibility.** No business logic inside `if \_\_name\_\_ == "\_\_main\_\_"`.
* **All config from `config.py`.** No hardcoded keys, paths, or URLs anywhere.
* **Every ETL script must be idempotent.** Re-running it twice gives the same result.
* **Bronze is immutable.** Never overwrite or edit Bronze files. Append-only.
* **Silver/Gold are rebuilt from source.** Never hand-edit Parquet files.
* **Quarantine bad rows, never silently drop them.** Write rejects to `data\_quality/rejected/`.
* **All DDL and analytics SQL live in `.sql` files.** Never type them only in a notebook/shell.
* **Log the what and how many, not just "done".** Include counts and duration in every run summary.
* **Never `SELECT \*` in app code.** Name columns explicitly.
* **Index FK columns** used in joins (PostgreSQL).
* **One task = one commit.** Message format: `Task N: short description`

\---

## 🗺️ Phase Map

|Phase|Name|Tasks|Status|
|-|-|-|-|
|1|TMDB Ingestion (Bronze)|1–8|In progress|
|2|Data Lake (Silver/Gold)|9–14|Not started|
|3|Warehouse Modeling|15–21|Not started|
|4|SQL Analytics|22|Not started|
|5|Django UI|23–30|Not started|
|6|Polish|31–33|Not started|

\---

## ✅ Task List

> Work top to bottom. Don't skip ahead — each phase depends on data the previous one produced.

### Phase 1 — TMDB Ingestion (Bronze)

#### \[x] Task 1 — Project scaffolding \& environment

* **Goal:** Repo skeleton, virtual env, config, and secrets handling.
* **Files:** full `theoria/` tree, `requirements.txt`, `.env.example`, `config.py`, `.gitignore`
* **Outcome:** Root-level scaffold exists; `pip install -r requirements.txt` works; `python -c "import config"` runs cleanly when `.env` is filled in.

#### \[x] Task 2 — TMDB API client wrapper

* **Goal:** Single reusable client for all TMDB calls.
* **Files:** `etl/tmdb\_client.py`
* **Key rules:** Centralize base URL and API key; retry-with-backoff for 429/5xx; raise `TMDBAPIError` on persistent failure; never swallow errors.
* **Outcome:** `TMDBClient` with `get\_genres()`, `get\_popular\_movies(page)`, `get\_movie\_details(movie\_id)`, `get\_movie\_credits(movie\_id)`. Retry/backoff verified; real TMDB smoke test passed.

#### \[ ] Task 3 — S3 writer utility (shared)

* **Goal:** Shared write-to-S3 logic for all ingestion scripts.
* **Files:** `etl/s3\_utils.py`
* **Steps:** `write\_json(bucket, key, data)`, `write\_parquet(bucket, key, df)`, `build\_path(layer, entity, ingestion\_date, filename)`.
* **Expected output:** Bronze scripts call shared functions; path convention defined in exactly one place.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 4 — Bronze ingestion: Genres

* **Goal:** Pull genre list and write raw JSON to Bronze.
* **Files:** `etl/bronze/ingest\_genres.py`
* **Expected output:** File at `bronze/genres/ingestion\_date=.../genres.json`; log row count + path.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 5 — Bronze ingestion: Movies (paginated)

* **Goal:** Pull a catalog of movies, one file per page.
* **Files:** `etl/bronze/ingest\_movies.py`
* **Key rules:** Configurable `MAX\_PAGES`; one JSON file per page; partial failure must not lose completed pages; collect discovered `movie\_id` list.
* **Expected output:** N JSON files in S3; log summary of total movies.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 6 — Bronze ingestion: Movie details

* **Goal:** Fetch full details per `movie\_id`.
* **Files:** `etl/bronze/ingest\_movie\_details.py`
* **Key rules:** One file per movie; log specific `movie\_id` on failure (not just "ingestion failed").
* **Expected output:** One JSON per `movie\_id`; failures logged with the id.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 7 — Bronze ingestion: Credits (cast \& crew)

* **Goal:** Pull cast/crew per movie.
* **Files:** `etl/bronze/ingest\_credits.py`
* **Expected output:** `bronze/credits/ingestion\_date=.../<movie\_id>.json` per movie.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 8 — Ingestion logging \& run summary

* **Goal:** Consistent logging across all ingestion scripts.
* **Files:** `etl/logging\_config.py`; small edits to Tasks 4–7.
* **Expected output:** Every run logs: start time, records fetched, records written, failures, duration. One-line summary at end.
* **Outcome:** *(fill in when done)*

\---

### Phase 2 — Data Lake (Silver \& Gold)

#### \[ ] Task 9 — Silver transform: Movies

* **Files:** `etl/silver/transform\_movies.py`
* **Steps:** Read Bronze JSON → flatten → cast types → deduplicate on `movie\_id` → write Parquet.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 10 — Silver transform: People (actors \& directors)

* **Files:** `etl/silver/transform\_people.py`
* **Steps:** Read Bronze credits → split cast/crew → standardize → deduplicate on `person\_id` → write `silver/actors/` and `silver/directors/` separately.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 11 — Silver transform: Genres

* **Files:** `etl/silver/transform\_genres.py`
* **Outcome:** *(fill in when done)*

#### \[ ] Task 12 — Silver transform: Credits bridge

* **Files:** `etl/silver/transform\_credits\_bridge.py`
* **Steps:** Rows of `(movie\_id, person\_id, role, ordering)` → dedupe → validate referential integrity → write Parquet. Flag (don't crash on) orphan rows.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 13 — Silver data quality checks

* **Files:** `data\_quality/silver\_checks.py`
* **Steps:** Null checks, duplicate-key checks, schema/type validation, range checks. Write rejects to `data\_quality/rejected/`. Auto-run after Tasks 9–12.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 14 — Gold layer: aggregated datasets

* **Files:** `etl/gold/build\_gold\_datasets.py`
* **Steps:** Pre-aggregate from Silver: movie metrics per genre, counts/avg ratings per decade, actor filmography counts, director avg ratings.
* **Outcome:** *(fill in when done)*

\---

### Phase 3 — Warehouse Modeling (PostgreSQL)

#### \[ ] Task 15 — PostgreSQL setup \& connection layer

* **Files:** `warehouse/db.py`
* **Steps:** Create DB `theoria`; `get\_session()` via SQLAlchemy engine from `DATABASE\_URL`.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 16 — DDL: Dimension tables

* **Files:** `warehouse/ddl/01\_dimensions.sql`
* **Steps:** `CREATE TABLE` for all five dims with `PRIMARY KEY`.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 17 — DDL: Fact tables

* **Files:** `warehouse/ddl/02\_facts.sql`
* **Steps:** `CREATE TABLE` for both facts; explicit `FOREIGN KEY` constraints; indexes on FK columns.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 18 — Loader: Dimensions

* **Files:** `etl/warehouse\_loader/load\_dimensions.py`
* **Steps:** Read Silver Parquet → upsert into `dim\_\*` using `ON CONFLICT DO UPDATE`. Populate `dim\_date` as a full calendar table.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 19 — Loader: Facts

* **Files:** `etl/warehouse\_loader/load\_facts.py`
* **Steps:** Join Silver to resolve surrogate keys → insert into fact tables → quarantine rows that fail FK lookups.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 20 — Incremental load logic

* **Files:** `etl/incremental.py`; edits to loaders.
* **Steps:** Track watermark (last successful `ingestion\_date`); process only newer partitions; facts: guard against duplicate inserts via unique constraint on `(movie\_id, ingestion\_date)`.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 21 — End-to-end data quality validation

* **Files:** `data\_quality/warehouse\_checks.py`
* **Steps:** FK integrity checks; row-count sanity Bronze→Silver→Gold→Warehouse; produce single pass/fail report.
* **Outcome:** *(fill in when done)*

\---

### Phase 4 — SQL Analytics

#### \[ ] Task 22 — Analytics SQL queries

* **Files:** `warehouse/queries/` (one `.sql` file per query or one combined file)
* **Queries:** Top-rated directors, most productive actors, revenue by genre, movies by decade, director trend over time, actor collaboration frequency (self-join on `fact\_casting`), genre growth over time.
* **Outcome:** *(fill in when done)*

\---

### Phase 5 — Django UI

#### \[ ] Task 23 — Django project \& `core` app

* **Files:** `django\_app/` (project), `django\_app/core/` (app), `base.html`, `settings.py`
* **Steps:** `startproject` + `startapp core`; point `DATABASES` at the warehouse (read-only); nav: Home, Movies, Analytics.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 24 — `movies` app: models

* **Files:** `django\_app/movies/models.py`
* **Steps:** ORM models for all warehouse tables with `class Meta: managed = False`. Map FKs where useful.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 25 — Home page

* **Files:** `movies/views.py`, `movies/urls.py`, `movies/templates/movies/home.html`
* **Steps:** Aggregate total movies, actors/directors, avg rating. Route: `/`.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 26 — Movie Details page

* **Files:** `movies/views.py` (`movie\_detail`), URL `/movies/<id>/`, template.
* **Steps:** Fetch movie + genres + cast via joins. Avoid N+1 queries (`select\_related`/`prefetch\_related` or explicit join).
* **Outcome:** *(fill in when done)*

#### \[ ] Task 27 — Actor Details page

* **Files:** `movies/views.py` (`actor\_detail`), URL `/actors/<id>/`, template.
* **Steps:** Filmography via `fact\_casting`; compute career stats (film count, avg rating, career span) in SQL.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 28 — Director Details page

* **Files:** `movies/views.py` (`director\_detail`), URL `/directors/<id>/`, template.
* **Steps:** Mirror of Task 27 for directors.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 29 — Genre Details page

* **Files:** `movies/views.py` (`genre\_detail`), URL `/genres/<id>/`, template.
* **Steps:** Top-rated movies in genre; revenue trend by year. Reuse Gold-layer aggregates where possible.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 30 — Analytics Dashboard

* **Files:** `analytics/` app, `analytics/views.py`, `analytics/urls.py`, templates.
* **Steps:** Each panel calls one Task 22 query (via `.raw()` or `.annotate()`). Basic tables; optional Chart.js via CDN for trends. Route: `/analytics/`.
* **Outcome:** *(fill in when done)*

\---

### Phase 6 — Polish

#### \[ ] Task 31 — Tests

* **Files:** `tests/test\_etl.py`, `tests/test\_data\_quality.py`, `tests/test\_django\_views.py`
* **Steps:** Unit tests: a Silver transform on a small fixture (3–5 rows), a DQ check catching a bad row, each view returns 200 with expected context keys.
* **Outcome:** *(fill in when done)*

#### \[ ] Task 32 — Documentation

* **Files:** `README.md`, `docs/architecture.md`
* **Steps:** README covers full setup → ingest → transform → load → Django. `architecture.md` covers data flow + schema (written for an interviewer).
* **Outcome:** *(fill in when done)*

#### \[ ] Task 33 — Logging, config, and dependency cleanup

* **Steps:** Grep for hardcoded paths/keys; confirm all scripts use `config.py` and `logging\_config.py`; regenerate `requirements.txt` with `pip freeze`; trim unused packages.
* **Outcome:** *(fill in when done)*

\---

## 📄 Additional Reference

Full design rationale and original architecture decisions: `docs/architecture.md`
Learning log (updated after every task): `for\_learning.md`

