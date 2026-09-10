# Theoria — Claude Code Project Memory

A movie analytics platform (mini IMDb + analytics) built to learn real Data Engineering:
`TMDB API → S3 Data Lake (Bronze/Silver/Gold) → PostgreSQL warehouse (star schema) → Django UI`

---

## Quick Commands

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-etl.txt          # superset; requirements.txt is web-only
python -c "import config"                    # verify env is set up (core vars)
python -c "import config; config.require_etl()"   # the pipeline's own required set
pytest                                       # run all tests
python manage.py serve                        # auto-sync replica if stale, then runserver
```

**Two requirements files:** `requirements.txt` is the *web runtime only* (Django, psycopg2,
python-dotenv) because the hosted Vercel function installs exactly that file into a bundle capped
at 500 MB; `requirements-etl.txt` includes it and adds pandas/pyarrow/boto3/SQLAlchemy/pytest.
Install the ETL one locally and in CI. `config.py` groups required env vars by **role** — core
(`DATABASE_URL`), web (`DJANGO_SECRET_KEY`), etl (`TMDB_API_KEY`, `AWS_*`, `S3_BUCKET`) — enforced
by `require_web()`/`require_etl()` where that role starts, so neither process demands the other's
secrets. See `docs/architecture.md` §4.4.

**Hosting:** the site deploys to Vercel as one Python function, pinned to `fra1` so it sits in
the same region as Neon (the default `iad1` would re-create the ~90 ms/query problem the local
replica exists to solve). Data changes need no deploy — Actions writes Neon, the site reads it.
Schema changes need the *reverse* order of the Django habit: apply the DDL to Neon and run the
loader **before** deploying the code that reads it, since Django never migrates the warehouse.
`vercel.json`'s `ignoreCommand` stops the nightly `ops/refresh-history.md` commit from redeploying
the site every night — Vercel does not honour `[skip ci]`. See `docs/architecture.md` §4.4.

**Warehouse topology:** the nightly GitHub Actions job writes **Neon** (`eu-central-1`, source of
truth). Django runs locally and reads a **local Postgres replica** — reading Neon directly costs
~90 ms/query (seconds/page). `manage.py serve` calls `sync_if_stale()` first: one date query
against Neon, and a full truncate-and-reload (`scripts/sync_warehouse_from_neon.py`, ~60s, ~624k
rows) *only* when Neon's `ingestion_date` is newer than the replica's — a normal restart is
instant. `python -m scripts.sync_warehouse_from_neon [--if-stale]` runs it standalone (e.g. from
cron). `.env` locally: `DATABASE_URL` = local replica, `NEON_DATABASE_URL` = Neon (sync source
only). See `docs/architecture.md` §4.3.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

---

## Task Log

Full task history, current status, the phase map, and the backlog now live in
`tasks.md` (gitignored — local only, not checked into the repo).

**After finishing any task, in this order:**
1. Check off `[ ]` → `[x]` in the Task List in `tasks.md`.
2. Fill in that task's **Outcome** line (1–2 sentences: what now exists/works).
3. Update the Current Status block at the top of `tasks.md`.
4. Write the learning entry in `for_learning.md` (see rules below).
5. Commit: `git add -A && git commit -m "Task N: short description"`

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
├── vercel.json                 # hosting: fra1 region, ignoreCommand, bundle excludes
├── requirements.txt            # web runtime only (what the hosted function installs)
├── requirements-etl.txt        # the above + pipeline stack; install this locally
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

> **30 tables** on the live Neon warehouse (and the local replica) as of 2026-09-10. The **18
> movie-side tables** were verified 2026-09-06 against `information_schema` and a fresh scratch DB
> from `01`–`03` (they match table-for-table): 9 dimensions, 4 facts, 3 bridges, 1
> repeating-attribute (`person_alias`, Task 72), 1 operational (`etl_watermarks`). Task 74 added
> `dim_movie_video`; Task 76's live `run_refresh` populated it (~17.3k rows / ~1.2k films as of
> 2026-09-07, replace-loaded nightly). **Task 85 applied the 12 TV tables** — `dim_series`,
> `dim_network`, `dim_season`, `dim_episode`, `fact_series_credit`, `fact_series_rating`,
> `fact_episode_rating`, and `bridge_series_{genre,company,country,language,network}` — to **both**
> Neon and the replica (DDL `19`–`22`, also folded into `01`/`02`); their columns and indexes are
> documented in the `### Feature — TV Shows` block in `tasks.md` and each DDL file's header.
> Population runs with the first Task 85 `--with-tv` load. `dim_actor`, `dim_director`, `fact_cast`
> and `fact_crew` were dropped in Task 53; `fact_casting` was replaced in Task 35.
> `warehouse/ddl/01`–`03` bootstrap this schema; `04`–`22` are migrations for an existing DB (once
> `11` drops tables, "run every file in order" ≠ "build the current schema" — see README §2).

**Dimensions (9):**
- `dim_movie(movie_id PK, title, release_date, runtime, budget, revenue, original_language, status, overview, tagline, poster_path, backdrop_path, imdb_id, original_title, homepage, slug, collection_id FK)`
- `dim_person(person_id PK, name, gender, popularity, profile_path, known_for_department, slug, biography, birthday, deathday, place_of_birth, homepage, imdb_id)` — the last 6 from `GET /person/{id}` (Task 72), all nullable and sparse even among people with a photo. `imdb_id` has a non-unique index.
- `dim_genre(genre_id PK, genre_name)`
- `dim_collection(collection_id PK, name, poster_path, slug)`
- `dim_date(date_id PK, full_date, year, month, day, decade)`
- `dim_company(company_id PK, name, logo_path, origin_country, slug, description, headquarters, homepage, parent_company_id, parent_company_name)` — Task 58; the last 5 from `GET /company/{id}` (Task 65). `parent_company_id` has **no FK** (a holding-company parent frequently has no `dim_company` row) — soft reference, resolved at read time.
- `dim_country(country_code PK, name)` — Task 61, ISO code is the PK (no surrogate, no slug)
- `dim_language(language_code PK, name, english_name)` — Task 61, ISO code is the PK
- `dim_movie_video(movie_id FK, video_id, name, key, site, type, official, size, iso_639_1, iso_3166_1, published_at, ingestion_date)` — PK `(movie_id, video_id)` on TMDB's `video_id` (not `key`, unique only within a site); index `(movie_id, type)`. Task 74. A film's trailers/clips — a multi-valued attribute of `dim_movie`, so `dim_` (not `fact_` — `size` is a resolution, no measure; not `bridge_` — no `dim_video` to join to). **Loaded by REPLACE, not upsert**: `common._replace_by_parent()` deletes every row for the partition's `movie_id`s then re-inserts, so a film's video set can *shrink* when TMDB drops a video or a YouTube key rots.

**Facts (4):**
- `fact_movie_metrics(movie_id FK, date_id FK, genre_id FK, rating, vote_count, revenue, budget, popularity, ingestion_date)` — PK `(movie_id, date_id, genre_id)`, so a multi-genre film repeats its movie-level measures once per genre. Any query aggregating `revenue`/`popularity` must collapse it with `SELECT DISTINCT movie_id, …` first. **`rating`/`vote_count` have had no readers since Task 69** — every rating now comes from `fact_movie_rating`; the loader still writes them, a knowingly-retained write-only path (same posture as `dim_collection`).
- `fact_credit(movie_id FK, person_id FK, department, job, character_name, ordering, ingestion_date)` — PK `(movie_id, person_id, department, job)`, the grain TMDB publishes.
- `fact_collaboration(person_a_id FK, person_b_id FK, films_together, first_year, last_year)` — derived in Gold, `CHECK (person_a_id < person_b_id)`.
- `fact_movie_rating(movie_id FK, source, rating, vote_count, ingestion_date)` — PK `(movie_id, source)`, `CHECK (source IN ('imdb','tmdb'))`. Task 67. One row per film per source, so `AVG(rating)` needs no de-dup guard. IMDb (from the daily `title.ratings.tsv.gz` bulk file) is the rating of record on the site; TMDB kept for comparison.

**Bridges (3):** factless join tables — `bridge_` not `fact_` because they carry no measure, only that a relationship exists.
- `bridge_movie_company(movie_id FK, company_id FK, ingestion_date)` — PK `(movie_id, company_id)`. Task 58.
- `bridge_movie_country(movie_id FK, country_code FK, relation, ingestion_date)` — PK `(movie_id, country_code, relation)`; `relation ∈ {origin, production}` is in the key because the two disagree on ~23% of films. Task 61.
- `bridge_movie_language(movie_id FK, language_code FK, ingestion_date)` — PK `(movie_id, language_code)`. Task 61.

**Repeating-attribute (1):** neither `dim_`, `fact_` nor `bridge_` — it attaches one dimension's repeating text to it (doesn't join two dimensions, carries no measure).
- `person_alias(person_id FK, alias, ordering, ingestion_date)` — PK `(person_id, alias)`. Task 72; `also_known_as` from `GET /person/{id}`, which is a list and so can't be a `dim_person` column without breaking 1NF.

**Operational (1):** `etl_watermarks(loader_name PK, last_ingestion_date, updated_at)`

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
- **Never surface internal implementation names in the UI.** No table/column names (`dim_movie`, `fact_credit`, `fact_collaboration`, ...), no `.sql` filenames (`movies_by_decade.sql`), no raw surrogate keys (`movie.movie_id`), no query/script names — anywhere a user-facing template renders a caption, section-note, or label. These are pipeline/warehouse internals and mean nothing to a reader of the site. If a section needs a caption, describe what the section *shows* ("by decade", "release order", "connectivity"), not where the data came from internally.

---

## Additional Reference

Full design rationale and original architecture decisions: `docs/architecture.md`
Learning log (updated after every task): `for_learning.md`
