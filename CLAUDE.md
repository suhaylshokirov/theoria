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

## Current Status — UPDATE AFTER EVERY TASK

```
Last completed task   : **Task 71 — genre chips on the movie page link to the filtered index
(2026-08-30).** One template line and one test: `<span class="chip">` → `<a class="chip"
href="/movies/?genre={{ genre.genre_name|slugify }}">`. No view change and no `?sort=` in the
href — `/movies/` already defaults to newest-first, which is where a reader clicking a genre wants
to land. The slug comes from Django's built-in `slugify` **template filter**, which is the same
`django.utils.text.slugify` the view uses for its `{slug: genre_id}` map, so the two sides can't
drift (this is what `dim_genre` having no slug column costs, and it's cheap). **Zero new CSS**:
`.chip:hover` was written for links and had been stranded since the chips were demoted to `<span>`s
on 2026-08-14 when `/genres/` was removed — the cascade was checked, not assumed (`.chip`'s ink
colour outranks the global `a` lime; `.chip:hover` overrides `a:hover`'s underline by source
order). Live-verified: Godfather → `?genre=drama`/`?genre=crime`, Inception →
`?genre=science-fiction` (multi-word slug round-trips); each lands 200 with the `<select>`
preselected and results newest-first; `?genre=tv-movie` returns its 2 films. `pytest` **317**.
Full detail in the Task 71 block below.
Since then (ad-hoc, 2026-08-31): **the studio detail page now opens with the studio's logo** —
new `_studio_header.html` partial: the record (name + the four stats) leads, the logo sits to its
right as a brand mark. Reuses the `/studios/` grid card's `--logo-plate`/`contain`/monogram-
fallback treatment, incl. the fixed light plate that is never redefined under `[data-theme]` so
dark-on-transparent wordmarks stay legible in dark mode. Template + CSS only, no view change.
`pytest` **319**. Full detail in `for_learning.md`.
Prior task: **Task 70 — replaced the `/movies/` country filter with a genre filter
(2026-08-30).
Last updated          : 2026-08-31
```

**After finishing any task, in this order:**
1. Check off `[ ]` → `[x]` in the Task List below.
2. Fill in that task's **Outcome** line (1–2 sentences: what now exists/works).
3. Update the status block above.
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

> The live shape as of Tasks 63 + 69 — **16 tables** (verified 2026-08-30 against
> `information_schema`, and a fresh DB built from `01`–`03` produces exactly this set).
> `dim_actor`, `dim_director`, `fact_cast` and `fact_crew` were dropped in Task 53; `fact_casting`
> was replaced in Task 35. `warehouse/ddl/01`–`03` bootstrap this schema; `04`–`15` are migrations
> for an existing DB (once `11` drops tables, "run every file in order" ≠ "build the current
> schema" — see README §2).

**Dimensions (8):**
- `dim_movie(movie_id PK, title, release_date, runtime, budget, revenue, original_language, status, overview, tagline, poster_path, backdrop_path, imdb_id, original_title, homepage, slug, collection_id FK)`
- `dim_person(person_id PK, name, gender, popularity, profile_path, known_for_department, slug)`
- `dim_genre(genre_id PK, genre_name)`
- `dim_collection(collection_id PK, name, poster_path, slug)`
- `dim_date(date_id PK, full_date, year, month, day, decade)`
- `dim_company(company_id PK, name, logo_path, origin_country, slug, description, headquarters, homepage, parent_company_id, parent_company_name)` — Task 58; the last 5 from `GET /company/{id}` (Task 65). `parent_company_id` has **no FK** (a holding-company parent frequently has no `dim_company` row) — soft reference, resolved at read time.
- `dim_country(country_code PK, name)` — Task 61, ISO code is the PK (no surrogate, no slug)
- `dim_language(language_code PK, name, english_name)` — Task 61, ISO code is the PK

**Facts (4):**
- `fact_movie_metrics(movie_id FK, date_id FK, genre_id FK, rating, vote_count, revenue, budget, popularity, ingestion_date)` — PK `(movie_id, date_id, genre_id)`, so a multi-genre film repeats its movie-level measures once per genre. Any query aggregating `revenue`/`popularity` must collapse it with `SELECT DISTINCT movie_id, …` first. **`rating`/`vote_count` have had no readers since Task 69** — every rating now comes from `fact_movie_rating`; the loader still writes them, a knowingly-retained write-only path (same posture as `dim_collection`).
- `fact_credit(movie_id FK, person_id FK, department, job, character_name, ordering, ingestion_date)` — PK `(movie_id, person_id, department, job)`, the grain TMDB publishes.
- `fact_collaboration(person_a_id FK, person_b_id FK, films_together, first_year, last_year)` — derived in Gold, `CHECK (person_a_id < person_b_id)`.
- `fact_movie_rating(movie_id FK, source, rating, vote_count, ingestion_date)` — PK `(movie_id, source)`, `CHECK (source IN ('imdb','tmdb'))`. Task 67. One row per film per source, so `AVG(rating)` needs no de-dup guard. IMDb (from the daily `title.ratings.tsv.gz` bulk file) is the rating of record on the site; TMDB kept for comparison.

**Bridges (3):** factless join tables — `bridge_` not `fact_` because they carry no measure, only that a relationship exists.
- `bridge_movie_company(movie_id FK, company_id FK, ingestion_date)` — PK `(movie_id, company_id)`. Task 58.
- `bridge_movie_country(movie_id FK, country_code FK, relation, ingestion_date)` — PK `(movie_id, country_code, relation)`; `relation ∈ {origin, production}` is in the key because the two disagree on ~23% of films. Task 61.
- `bridge_movie_language(movie_id FK, language_code FK, ingestion_date)` — PK `(movie_id, language_code)`. Task 61.

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

## Phase Map

| Phase | Name                   | Tasks  | Status      |
|-------|------------------------|--------|-------------|
| 1     | TMDB Ingestion (Bronze) | 1–8   | Complete |
| 2     | Data Lake (Silver/Gold) | 9–14  | Complete |
| 3     | Warehouse Modeling      | 15–21 | Complete |
| 4     | SQL Analytics           | 22    | Complete |
| 5     | Django UI               | 23–30 | Complete |
| 6     | Polish                  | 31–33 | Complete |
| 7     | Product Upgrade         | 34–39 | Complete |
| 8     | Correctness & Catalog Depth | 40–44 | Complete (43 deferred) |
| 9     | Frontend Polish & URL Design | 45–46 | Complete |
| 10    | People, Partnerships & Franchises | 47–53 | Complete |
| 11    | Movie Page Legibility  | 54     | Complete |
| 12    | Movie Provenance — the scalar fields | 55–56 | Complete |
| 13    | Studios — `dim_company` + the first bridge table | 57–60 | Complete |
| 14    | Where and in What Language | 61–63 | Complete |
| 15    | IMDb becomes the rating of record | 66–69 | Complete |

---

## Task List

> Work top to bottom. Don't skip ahead — each phase depends on data the previous one produced.


> Full plan: `~/.claude/plans/what-else-can-we-lazy-unicorn.md`. Theme: the pipeline runs
> clean and every check passes, which was hiding the fact that the warehouse was silently
> discarding data it had already ingested. Fix the silent losses first, then grow the catalog.
--

### Phase 12 — Movie Provenance: the scalar fields

> **Origin of Phases 12–14:** an audit on 2026-08-10 of every field in the Bronze payloads
> against every reader in the app. The pipeline has been ingesting these fields since Task 42
> and dropping them at `_flatten_movie()` — they have never existed at any layer past Bronze.
> **Zero new TMDB calls for any task in Phases 12–14**; everything rebuilds from immutable Bronze.
> Measured on the 1,140-file `2026-07-29` partition:
>
> | Bronze field | Coverage | Shape |
> |---|---|---|
> | `production_companies` | 99.8% | 1,243 studios, 3,200 links (2.81/film), 45% have a logo |
> | `production_countries` | 99.9% | 40 countries, 1,506 links, 295 films multi-country |
> | `spoken_languages` | 99.6% | 70 languages, 2,013 links |
> | `origin_country` | 99.9% | list; >1 entry on 63 films; **differs from `production_countries` on 259 films (22.7%)** |
> | `imdb_id` | 100% | `tt0119654` |
> | `original_title` | 100% | **differs from `title` on 78 films (6.8%)** |
> | `homepage` | 58.2% | official site URL |
> | `adult`, `video`, `softcore` | 0% | always null/false — **deliberately out of scope, they carry nothing** |
>
> Deliberately **not** in scope: retiring the 4 write-only Gold datasets, trimming `dim_date`'s
> 48,526 unreferenced rows, deleting the stale `silver/actors/` + `silver/directors/` S3
> prefixes, and the 3 `fact_movie_metrics` ↔ `dim_movie` revenue mismatches from an older
> partition. All real, all cleanup rather than capability; logged here so they aren't re-discovered.

#### [x] Task 55 — Carry `imdb_id`, `original_title` and `homepage` into `dim_movie`
- **Goal:** Three scalar fields present in 100%/100%/58.2% of Bronze payloads that stop at `_flatten_movie()`. The cheapest item in the whole audit: no new table, no bridge, no grain question.
- **Files:** `etl/silver/transform_movies.py`, `warehouse/ddl/01_dimensions.sql` + new `12_add_movie_identifiers.sql`, `etl/warehouse_loader/load_dimensions.py`, `data_quality/silver_checks.py`, `tests/{test_etl,test_data_quality}.py`
- **Steps:**
  1. `_flatten_movie()` keeps `imdb_id`, `original_title`, `homepage`. TMDB returns `""` for a missing homepage and imdb_id — normalise to `None` with `or None`, same as the Task 36 image fields.
  2. `12_add_movie_identifiers.sql`: idempotent `ADD COLUMN IF NOT EXISTS imdb_id VARCHAR(20)`, `original_title TEXT`, `homepage TEXT` on `dim_movie`; add the same three to `01_dimensions.sql` for a fresh bootstrap. Add a **non-unique** index on `imdb_id` — it's an external lookup key, but nothing guarantees TMDB never repeats one.
  3. Extend `load_dim_movie()`'s explicit column list. **This is the whole reason the column has to be added in two places** — per the Task 41 lesson, a column the loader doesn't name is indistinguishable from a column that doesn't exist, and fails silently either way.
  4. Add the three to `silver_checks.ENTITY_CONFIGS["movies"]["expected_cols"]`. `original_title` is `required` (100% coverage); the other two are not.
  5. Backfill by **re-running `load_dimensions()`** for all three partitions after rebuilding Silver — never an ad-hoc `UPDATE`.
- **Verify:** `imdb_id` non-null on ~1,215/1,215; `original_title` differs from `title` on ~78; `homepage` populated on ~58%.
- **Outcome:** `_flatten_movie()` now keeps `imdb_id`, `original_title`, `homepage`, normalising TMDB's `""` to `None` for the two nullable fields (`original_title` is always populated). New `12_add_movie_identifiers.sql` (idempotent `ADD COLUMN IF NOT EXISTS` + a **non-unique** index on `imdb_id`, since nothing guarantees TMDB never repeats one) applied to the live DB; the same three columns added to `01_dimensions.sql` for a fresh bootstrap. `load_dim_movie()`'s explicit column list extended — the Task 41 lesson (an unnamed column is silently indistinguishable from a missing one) held again by construction this time, not discovered after the fact. `silver_checks.ENTITY_CONFIGS["movies"]` gained all three in `expected_cols`, with `original_title` also added to `required_cols` (100% Bronze coverage; `imdb_id`/`homepage` are not, so they stay optional). Backfilled by **rebuilding Silver from immutable Bronze** for all three partitions (`2026-07-06`, `2026-07-09`, `2026-07-29` — Silver DQ 4/4 on each) and then re-running `load_dimensions()` for each, never an ad-hoc `UPDATE`. **Live-verified:** `dim_movie` 1,215 rows, `imdb_id` non-null on 1,213/1,215 (2 genuinely absent in Bronze), `original_title` differs from `title` on 101 films (the 3-partition total superset of the audit's ~78-on-1,140 figure), `homepage` populated on 699/1,215 (57.5%, matching the audit's 58.2%); *The Godfather* shows `imdb_id=tt0068646`, `homepage=http://www.thegodfather.com/`. Warehouse checks 20/20 on the `2026-07-29` partition. No DDL for any other table, no fact reload, no new TMDB calls. Tests 204 → 205 (1 new: `test_flatten_movie_carries_identifier_fields`; existing fixtures/assertions in `test_etl.py`/`test_data_quality.py` extended to include the three new columns rather than left stale).

#### [x] Task 56 — Surface identifiers and the original title on the movie page
- **Goal:** Make the three new columns visible without cluttering a page Task 54 just finished making legible.
- **Files:** `django_app/movies/models.py`, `movies/templates/movies/movie_detail.html`, `static/css/theoria.css`, `tests/test_django_views.py`
- **Steps:**
  1. Three `TextField(null=True)` on the `Movie` model (unmanaged, as always).
  2. **`original_title` renders only when it differs from `title`** — on 93% of films it's the same string, and printing it twice is noise, not data. Guard in the template, not the view.
  3. IMDb and homepage as outbound links in the existing record list. External links need `rel="noopener noreferrer"` and a visible marker that they leave the site.
  4. **Do not print the raw `imdb_id` string as a label** — per the UI rule, link it as "IMDb" and let the href carry the id.
- **Outcome:** Three `TextField(null=True)` added to the unmanaged `Movie` model. `original_title`
  renders in a new `.specimen-original-title` line directly under the page title, guarded by
  `{% if movie.original_title and movie.original_title != movie.title %}` in the template — a
  view-level guard was rejected since "is this noise" is a rendering decision, not a data-shape
  one. IMDb and homepage render as a single "Elsewhere" record row (both links, `·`-separated,
  either optional) rather than two near-empty rows, since ~42% of films have no homepage and a
  row with one dash and one link reads worse than one combined row that just omits the missing
  half. Both links carry `target="_blank" rel="noopener noreferrer"` and a new `.ext-link::after`
  CSS rule appends a visible `↗` — `target="_blank"` alone gives no visual cue a link leaves the
  site. The `imdb_id` value only ever appears inside an `href`, never as page text, per the
  no-raw-internals-in-the-UI rule (loosely applied here too, even though `imdb_id` is an external
  TMDB/IMDb identifier rather than a warehouse internal — printing the bare `tt0068646` string
  next to a link is redundant with the link text). Read-side only: no DDL, no ETL, no pipeline
  re-run. Live-verified: `/movies/the-godfather/` renders `Elsewhere → IMDb ↗ · Official site ↗`
  linking to `imdb.com/title/tt0068646/` and `thegodfather.com`; `/movies/warriors-of-the-wind/`
  renders `originally "風の谷のナウシカ"` (non-Latin original titles round-trip through the
  template correctly); a film where `original_title == title` (e.g. Inception) shows neither line.
  Tests 205 → 207 (2 new: renders-when-differs-with-both-links, hidden-when-same-and-both-null).

---

### Phase 13 — Studios: `dim_company` and the project's first bridge table

> The standout finding of the audit. Unlike everything else in Phase 12, a production company
> is a genuinely new **entity** with its own identity, artwork and page — and unlike
> `dim_collection` (one collection per film, so it flattens to a column on `dim_movie`), a film
> has 2.81 companies on average. That's a true many-to-many, which this warehouse has never
> modelled: genres are currently handled by fanning `fact_movie_metrics` out to one row per
> genre, the wart every analytics query has to `SELECT DISTINCT` around. **Phase 13 is where
> the project learns the bridge-table pattern properly**, and Phase 14 applies it twice more.

#### [x] Task 57 — Silver: `transform_movie_links.py` — companies, countries and languages
- **Goal:** One new Silver module emitting all three nested arrays as tidy long tables. Companies are only *used* in Phase 13; countries and languages are extracted here too because they come from the same Bronze pass and re-reading 1,140 S3 objects a second time to fetch them later would be wasted work.
- **Files:** new `etl/silver/transform_movie_links.py`, `data_quality/silver_checks.py`, `scripts/run_pipeline.py`, `tests/{test_etl,test_data_quality}.py`
- **Steps:**
  1. New module mirroring `transform_credits_bridge.py`'s relationship to `transform_people.py` — a bridge module doing its own Bronze pass, rather than widening `transform_movies` to return four URIs. Consistent with the existing pattern; the cost is one extra pass over `bronze/movie_details`, and the alternative breaks the one-module-one-responsibility rule.
  2. Three outputs, all **denormalised long tables** (the link plus the entity's attributes on every row) so each dimension can be derived with `drop_duplicates` at load time — exactly the `load_dim_collection()` pattern:
     - `silver/movie_companies/movie_companies.parquet` — `(movie_id, company_id, company_name, logo_path, origin_country)`
     - `silver/movie_countries/movie_countries.parquet` — `(movie_id, country_code, country_name, relation)`
     - `silver/movie_languages/movie_languages.parquet` — `(movie_id, language_code, language_name, english_name)`
  3. **`relation` on the country table is the grain decision of this phase.** `origin_country` and `production_countries` are two different relationships that disagree on 259 of 1,140 films, so they cannot be merged into one row set without losing which is which; `relation ∈ {"origin", "production"}` records the relationship instead of asserting the two are the same fact. Note in the docstring that the alternative — one row per `(movie_id, country_code)` with `is_origin`/`is_production` booleans — was rejected as it makes the row mean two things at once.
  4. Dedup keys, each matching its true grain: `(movie_id, company_id)`, `(movie_id, country_code, relation)`, `(movie_id, language_code)`. Per Task 40, do not widen a key past the grain to "be safe".
  5. Drop null-id rows with a warning; never crash.
  6. Three new `ENTITY_CONFIGS` entries in `silver_checks.py`, **written from the TMDB payload shape** rather than by mirroring the transform — a check that copies the transform's assumptions confirms bugs instead of catching them (the Task 40 lesson).
  7. Wire into `run_pipeline.py` after `transform_movies`.
- **Verify:** on `2026-07-29` — ~3,200 company links / 1,243 distinct companies; ~1,506 production + ~1,200 origin country links; ~2,013 language links.
- **Outcome:** New `etl/silver/transform_movie_links.py` does its own pass over `bronze/movie_details`
  (same source `transform_movies.py` reads, per the docstring's stated tradeoff — one extra Bronze
  pass rather than widening `transform_movies` to return four URIs) and writes the three
  denormalised long tables exactly as scoped. A shared `_write_link_table()` helper runs the
  cast→dedupe→drop-null-ids→write pipeline once for all three, parameterised by dedup subset and
  required id columns — the three tables are similar enough that three copies of that logic would
  have been the premature-duplication smell, not the premature-abstraction one. `relation` folded
  into `movie_countries`' dedup key and PK as scoped; an origin row's `country_name` is backfilled
  from a same-payload `production_countries` row when the code matches, else left null (never
  guessed across movies). `ENTITY_CONFIGS` gained `movie_companies`/`movie_countries`/`movie_languages`,
  written from the raw TMDB array shapes. Wired into `run_pipeline.py` right after
  `transform_credits_bridge`. **Live-verified on `2026-07-29`**: 3,200 company rows / **1,243**
  distinct `company_id`s, country rows split **1,506 production + 1,211 origin** (17 origin rows
  left with a null name, no false match forced), **2,013** language rows across **70** distinct
  codes — every figure matches the Phase 12 audit table. Silver DQ 16/16 → **28/28**. No warehouse
  or Django change — this task is Silver-only by design; Task 58 reads these three files. Tests
  207 → 217 (9 new in `test_etl.py` covering extraction, backfill-or-null, true-grain dedup, and
  null-id dropping; 1 new in `test_data_quality.py` regression-testing that a null `relation`
  fails the nulls check; `_all_entity_dfs()` extended with fixtures for the three new entities).

#### [x] Task 58 — Warehouse: `dim_company` + `bridge_movie_company`
- **Goal:** The dimension and the bridge, loaded, indexed, checked and backfilled.
- **Files:** new `warehouse/ddl/13_companies.sql`, `warehouse/ddl/01_dimensions.sql`, `etl/warehouse_loader/load_dimensions.py`, `etl/warehouse_loader/load_facts.py`, `data_quality/warehouse_checks.py`, `tests/{test_etl,test_warehouse_checks}.py`
- **Steps:**
  1. `dim_company(company_id PK, name, logo_path, origin_country, slug)` + unique index on `slug`. `origin_country` is populated for 1,070/1,243 companies (86%) — nullable.
  2. `bridge_movie_company(movie_id FK, company_id FK, ingestion_date)`, PK `(movie_id, company_id)`, index on **both** FKs (the join runs in both directions: a film's studios, and a studio's films). Carries `ingestion_date` for audit/traceability like the other facts.
  3. **Naming:** `bridge_` rather than `fact_` is deliberate and should be written down — this is a *factless* fact table, recording that a relationship exists with no measure attached. Reserving `fact_` for tables with measures keeps the schema self-describing.
  4. `load_dim_company()` in `load_dimensions.py`, deriving the dimension from the Silver bridge via `drop_duplicates(company_id)`, filtering on id **and** name (`name NOT NULL`), then `assign_slugs(session, "dim_company", "company_id", "name")` — reusing Task 46's whole-table recompute, which already handles collisions and permutations (the Task 48 `UniqueViolation` fix).
  5. `load_bridge_movie_company()` in `load_facts.py`, resolving both FKs against the live dimensions and **quarantining** unresolvable rows to `data_quality/rejected/`, never dropping them. Must run after `load_dim_company()` and `load_dim_movie()`.
  6. Two new `_FK_CHECKS` entries in `warehouse_checks.py`, plus a load-sanity check for the bridge.
  7. Backfill all three partitions.
- **Verify:** ~1,243 companies, 0 null slugs, ~3,200 bridge rows, 0 rejects; Warner Bros. ≈ 128 films.
- **Outcome:** Built exactly as scoped. `13_companies.sql` (also folded into `01_dimensions.sql`)
  creates both tables with the FK/index shape specified; applied live before any loader ran.
  `load_dim_company()` is a near-literal copy of `load_dim_collection()`'s shape — distinct-by-id,
  filter on id and name, then `assign_slugs()` — which is the point: the pattern from Task 50 held
  up unchanged for a second dimension derived from a link table. `load_bridge_movie_company()`
  resolves FKs against `dim_movie`/`dim_company` and quarantines misses, wired into `load_facts()`
  after both dimension loads complete. The row-count-sanity check needed one real deviation from
  the existing per-entity pattern: `movie_companies` is a link table, so comparing its raw Silver
  row count against `dim_company`'s cumulative row count would fail for every popular studio (128
  Silver rows for Warner Bros. alone vs. 1 warehouse row) — fixed by comparing against
  `nunique(company_id)` instead, caught by writing the check before assuming the existing
  `_check_entity_counts` helper would just work. **Backfilled and live-verified across all three
  partitions**: `dim_company` **1,383** rows total, **0** null slugs; `bridge_movie_company`
  **3,409** rows, **0** rejects; Warner Bros. Pictures → **128 films**, exactly matching the
  estimate. Warehouse checks 20/20 → **25/25**. Tests 217 → 225 (8 new, incl. a regression test
  naming the distinct-vs-row-count fix so it can't silently regress).

#### [x] Task 59 — Django: `/studios/` and the studio page
- **Goal:** Give the new entity its pages, and put a film's studios on the movie page.
- **Files:** `django_app/movies/{models,views,urls}.py`, new `movies/templates/movies/{studio_list,studio_detail}.html`, `movie_detail.html`, `templates/base.html`, `tests/test_django_views.py`
- **Steps:**
  1. `Company` + `MovieCompany` models (`managed = False`; the bridge gets the same fake-single-PK treatment as the other composite-PK facts, with the comment explaining why).
  2. `/studios/` — a ranked sheet reusing `table-2col` + `data-meter` share bars from **`genre_list.html`**, plus the shared `_pager.html`. **No new CSS or JS** — Task 38 established this page shape. `.annotate(Count).filter(film_count__gt=0)` so the filter compiles to `HAVING`. (The `/franchises/` pages copied this same shape and would have been the closer model, but they were **removed on 2026-08-10** — see the status block. `genre_list.html` is the surviving original.)
  3. `/studios/<slug>/` — films in release order (reuse `_movie_card.html`), film count, span, avg rating, total revenue. Avg rating **must** collapse `fact_movie_metrics` with `.values().distinct()` first; revenue sums straight off `dim_movie` (one row per film). Two aggregates on one page, only one needing the genre-fanout guard — get this wrong and the revenue figure silently multiplies by the film's genre count.
  4. Studios on the movie page, linked, in the existing record list.
  5. Nav entry. **Check the nav isn't getting crowded** — Task 51 already collapsed Actors+Directors into People for this reason.
- **Verify:** all routes 200, bad slug 404s, a known studio page renders the expected film count.
- **Outcome:** Built as scoped, with one necessary deviation: `genre_list.html`, the plan's named
  page-shape reference, had been deleted four days before this task ran (the genre-browsing UI was
  removed on 2026-08-14 when the Analytics dashboard was cut to 2 panels — see the Phase 11 status
  entry). The underlying CSS/JS it was built from (`.table-2col`, `[data-meter]`, `initMeters()`)
  was untouched by that removal, so `studio_list.html` reuses those primitives directly in a fresh
  template rather than copying a file that no longer exists — same shape, same "no new CSS or JS"
  guarantee, different starting point. `Company`/`MovieCompany` added as scoped; `MovieCompany` is
  an explicit model rather than a `ManyToManyField(through=...)`, since that Django field expects
  to own and generate its own join table and `bridge_movie_company` already exists, fully owned by
  `13_companies.sql`. `studio_detail`'s two aggregates follow the genre-fanout rule exactly:
  `.values("movie_id","rating").distinct()` before `Avg()`, plain `Sum("revenue")` off `dim_movie` —
  getting either backwards was the concrete failure mode the plan warned about, and writing the
  view surfaced it as a real thing to get right, not just a warning to read past. Nav gained
  "Studios" between People and Analytics (4 items, not crowded). **Live-verified**: all 10 routes
  200 incl. `/studios/?page=2`, bad slug 404s; `/studios/` ranks Warner Bros. Pictures first at
  **128 films** (exactly matching Task 58's warehouse figure); `/studios/warner-bros-pictures/`
  shows **128 / ★7.24 / 1971–2025 / $45,341,167,063**; *The Godfather* links all three of its
  studios. Tests 225 → 229 (4 new, plus the 10 existing `movie_detail` tests updated to mock the
  view's new `MovieCompany` query).

#### [x] Task 60 — Analytics: studio panels
- **Goal:** Spend the new dimension on the dashboard.
- **Files:** new `warehouse/queries/{studio_output_by_decade,top_studios_by_revenue}.sql`, `django_app/analytics/{views.py,templates/analytics/dashboard.html}`
- **Steps:**
  1. Two panels: studio output by decade, and top studios by revenue + avg rating (with a minimum film count so a studio with one hit doesn't top the table — the same shape as `top_rated_directors.sql`'s ≥3-film floor).
  2. **Every query carries an explicit `LIMIT`** (Task 42's lesson — two unbounded queries once returned 1,304 rows into a fixed-height panel) and the `SELECT DISTINCT movie_id` CTE wherever it touches `fact_movie_metrics`.
  3. Time each query; the current dashboard's slowest panel is under 0.5s and these join one more table than any existing panel.
- **Outcome:** Both queries added exactly as scoped, with a real narrowing decision on the first:
  a literal (studio × decade) crosstab would be either 1,383 columns or thousands of rows, so
  `studio_output_by_decade.sql` instead surfaces the leading studio per decade via `RANK() OVER
  (PARTITION BY decade ...)`, staying one row per decade — the query's own comment records this as
  a deliberate scope choice, not a shortcut taken silently. Both queries carry an explicit `LIMIT`
  (20, added to the decade query even though it's naturally bounded to ~10 rows — cheap insurance
  matching the literal rule rather than relying on "this one happens to be safe"). `top_studios_by_revenue`
  applies the genre-fanout guard on only one of its two aggregates and says so in a comment: `SUM(revenue)`
  is a plain sum off `dim_movie` (one row per film), while `rating` is de-duplicated to one row per
  movie via a `movie_ratings` CTE before `AVG()` — the same fork Task 59's `studio_detail` view had
  to get right, now named explicitly so a future reader doesn't have to re-derive why the two
  aggregates look asymmetric. **A real bug caught before shipping**: the first draft linked each
  studio by `row.company_id`, which matches the `<slug:...>` URL converter syntactically but
  resolves to nothing (a numeric string never equals a text slug), silently 404ing every link —
  found by actually clicking a live-rendered row, not by reading the template. Fixed by adding
  `c.slug AS studio_slug` to the query. Also dropped the dashboard's `eyebrow`/`accession` params
  to `_sheet_header.html`, dead since Task 45 removed that kicker line from the template itself
  but never cleaned out of this call site. **Live-verified**: both queries run in **8.7ms and
  11.4ms** against the full warehouse, full `/analytics/` response **116ms**; dashboard is now
  4 panels; `studio_output_by_decade` shows United Artists → Paramount → Universal leading
  successive decades; `top_studios_by_revenue` ranks Warner Bros. Pictures first (128 films,
  $45.3B, ★7.24) and every studio link resolves 200. Tests: the existing dashboard context test
  extended with the two new panels' fixtures rather than duplicated (229 → 229, no net new test
  needed since one assertion-rich test already covers the whole view).

---

### Phase 14 — Where and in What Language

> Phase 13's bridge pattern, applied twice to Silver data that Task 57 already wrote. Cheap by
> design: if Phase 13 was built well, this phase is mostly configuration. If it turns out
> expensive, that's a signal Task 58 hardcoded something that should have been shared.

#### [x] Task 61 — Warehouse: `dim_country`, `dim_language` and their bridges
- **Files:** new `warehouse/ddl/14_countries_languages.sql`, `warehouse/ddl/01_dimensions.sql`, `etl/warehouse_loader/{common,load_dimensions,load_facts}.py`, `data_quality/warehouse_checks.py`, `tests/{test_etl,test_warehouse_checks}.py`
- **Outcome:** Built exactly as scoped, following Task 58's bridge pattern for a second and third
  time. `dim_country(country_code PK, name)` and `dim_language(language_code PK, name,
  english_name)` use their ISO code directly as the primary key — no surrogate id, no slug, no
  `assign_slugs()` call, since the code is already short, stable and URL-safe. `load_dim_country()`
  and `load_dim_language()` mirror `load_dim_company()`'s distinct-by-id, filter-on-id-and-name
  shape exactly. `bridge_movie_country(movie_id, country_code, relation, ingestion_date)` carries
  `relation` inside its own PK — the grain decision Task 57 already made in Silver, carried through
  unchanged, since `origin` and `production` are two simultaneously-true relationships to the same
  country and folding `relation` out of the key would let one silently overwrite the other on
  upsert. New `_existing_str_ids()` in `etl/warehouse_loader/common.py` (extracted alongside
  `_existing_ids()`, which `int()`-casts and would break on a string PK) resolves both new bridges'
  FKs. Two new `_FK_CHECKS` pairs plus distinct-count row-count-sanity checks (reusing the
  Task 58 `nunique()` pattern — `movie_countries`/`movie_languages` are link tables too) plus two
  new fact-load-sanity checks in `warehouse_checks.py`. Silver's `relation`-null-name origin rows
  (17 on `2026-07-29`, Task 57) correctly get no `dim_country` row and their bridge rows are
  quarantined via the ordinary unresolvable-FK path, not special-cased. **Live-verified across all
  three backfilled partitions**: `dim_country` 46 rows (40 named on the `2026-07-29` partition
  alone, matching the audit), `dim_language` 73 rows (70 on `2026-07-29`); `bridge_movie_country`
  2,907 rows / `bridge_movie_language` 2,113 rows cumulative, **0 rejects on any partition**;
  *The Godfather* shows `production=USA, origin=USA` (agreeing) and languages
  English/Italiano/Latin; 278 films (of 1,215) have a origin/production country disagreement,
  matching Task 57's ~23% figure. Warehouse checks 25/25 → **35/35** (4 new FK checks + 4 new
  row-count checks + 2 new fact-load-sanity checks). No Django/UI change yet — Task 62 reads these
  tables. Tests 229 → 238 (9 new: dimension dedup/null-filter for both, bridge builder FK
  resolution + relation-passthrough for countries, bridge builder FK resolution for languages, and
  the countries/languages distinct-vs-row-count row-count-sanity regression).

#### [x] Task 62 — Django: provenance on the movie page, and browse by country/language
- **Goal:** Surface both without inventing two more entity pages nobody asked for.
- **Files:** `django_app/movies/{models,views}.py`, `movies/templates/movies/{movie_detail,movie_list}.html`, `tests/test_django_views.py`
- **Steps:**
  1. Countries and languages on the movie page, in the record list. **Render the origin/production distinction only when they disagree** (22.7% of films) — on the other 77% two identical country lists is noise. This is the same judgment as Task 56's `original_title`.
  2. `/movies/?country=` and `?language=` filters on the existing list page, alongside `?q=` and `?sort=`. **Filters, not detail pages** — a country is a facet of a film, not a thing with a biography, and `/movies/?country=JP` answers the real question ("what Japanese films are here") with no new template.
  3. The filter must survive pagination: `_pager.html` already takes a `base_query`, and `movie_list` already builds one with `urlencode` — extend it, don't rebuild it.
  4. `dim_movie.original_language` already exists and is already shown on the movie page (16 distinct values). **Reconcile it with `spoken_languages` rather than shipping two language facts side by side** with no explanation of how they differ.
- **Outcome:** Built exactly as scoped — see the detailed write-up under "Blockers / open issues"
  above for the full design rationale and live-verification numbers. Four new `managed = False`
  models (`Country`, `Language`, `MovieCountry`, `MovieLanguage`) mirror the existing bridge-model
  shape. `movie_detail` gained `_country_provenance()` (one "Countries" row when origin/production
  agree, split rows only when they disagree) and `_movie_languages()` (merges
  `original_language` with the spoken-languages bridge into one ordered, deduplicated,
  name-resolved list). `/movies/` gained `?country=`/`?language=` `<select>` filters, matching
  either relation for country, with `base_query` extended so both survive pagination. No DDL/ETL
  change. Tests 243 → 250.

#### [x] Task 63 — Analytics, live re-run, verification, doc truth-up
- **Goal:** The phase-closing task, following Tasks 44 and 53.
- **Files:** new `warehouse/queries/*.sql`, `analytics/{views.py,dashboard.html}`, `README.md`, `docs/architecture.md`, `CLAUDE.md`, `for_learning.md`
- **Steps:**
  1. Panels for films by country of production and non-English cinema over time.
  2. **Full live pipeline re-run across all three partitions** — the first end-to-end run since the new Silver entities existed, so it's the first proof `run_pipeline.py` sequences them correctly.
  3. Silver DQ and warehouse checks: both counts **rise** this phase (new entities, new FK checks). Record the new numbers so a future reader doesn't misread the change.
  4. Walk all routes live, including the new studio pages and both new filters.
  5. **Verify a fresh install empirically, not by reading the README** — a throwaway DB built from DDL `01`–`03` must produce exactly the live table list, per the Task 53 lesson that "run every DDL file in order" stopped being the same instruction as "build the current schema" once migration `11` dropped tables.
  6. Update `docs/architecture.md` with the bridge-table decision (why `bridge_` not `fact_`, and why a bridge is right for companies where a column was right for collections), and this file's Warehouse Schema section with the final table list.
- **Outcome (2026-08-30, merged with Task 69 — one commit, the Task 66–67 "landed together"
  precedent, because the two share this close-out too tightly to split without faking it):**
  Shipped two new panels on `/analytics/`: **"Films by production country"** (ranked table off
  `bridge_movie_country WHERE relation='production'` → `dim_country`, with an IMDb-sourced avg
  rating; US 1,050 / UK 202 / France 74 / Germany 52 / Japan 35 …) and **"Non-English cinema over
  time"** (Chart.js line + table, `dim_movie.original_language <> 'en'` share by decade — 1970s
  **24.8%** → 2010s **2.0%**; NULL language counted as unknown, not non-English). Both queries
  carry an explicit `LIMIT` per the Task 42/60 rule even though naturally bounded; timed
  **16.1 ms** / **6.6 ms** against the replica. `analytics.js` gained a third line chart;
  `analytics.css` untouched (its "only adds, never restyles" contract). **Same-day ad-hoc
  follow-up (user request):** "Non-English cinema over time" was removed again, together with the
  older "Studio output by decade" panel (Task 60) — app-layer only (query files kept, per the
  Franchises/9→2 precedent), the language line chart dropped from `analytics.js`. `/analytics/`
  now stands at **4 panels**: Rating by decade, Revenue by genre, Top studios by revenue, Films by
  production country. **The "full live re-run" step was
  deliberately skipped** — see the Task 69 outcome: the Task 64 nightly cloud path had already
  brought all 1,211 rateable films' IMDb ratings and the Phase 13–14 bridge data into one current
  partition (`2026-08-29`), so a hand re-run would only re-prove `nightly-refresh` runs 3–4.
  **Fresh-install check: a scratch DB built from `01`–`03` produced exactly the live 16-table
  set**, byte-for-byte (the bootstrap DDL was kept current at Tasks 58/61/67). Silver DQ **32/32**
  and warehouse checks **39/39** against `2026-08-29` — **unchanged** (this task added no Silver
  entities and no checks; the rise the step anticipated already happened at Tasks 61 and 66–67).
  All routes walked live: `/`, `/movies/`, a film, `/people/`, a person, `/studios/`, a studio,
  `/analytics/`, `/movies/?country=JP`, `?language=ja`, `?sort=rating` all 200; `/actors/<slug>/`
  301; bad slug 404. Docs trued up: `docs/architecture.md` gained **§3.7** (bridge-table decision)
  and **§3.8** (`fact_movie_rating` — bulk file vs API, long table vs two columns, licensing) plus
  an updated §3 intro; `README.md`'s "warehouse, as it stands" table (16 tables, live figures) and
  star-schema section; this file's Warehouse Schema section (9 → 16 tables) and Phase Map. Tests
  **298/298** (the one dashboard test extended with the two new panels' fixtures, not duplicated —
  Task 60 precedent; net 0 new tests).

---

### Phase 15 — IMDb becomes the rating of record

> Full plan: `~/.claude/plans/serene-herding-wadler.md`. Raised by user request on 2026-08-26:
> every rating the site shows is TMDB's `vote_average`, and the user wanted the number a reader
> sees to come from IMDb instead, with IMDb's own mark beside it so the source is visible rather
> than implied. **Kinopoisk was researched and then dropped by user decision** ("we don't need to
> touch Kinopoisk at all") — the research is recorded in the plan file so it isn't redone: its
> free tier is 200 req/day via `api.poiskkino.dev` (the old `api.kinopoisk.dev` 301s there now),
> and `/v1.4/movie` accepts array filters with `limit` max 250, so the whole catalog would have
> batched into ~5–13 calls. Not needed; noted in case it ever is.
>
> **The decisive research finding:** IMDb publishes `title.ratings.tsv.gz` at `datasets.imdbws.com`
> — 8.6 MB, refreshed daily, **no auth, no key, no quota**, three columns
> (`tconst`/`averageRating`/`numVotes`). We have carried `imdb_id` since Task 55, so the whole
> feature is one HTTP GET and a join: **zero per-movie API calls, no new secret, no new Python
> dependency**. Measured live before any code: **1,211 of 1,215 films match (99.7%)** — the 4
> misses are 2 films with no `imdb_id` and 2 whose id is below IMDb's ≥5-vote publication floor.
> This makes the ratings path *cheaper* than the TMDB one it replaces.

#### [x] Task 66 — Bronze + Silver: the IMDb ratings dataset
- **Files:** new `etl/bronze/ingest_imdb_ratings.py`, new `etl/silver/transform_imdb_ratings.py`, `etl/s3_utils.py`, `config.py`, `.env.example`, `data_quality/silver_checks.py`, `scripts/run_pipeline.py`, `tests/{test_etl,test_data_quality}.py`
- **Outcome:** `ingest_imdb_ratings()` is the project's **first non-TMDB Bronze source and its first
  bulk-file one** — every other ingest module writes one JSON per entity id because it calls a
  per-entity API, whereas here a single daily snapshot *is* the raw response, so one file per
  partition is the faithful representation rather than a shortcut. It uses `requests` directly with
  its own retry loop mirroring `tmdb_client.py`'s posture (no `TMDBClient` — there is no API key to
  inject), and stores the gzip **verbatim** via a new `s3_utils.write_bytes()`, the module's third
  writer alongside `write_json`/`write_parquet`. `IMDB_RATINGS_URL` is declared with `_optional()`
  and the real URL as its default, so the location is configurable but **nothing new becomes
  *required* in `.env`**. `transform_imdb_ratings()` is the **first Silver transform that joins two
  Silver inputs** — the Bronze snapshot against that partition's own `movies.parquet`, on `imdb_id`
  — deliberately *not* against `dim_movie`, since Silver reading the warehouse would be a layer
  inversion and would make the transform's output depend on load order rather than only on
  immutable upstream data. Resolving `imdb_id → movie_id` in Silver (not the loader) keeps the
  loader's job identical to every other one. The filter is what earns its keep: the raw file is
  1,709,992 titles, overwhelmingly TV episodes, so shipping it to Silver untouched would be
  99.9% waste. Films drop out of the join for two reasons, both logged and neither an error — no
  `imdb_id` at all, or an id below IMDb's publication vote floor. `ENTITY_CONFIGS["imdb_ratings"]`
  was written from **IMDb's published schema**, not by mirroring the transform (the Task 40 lesson,
  where a check that copied the transform's assumptions confirmed the bug instead of catching it).
  **Live-verified on `2026-07-29`**: 8,635,427 bytes to Bronze (byte-identical to source), 1,709,992
  rows parsed, **1,139 of 1,140** films matched in 10.26s, 1 excluded. Silver DQ 28/28 → **32/32**.
  **One environmental gotcha worth recording:** the first transform run hung for 4.5 minutes in
  `do_sys_poll` on a stalled S3 socket — `StreamingBody.read()` has no read deadline of its own, so
  a dropped transfer blocks indefinitely rather than failing. A fresh run took 9.2s for the same
  read. Transient, not a code defect, but a real robustness gap if this ever runs unattended
  (relevant to Task 64).

#### [x] Task 67 — Warehouse: `fact_movie_rating`
- **Files:** new `warehouse/ddl/15_movie_ratings.sql`, `warehouse/ddl/02_facts.sql`, `etl/warehouse_loader/load_facts.py`, `data_quality/warehouse_checks.py`, `tests/{test_etl,test_warehouse_checks}.py`
- **Outcome:** `fact_movie_rating(movie_id, source, rating, vote_count, ingestion_date)`, PK
  `(movie_id, source)`, with a `CHECK (source IN ('imdb','tmdb'))` and an index on
  `(source, rating DESC)` so "top rated films" is an index range scan with no sort. **The grain is
  the whole point.** `fact_movie_metrics` is at `(movie_id, date_id, genre_id)`, so a multi-genre
  film stores its rating once per genre — the reason every reader of it carries a `SELECT DISTINCT`
  / `.values(...).distinct()` guard. A rating has nothing to do with a film's genres, so putting it
  at its true grain makes that guard unnecessary **by construction** rather than something each
  caller must remember. The Godfather is the worked example: **2 rows in `fact_movie_metrics`** (one
  rating, stored twice, once for Crime and once for Drama) versus **2 rows in `fact_movie_rating`**
  (two genuinely different facts, IMDb 9.20 and TMDB 8.69). Two alternatives were considered and
  are recorded in the DDL header: **no `dim_rating_source` table** (a two-row dimension whose only
  attributes — icon, label, outbound URL template — are pure presentation would be over-modelling;
  a CHECK enforces the vocabulary and Django owns the display metadata), and **`fact_` not
  `bridge_`** (this table carries a measure, so it earns the prefix under the naming rule
  `13_companies.sql` established). `load_fact_movie_rating()` builds from **both** Silver sources in
  one function — `imdb_ratings.parquet` → `source='imdb'` and `movies.parquet`'s existing
  `vote_average`/`vote_count` → `source='tmdb'` — which is what makes the table the single answer to
  "what is this film rated" instead of a second partial answer sitting beside `fact_movie_metrics`.
  Unresolvable FKs are quarantined, never dropped. **Live-verified on `2026-07-29`**:
  `fact_movie_rating` **2,279 rows** (1,139 imdb + 1,140 tmdb), **0 rejects**; imdb avg 7.248 vs
  tmdb avg 7.236, but max vote counts of **3,229,396 vs 40,480** — IMDb carries ~80× the votes
  behind each figure, which is the actual reason to prefer it. Spot checks read back from Postgres:
  The Godfather imdb 9.20 / 2,250,628; The Dark Knight 9.10 / 3,217,719; Inception 8.80 / 2,860,602.
  Zero films have more than one `imdb` row. Warehouse checks 35/35 → **39/39** (1 FK + 2 row-count +
  1 fact-load-sanity). **No Django/UI change yet — nothing renders this table until Task 68.**
  Tests 250 → **268** (18 new, including an explicit grain regression test, since one-row-per-film
  is the entire premise of the table).

#### [x] Task 68 — Django: the IMDb rating, with its mark
- **Goal:** Surface IMDb everywhere the TMDB rating is read today — the movie page, `?sort=rating`, home's Avg rating tile, the person and studio Avg rating stats — plus a compact figure on the poster cards, which show no rating at all today.
- **Files:** `django_app/movies/{models,views}.py`, new `movies/templates/movies/_rating_badge.html`, `movie_detail.html`, `_movie_card.html`, new `django_app/static/img/imdb.svg`, `static/css/theoria.css`, `tests/test_django_views.py`
- **Outcome:** Built as scoped. New `MovieRating` model (`managed = False`, fake single PK on
  `movie`, same shape as every other composite-PK fact). **One filtered annotation —
  `Max("movierating__rating", filter=Q(movierating__source="imdb"))` — serves both sorting and card
  display**, annotated unconditionally rather than only when `sort == "rating"` (as the old
  `moviemetrics` annotation was), so a list can no longer sort by one number and render another;
  `MOVIE_SORTS["rating"]` points at the same annotation. The genre-fanout dedupe guards in
  `studio_detail()` and `person_detail()` were **deleted, not ported**, each with a comment saying
  the new one-row-per-film grain makes them unnecessary — without that comment their absence reads
  as an oversight and someone re-adds them. `movie_detail()`'s context key was renamed
  `metrics` → `movie_rating` and now renders a vote count, which the page never showed before.
  New `_rating_badge.html` partial (the project's first shared icon partial) renders **nothing**
  when `rating` is falsy, a compact mark+figure on cards, and a full mark+figure+votes badge linked
  to IMDb on the detail page — reusing the `.ext-link` rule that had sat in `theoria.css` with zero
  consumers since `da9b59b`. `.rating-badge` is a new **additive** component; no shared rule was
  restyled (Task 38's CSS contract). `django_app/static/img/imdb.svg` is the project's **first image
  asset** — a deliberate deviation from the inline-SVG convention, since hand-drawing a specific
  wordmark would be a poor reproduction; taken verbatim from Wikimedia Commons (PD-textlogo).
  Accessibility: the label lives on the link (`aria-label="IMDb rating 9.2 out of 10"`) with the
  mark's `alt=""` and both figure spans `aria-hidden` — a logo plus a bare number is silent to a
  screen reader. **One necessary deviation from the plan's literal snippet:** `person_detail()`'s
  filmography comes from `Credit.select_related("movie")`, not a bare `Movie` queryset, so a
  queryset `.annotate()` was impossible; one extra `values_list` builds a dict and attaches
  `imdb_rating` as an instance attribute — still constant-cost, which is what the constraint
  actually required. **Live-verified**: all 10 routes 200, bad slug 404s; `/movies/the-godfather/`
  shows 9.2 / 2,250,628 votes linking to `imdb.com/title/tt0068646/`; `/movies/?sort=rating` ranks
  Shawshank 9.3 → Godfather 9.2 → Dark Knight 9.1 with every card showing the figure it sorted by;
  `/movies/vixen/` (no IMDb row) renders zero badges and no Rating row at all, 200 not an error.
  **N+1 verified by counting queries, not by inspection**: `/movies/` is **4 queries for 24 cards**,
  `/` 7, `/studios/<slug>/` 7, `/people/<slug>/` 5 — flat regardless of card count.
  **`home()`'s Avg rating moved 7.16 → 7.25**, which is the fix landing: the old figure averaged
  every `fact_movie_metrics` row and over-weighted multi-genre films. **Worth knowing:** 1,139 of
  1,215 films (93.7%) have an IMDb rating, and the 76 that don't are almost entirely unreleased or
  just-released titles below IMDb's ≥5-vote floor (54 of the 114 films released 2024+). Because
  `/movies/` defaults to newest-first, **the default landing view is the one place the badges look
  absent** — correct behaviour that reads as a bug. Worth revisiting the default sort. Tests
  268 → **271**.

#### [x] Task 69 — Analytics, live re-run, doc truth-up
- **Goal:** The phase-closing task, following Tasks 44 and 53.
- **Files:** `warehouse/queries/{movies_by_decade,top_rated_directors,top_studios_by_revenue,director_trend_over_time}.sql`, `django_app/analytics/views.py`, `README.md`, `docs/architecture.md`, `CLAUDE.md`, `for_learning.md`
- **Key points:** repoint all four rating queries and **delete their `SELECT DISTINCT movie_id, rating` CTE** — that deletion is the phase's payoff made visible. Full live re-run for the current date across the whole catalog, which is also what gives all 1,215 films an IMDb rating (a partition only gets ratings for the films it contains, so the 1,139/1,140 above is per-partition, not catalog-wide). **`CLAUDE.md`'s Warehouse Schema section is stale** — it claims 9 tables "as of Task 54"; the live warehouse had **15** before this phase and **16** after (verified 2026-08-26). True up the whole section, not just this phase's addition. `fact_movie_metrics.rating`/`.vote_count` will have no readers after this phase — leave them and record them as a knowingly-accepted write-only path, the same posture already taken for `dim_collection`.
- **Outcome (2026-08-30, merged with Task 63 — one commit):** All **four** rating queries
  repointed from `fact_movie_metrics` to `fact_movie_rating WHERE source = 'imdb'`, and their
  `WITH movie_ratings AS (SELECT DISTINCT movie_id, rating[, vote_count] FROM fact_movie_metrics)`
  CTEs **deleted** — the guard existed only to undo the per-genre fan-out the new
  one-row-per-film grain never creates, so its removal is the phase's payoff made visible. Two
  (`movies_by_decade`, `top_studios_by_revenue`) feed live dashboard panels; two
  (`top_rated_directors`, `director_trend_over_time`) are dormant `.sql` files fixed anyway so
  they don't rot. All four moved to `LEFT JOIN` (`top_rated_directors` was an inner join through
  its CTE — a LEFT JOIN keeps `movie_count` meaning "films directed", not "rated films directed";
  it also gained `ORDER BY avg_rating DESC NULLS LAST` so an all-unrated director can't top the
  chart). `analytics/views.py` needed no change for the repoint — the result columns kept their
  names. Verified against the replica: `movies_by_decade` 11 ms, `top_studios_by_revenue` 11 ms,
  `top_rated_directors` ~25 ms, `director_trend_over_time` 21 ms; the "Rating by decade" chart and
  "Top studios by revenue" table now show IMDb averages (Warner Bros. ★7.29; decade line
  `[7.20, 5.37, 7.27, 7.16, 7.33, 7.32, 7.33, 6.53]`). **The "full live re-run" was skipped by
  user decision** — the Task 64 nightly cloud path (its whole reason for existing) had already
  refreshed the entire catalog: `fact_movie_rating` holds **1,211 IMDb + 1,215 TMDB rows** in one
  current partition (`2026-08-29`), verified on both Neon and the local replica, so all 1,215
  films' ratings are catalog-wide *now* — a hand `run_pipeline.py` run would only re-prove
  `nightly-refresh` runs 3–4. `fact_movie_metrics.rating`/`.vote_count` now have **zero readers**;
  left in place, loader still writes them, recorded as a knowingly-accepted write-only path
  (dropping them is a separate reversible one-commit follow-up). `CLAUDE.md`'s Warehouse Schema
  section trued up **9 → 16 tables** (the whole section, not just this phase's addition — it also
  still listed `dim_movie` without `imdb_id`/`original_title`/`homepage`). `docs/architecture.md`
  §3 intro + new §3.8. Fresh-install check, DQ totals (32/32, 39/39 unchanged), route walk, test
  count (**298/298**) — all in the Task 63 outcome above.

---

### MUST DO — Automated Nightly Refresh

> Not part of any phase. Independent of Tasks 61–63 and can run at any point, but **best done
> before Task 63**, so that phase's live re-run happens on the scheduled path rather than being
> re-verified by hand afterwards.

#### [x] Task 64 — Nightly cloud refresh: warehouse to Neon, pipeline on GitHub Actions
- **Goal:** The data goes stale the moment the laptop closes, and the only way to refresh a film
  already in the catalog is a full `run_pipeline.py` re-run. Make the catalog refresh itself
  nightly, in the cloud, with no machine of ours powered on.
- **Research already done (2026-08-17) — do not re-derive these, they were measured live:**
  - **TMDB's changes feed cannot refresh ratings.** `/movie/changes` returns 6,909 changed films
    per 24h (70 pages × 100). 400 of them were probed via `/movie/{id}/changes`: 33 distinct
    change keys, and **`vote_average`, `vote_count` and `popularity` appear zero times** — TMDB
    excludes them by design, since they move on nearly every film daily and would make the feed
    useless. The keys it *does* report are `status` (116), `runtime` (52), `budget` (27),
    `revenue` (26), plus cast/crew/images/translations/release_dates/title.
  - **That is the exact inverse of what actually goes stale.** Measured against live TMDB at the
    stored precision (`rating` is `NUMERIC(4,2)`; TMDB now returns 3dp, so 7.584→7.58 is
    truncation, not drift):

    | field | random n=120 | released 2024+ n=60 |
    |---|---|---|
    | `vote_count` | 98% | 98% |
    | `rating` | 35% (3% by ≥0.05) | 85% (42% by ≥0.05) |
    | `revenue` | 2% | 28% |
    | `runtime` | 0% | 2% |
    | `status` | 0% | 2% |

    So a changes-driven refresh would faithfully update `runtime` (never drifts) and never touch
    `rating` (the wrong thing on the page). **The changes feed's real job is discovery and
    structural edits, not metrics.** Its date window caps at 14 days — a gap longer than that
    requires falling back to a `discover` sweep.
  - **A full refresh is cheap, so no incremental cleverness is warranted.** Observed throughput
    **4.76 req/s** (not the ~2 req/s in the Task 43 note). `?append_to_response=credits` returns
    a byte-equivalent payload (verified: 81 cast / 106 crew either way), collapsing today's 2
    calls per film to 1. Full catalog = **1,214 calls ≈ 4.3 min** (8.5 min on the current 2-call
    path). TMDB's soft ceiling is ~40 req/s, so there is headroom to parallelise if ever needed.
  - **Warehouse is 215 MB** — `fact_credit` 87 MB, `dim_person` 61 MB, `fact_collaboration`
    40 MB, `dim_date` 9.7 MB. Fits Neon's free 0.5 GB with room; if it ever tightens,
    `fact_collaboration` is fully derived and `dim_date` has ~48.5k unreferenced rows (~50 MB
    reclaimable).
  - **Repo `suhaylshokirov/theoria` is public**, so GitHub Actions minutes are free and
    unlimited. `config.py` already reads a single `DATABASE_URL`, and every stage is already
    idempotent per `ingestion_date` — which is what makes unattended scheduling safe and this
    task an env change plus a YAML file rather than a rewrite.
- **Why this shape and not the alternatives:** managed Postgres + a cron file adds no
  infrastructure — no Terraform, no Kubernetes, no Lambda — so it respects the Stack & Constraints
  non-goals ("a DE learning project, not an infra project"). The AWS-native path (ECS Fargate +
  EventBridge) and self-hosted Airflow both violate that spirit for no gain at this size; Modal
  and Cloud Run Jobs work but add a platform SDK or a container build. Real orchestration
  (DAGs, retries, backfills, lineage) is a later, separate concern and should not be bought here.
- **Files:** new `.github/workflows/nightly-refresh.yml`, new `etl/bronze/refresh_movies.py`,
  new `scripts/run_refresh.py`, `etl/tmdb_client.py`, `etl/gold/build_gold_datasets.py`
  (or a new `etl/gold/build_metrics_snapshot.py`), `etl/silver/transform_movies.py`,
  `.env.example`, `README.md`, `docs/architecture.md`, `tests/test_etl.py`
- **Steps:**
  1. **Move the warehouse to Neon.** Provision a free project, `pg_dump` local → `psql` into
     Neon (215 MB, a few minutes), then repoint `DATABASE_URL`. No code change — verify by
     running the existing test suite and Django against Neon before anything else is built.
  2. **`append_to_response=credits` in `TMDBClient.get_movie_details()`.** Halves Bronze call
     volume for both ingest and refresh. Free win, independent of everything else here.
  3. **`etl/bronze/refresh_movies.py` — the missing path.** Every ingest module sources its ids
     from a *discovery* endpoint; nothing sources them from `dim_movie`. That is the actual
     reason a stale film requires a full pipeline re-run. This one takes
     `SELECT movie_id FROM dim_movie` as input and writes a normal Bronze partition, so Silver,
     Gold and the warehouse loaders downstream are untouched. Bronze stays append-only.
  4. **Snapshot the volatile metrics to S3, not Postgres.** `fact_movie_metrics`' PK is
     `(movie_id, date_id, genre_id)` with `date_id` derived from the *release* date and
     `ingestion_date` only a column — so a refresh upserts in place and the previous rating is
     gone. Write one row per film per run (`movie_id, snapshot_date, rating, vote_count,
     revenue, popularity`) to `gold/metrics_snapshot/` as Parquet and keep only "latest" in
     Postgres. **Deliberately S3 and not a warehouse table:** ~1,215 rows/day would grow
     unbounded against a 0.5 GB tier, and history-of-measurements is exactly what the lake is
     for. This is what unlocks "rating over time" / "revenue accumulating after release" later.
  5. **`scripts/run_refresh.py`** — refresh-mode orchestrator, mirroring `run_pipeline.py`'s
     stage sequencing but sourcing ids from step 3. Keep them separate rather than adding a flag:
     ingest *discovers* films, refresh *updates* known ones, and conflating them is what produced
     the current problem.
  6. **`.github/workflows/nightly-refresh.yml`** — `schedule:` cron nightly plus
     `workflow_dispatch` for manual runs. Secrets: `TMDB_API_KEY`, `DATABASE_URL`, AWS creds.
     **The AWS key must be a least-privilege IAM user scoped to the one bucket** — the workflow
     file is world-readable on a public repo (the secrets are not, and fork PRs never receive
     them, but a general-purpose credential has no business here).
  7. **Guard the 60-day inactivity rule.** GitHub silently disables scheduled workflows on a
     public repo after 60 days with no repository activity — freshness would just stop, with no
     failure to notice. Either have the job commit its run summary (activity resets the clock),
     or make the repo private (a ~25 min nightly run is ~750 of the 2,000 free private minutes).
     Note also that `schedule` can be delayed under load and the floor is 5-minute granularity;
     neither matters nightly.
  8. **Watch the cross-region cost.** S3 is `eu-central-1` and GitHub runners are US-hosted,
     while the Silver transforms read Bronze one object at a time (~16 min per full pass — a
     known gap). If the nightly job's wall time is dominated by Silver rather than the ~4 min of
     TMDB calls, batch those reads as part of this task; that is the real bottleneck, not the API.
  9. **Weekly discovery, separately.** A second, weekly schedule walking `/movie/changes` to
     *add* new films and catch the structural edits a metrics refresh cannot see (new cast/crew,
     retitles, runtime corrections, `In Production → Released`). Optionally use the daily ID
     export (`files.tmdb.org/p/exports/movie_ids_MM_DD_YYYY.json.gz`, published ~08:00 UTC, no
     auth, 3-month retention) — one file download instead of 1,215 calls, and the only way to
     detect a TMDB id being **deleted or merged**, which would otherwise leave a dead film in
     `dim_movie` forever.
- **Verify:** trigger via `workflow_dispatch` and confirm it completes green end to end against
  Neon; confirm a known 2024+ film's `vote_count` changes between two consecutive runs; confirm
  the local Django site — unchanged, still on the laptop — serves the refreshed figures with no
  deploy or cache step, since the views read the warehouse live; confirm a snapshot Parquet lands
  in `gold/metrics_snapshot/`; confirm Silver DQ and warehouse checks pass in CI, not just locally.
- **Outcome (2026-08-29) — DONE. The catalog now refreshes itself nightly in the cloud with no
  machine of ours on.** `nightly-refresh` run 3 (`workflow_dispatch`) completed **green end to
  end against Neon** on commit `52b41e0` (Step 8 + Step 8b) — logged `success` in
  `ops/refresh-history.md` at 2026-08-29T06:15:30Z. All four verification gates pass:
  **(a)** `s3://<bucket>/gold/metrics_snapshot/ingestion_date=2026-08-29/metrics_snapshot.parquet`
  landed (42 KB), sitting beside the `2026-08-28` partition that runs 1–2 wrote before they
  timed out in the load phase. **(b)** `vote_count` moved on **941 of 1,215** films between the
  two consecutive snapshots (rating moved on 422; revenue on 0 — TMDB's community revenue figure
  is stable day-to-day, expected). **(c)** the local Django site — untouched, still on the laptop,
  no deploy or cache step — now serves the refreshed figures: `/movies/spider-man-brand-new-day/`
  flipped `Post Production → Released`, `revenue 0 → $2,232,611,878`, and gained an IMDb badge of
  **8.0 / 266,257 votes** (`fact_movie_rating (969681,'imdb')`), exactly the user's worked
  example. **(d)** `run_silver_checks` and `run_warehouse_checks` both ran inside the CI job and
  passed (the job is green; they abort it on failure). Local `pytest` **281/281**. The nightly
  path is what Task 63 and Task 69's full live re-runs will now run on. **`run_refresh.py` has
  now executed end-to-end** for the first time. Prior-state notes (all still accurate) follow:
  Written and green (`pytest` 270 → **278**, +8; no regressions; verified against Neon too):
  - **Step 2** — `TMDBClient.get_movie_details(movie_id, *, append_to_response=None)`. Backward
    compatible (default `None` → identical call). `refresh_movies` passes `"credits"`; the ingest
    path was left untouched (not in scope; a later free win).
  - **Step 3** — `etl/bronze/refresh_movies.py`. `refresh_movies(movie_ids=None, ingestion_date,
    client, engine)` — `movie_ids` defaults to `SELECT movie_id FROM dim_movie ORDER BY movie_id`
    (ascending so a retry re-processes in the same order and two runs' logs line up film-for-film,
    the `/connect/` ORDER BY lesson). `_split_payload()` separates one `append_to_response=credits`
    response into a details file with the `credits` key removed (byte-comparable to
    `ingest_movie_details`'s output) and a credits file rebuilt into the `{"id","cast","crew"}`
    shape the standalone `/credits` endpoint returns (what `transform_credits_bridge` /
    `transform_people` read). Missing `credits` in the payload → empty cast/crew + a warning,
    never a crash. Write-as-you-go, returns `(succeeded, failed)`. **Smoke-verified against the
    live local warehouse**: `_movie_ids_from_warehouse()` returns all **1,215** ids, ascending.
    The module has **never run end-to-end** (would hit live S3 + TMDB).
  - **Step 4** — `etl/gold/build_metrics_snapshot.py`. Reads `silver/movies/<date>/movies.parquet`,
    writes `gold/metrics_snapshot/<date>/metrics_snapshot.parquet` with exactly
    `movie_id, snapshot_date, rating, vote_count, revenue, popularity` (`vote_average`→`rating`),
    null-`movie_id` rows dropped, `snapshot_date` = the ingestion date. S3 not Postgres, per the
    step's own reasoning. IMDb ratings deliberately **not** folded in — the step lists six TMDB
    columns; noted as a possible later addition given Phase 15 made IMDb the rating of record.
  - **Step 5** — `scripts/run_refresh.py`. Separate orchestrator (not a flag). Sequence:
    `ingest_genres` → `refresh_movies` → `ingest_imdb_ratings` → the six Silver transforms →
    `run_silver_checks` → `build_gold_datasets` → **`build_metrics_snapshot`** (before the load
    upserts `fact_movie_metrics` in place) → `load_dimensions`/`load_facts`/`load_gold` →
    `run_warehouse_checks`. Genres + IMDb ratings are still fetched fresh (genres for
    `transform_genres`'s Bronze input; ratings/votes are the fields that actually drift).
  - **Steps 6–7** — `.github/workflows/nightly-refresh.yml` (cron `12 3 * * *` + `workflow_dispatch`,
    `timeout-minutes: 60`, `concurrency` group, `permissions: contents: write` only for the
    summary commit) and `.github/workflows/weekly-discovery.yml` (cron `20 4 * * 1`, runs
    `run_pipeline --source discover`). Both `always()`-append a line to `ops/refresh-history.md`
    and commit `[skip ci]` — that commit is the activity that stops GitHub disabling the schedule
    after 60 idle days. Secrets referenced: `TMDB_API_KEY`, `AWS_ACCESS_KEY_ID`,
    `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET`, `DATABASE_URL`, `DJANGO_SECRET_KEY`
    (the last is required by `config.py` even though the refresh never touches Django).
  - **Docs** — README §"Keeping the catalog fresh" + layout/test-count bump; `docs/architecture.md`
    new **§4.2** (separate orchestrator not a flag; `append_to_response` = 1 call; snapshot to S3
    not a table; why `/movie/changes` can't drive it — the 2026-08-17 measurement; Neon +
    Actions vs an AWS-native scheduler; the 60-day guard); `.env.example` Neon/`sslmode` note.
  - **8 new tests** in `tests/test_etl.py`: `get_movie_details` with/without `append_to_response`;
    `refresh_movies` — two-file split + `{"id","cast","crew"}` shape + no separate `/credits`
    call, `dim_movie` default via a mocked engine (order preserved), continue-after-failure with
    no partial writes, missing-`credits` tolerance; `build_metrics_snapshot` — six-column shape +
    rename + null-id drop + `snapshot_date` stamp, and reads-the-right-Silver-key.

  - **Step 1 — Neon migration. DONE and verified (2026-08-27).** Local `theoria` (16 tables) →
    `pg_dump --no-owner --no-privileges` plain SQL → `psql` into Neon project
    `ep-spring-brook-b1kjdyyg` (`eu-central-1`, PG 18.6), 0 restore errors. Row counts match local
    table-for-table. `.env` `DATABASE_URL` now the **direct** (non-pooler) Neon endpoint +
    `?sslmode=require` — direct not pooled because the loaders hold longer transactions
    (`assign_slugs` clear-then-rewrite, batched `executemany`) and Neon's own guidance is
    direct-for-migrations/long-sessions, pooled-for-serverless. Old local URL kept commented on
    the line above + `.env.local-backup-20260827`. Against Neon: `check_connection` True,
    warehouse checks 39/39, `pytest` 278/278, `manage.py check` clean, ORM read OK. Gotcha logged:
    first query after Neon's idle-suspend is a ~20–45s cold-start, once per idle period.

  **Infra steps — ALL DONE (2026-08-29):**
  1. **AWS IAM** — the user created a least-privilege user scoped to the one datalake bucket.
  2. **Repo secrets** — all seven added in GitHub → Settings → Secrets → Actions
     (`DATABASE_URL` = the Neon direct URL). Confirmed live: run 3's "Verify configuration" step
     passed and the job read S3 + TMDB + Neon successfully.
  3. **Verify** — done; see the Outcome block above (all four gates green on run 3).
  - **Step 8 (cross-region Silver reads) — DONE 2026-08-28.** The first `workflow_dispatch` run
    timed out at the 60-min job cap; the four Silver transforms reading ~1,215 Bronze objects each
    with a serial `get_object` from a US runner to `eu-central-1` S3 were the bottleneck (~15-20
    min of pure round-trip latency). New `s3_utils.read_json_objects()` does those reads through a
    32-worker thread pool (input-order results, per-key error capture); the transforms' four copies
    of `_read_json_from_s3` are gone. boto3 client got `connect_timeout`/`read_timeout`/`retries`
    so a stalled socket fails fast (Task 66 gotcha). Job timeouts raised to 90 (nightly) / 120
    (weekly). `refresh_movies`' ~2,400 serial S3 writes left as-is (write-as-you-go crash-safety);
    revisit only if it dominates. Tests 278 → 281.
  - **Step 8b (warehouse load: one round-trip per row) — DONE 2026-08-28.** With Silver fixed,
    the next `workflow_dispatch` run crawled in `load_dimensions`: `dim_movie` took 137s for 1,215
    rows (~113ms/row = one driver round-trip per row over the US↔Frankfurt link); `dim_person`
    (122k), `dim_date` (~49.7k), `fact_credit` (237k), `fact_collaboration` (193k) on that path
    would have been hours. Cause: `common._upsert` built its statement with `text()`, so
    SQLAlchemy's `insertmanyvalues` batching never engaged (it only rewrites INSERTs it compiled
    itself). Rewrote `_upsert` as a `postgresql.insert()` Core construct with
    `on_conflict_do_update` (one fix covers dimensions, facts and gold loaders).
    `assign_slugs`' per-row `UPDATE ... WHERE id = :id` executemany had the same problem — nothing
    batches an executemany UPDATE — so new `_apply_slugs()` does chunked
    `UPDATE ... FROM (VALUES ...)` (~1,000 rows/statement). Verified against local PG: 2,000-row
    upsert → 2 batched statements; 2,500-row slug pass → 3 statements, collision numbering intact,
    0 null/dupe slugs. **Confirmed live on run 3**: the full warehouse load (all dims + facts +
    gold + `run_warehouse_checks`) finished in ~2 min — the phase that had stalled 15+ min on
    `dim_movie` alone. Tests stayed 281 (3 assertions updated for the new statement shape).
  - **Step 9 (daily TMDB id export for deletions/merges)** — `weekly-discovery.yml` covers new
    titles + structural edits via `--source discover`; the `movie_ids_*.json.gz` export path for
    detecting deleted/merged ids is not implemented.

---

### MUST DO — Studio Provenance Page

> Not part of any phase. Independent of Tasks 61–64 and can run at any point. Raised by user
> request on 2026-08-17, immediately after the Studios redesign: the studio page shows a
> filmography and four derived stats, but nothing about the studio itself — no description, no
> headquarters, no official site, no parent company. All four exist on TMDB and are currently
> uncollected at every layer (Bronze included) — `dim_company`'s five columns
> (`company_id, name, logo_path, origin_country, slug`) come entirely from the `production_companies`
> stub embedded in a *movie's* detail payload, which TMDB deliberately keeps thin (id/name/
> logo/origin only). The richer fields live on a company's **own** detail endpoint, which this
> project has never called.

#### [x] Task 65 — Bronze → Silver → warehouse → Django: studio bios, headquarters, homepage, parent company
- **Goal:** `GET /company/{company_id}` on TMDB returns `description`, `headquarters`, `homepage`,
  and `parent_company` (nested `{id, name, logo_path}` or `null`) alongside the fields already
  collected. Surface all four on the studio page, above the filmography, the way `movie_detail`
  surfaces `overview`/`imdb_id`/`homepage` (Tasks 41, 56).
- **Before writing any code:** make one live `GET /company/{id}` call (e.g. Warner Bros. Pictures,
  id 174) and confirm the response shape assumed below — field names, whether `parent_company` is
  omitted vs. `null` when absent, and whether `description` is `""` or absent when empty. This
  project's convention (Tasks 40, 57) is checks and transforms written from the *measured* payload
  shape, never assumed from memory — do the same here before Silver or DDL is written. At the same
  time, measure real coverage across a sample of ~50–100 companies (this endpoint is expected to
  be far sparser than `movie.overview` — TMDB's own docs and spot checks suggest many smaller
  companies have an empty `description`) and write the actual figure into this task's Outcome
  rather than guessing one.
- **Cost, already reasoned through (do not re-derive):** ~1,383 companies (`dim_company`'s current
  row count) at Task 64's measured **4.76 req/s** ≈ under 5 minutes — cheap, unlike the deferred
  Task 43 person-bio enrichment (45k+ calls), which is why this is worth doing at all.
- **Files:** `etl/tmdb_client.py`, new `etl/bronze/ingest_companies.py`, new
  `etl/silver/transform_companies.py`, `data_quality/silver_checks.py`, `warehouse/ddl/01_dimensions.sql`
  + new `warehouse/ddl/15_company_details.sql`, `etl/warehouse_loader/load_dimensions.py`,
  `scripts/run_pipeline.py`, `django_app/movies/{models,views}.py`,
  `movies/templates/movies/studio_detail.html`, `tests/{test_etl,test_data_quality,test_django_views}.py`
- **Steps:**
  1. `TMDBClient.get_company_details(company_id)` — a one-line wrapper on `self.get(f"company/{company_id}")`,
     identical shape to `get_movie_details()`.
  2. `ingest_companies(company_ids, ingestion_date)` in a new `etl/bronze/ingest_companies.py`,
     mirroring `ingest_movie_details()` exactly: one JSON file per id, written as it completes so a
     mid-run failure never loses progress already made, returns `(succeeded_ids, failed_ids)`.
     S3 layout: `bronze/company_details/ingestion_date=YYYY-MM-DD/<company_id>.json`.
  3. **Where the company_id list comes from is the one real design decision here.** TMDB has no
     "list all companies" endpoint, and `run_pipeline.py` already threads `movie_ids` in-memory
     from discovery straight into `ingest_movie_details()`/`ingest_credits()` — company_ids need
     the same shape but aren't known until *after* movie details are fetched (they live inside
     each movie's `production_companies` array, not in the discovery listing). Add a small
     `_extract_company_ids(movie_ids, ingestion_date, bucket)` helper that re-reads the Bronze
     `movie_details` files just written (same `_list_bronze_keys`/`_read_json_from_s3` pattern
     `transform_movie_links.py` already uses) and returns the deduplicated set of
     `production_companies[].id`. Call it in `run_pipeline.py` right after `ingest_movie_details()`,
     before the Silver transforms.
  4. **Skip company_ids already enriched in a prior partition, rather than re-fetching every run.**
     This is a deliberate deviation from the "every Bronze entity is fetched fresh every partition"
     norm `ingest_movie_details()` follows — ratings/votes genuinely drift and are worth re-fetching
     (Task 64's whole nightly-refresh premise), but a studio's description/headquarters/parent
     essentially never change. Check `bronze/company_details/` across *all* prior ingestion_dates
     (not just this one) for an existing `<company_id>.json` before calling the API for it. Document
     this as the deliberate exception it is, since it breaks the pattern a future reader would
     otherwise expect from every other Bronze ingestion module.
  5. New `etl/silver/transform_companies.py`, reading `bronze/company_details/` for the date and
     writing `silver/company_details/company_details.parquet`: one row per company_id —
     `company_id, description, headquarters, homepage, parent_company_id, parent_company_name`.
     Normalise TMDB's `""` to `None` for `description`/`headquarters`/`homepage`, same convention
     as Tasks 36/55. `parent_company_id`/`parent_company_name` both null when TMDB's
     `parent_company` is `null`.
  6. New `ENTITY_CONFIGS["company_details"]` in `silver_checks.py`, written from the *measured*
     payload shape (step 0), not copied from the transform.
  7. `15_company_details.sql`: idempotent `ALTER TABLE dim_company ADD COLUMN IF NOT EXISTS
     description TEXT, headquarters TEXT, homepage TEXT, parent_company_id INTEGER,
     parent_company_name TEXT`. Add the same five columns to `dim_company` in `01_dimensions.sql`
     for a fresh bootstrap. **No FK constraint on `parent_company_id`, and this is deliberate**: a
     parent company (e.g. The Walt Disney Company) is not guaranteed to itself hold a
     `bridge_movie_company` row — it may never be directly credited on a film, only its
     subsidiaries are — so enforcing referential integrity would either reject a legitimate parent
     link or force fabricating a `dim_company` row for a company that was never actually linked to
     a movie, breaking the "this dimension is the distinct set of companies actually linked to a
     film" invariant Task 58 established. `parent_company_id` is a soft, unenforced reference:
     resolved at *read* time (step 9), the same "render only when it resolves" judgment Task 56
     already applied to IMDb/homepage links.
  8. `load_dim_company()` in `load_dimensions.py` currently derives the whole dimension from Silver's
     `movie_companies` link table alone. It now needs a second Silver source
     (`company_details`, one row per company, not per movie/company pair) **left-joined** onto the
     first — a company with no Bronze company-details row yet (a fresh company introduced this
     partition, enrichment pending, or a company whose one API call failed) must still upsert with
     its existing five columns and simply leave the five new ones null, never block the load.
  9. Django: five new fields on `Company` (`TextField(null=True)` / `parent_company_id =
     IntegerField(null=True)`). `studio_detail` resolves the parent — if `parent_company_id` is set,
     one extra `Company.objects.using("warehouse").filter(company_id=...).first()` (a single detail
     page, not the list, so one query is cheap) — and links to it only if that row exists, else
     renders the plain `parent_company_name` text with no link. New provenance block in
     `studio_detail.html`, positioned above the toolbar+filmography per the request ("above their
     films"): `description` as prose (only when non-null — the Task 56 "print only when there's
     something to say" rule), then a record list for Headquarters / Official site (`.ext-link`,
     reusing Task 56's exact outbound-link treatment — no new CSS) / Parent company. A studio with
     none of the four (real TMDB sparsity, not a bug) renders no block at all, the same way
     `movie_detail`'s "Elsewhere" row disappears when both IMDb and homepage are null.
  10. Wire `ingest_companies()`/`transform_companies()` into `run_pipeline.py` in sequence; backfill
      all three existing partitions once, then let the normal pipeline cadence keep it current
      (subject to step 4's already-enriched skip).
- **Verify:** the live payload-shape + coverage numbers from the pre-work step land in this task's
  Outcome, not assumed figures; `dim_company` gains all five columns with 0 rows lost; Warner Bros.
  Pictures' page shows a real description/headquarters/homepage; a studio with a resolvable parent
  (e.g. a Marvel or Lucasfilm subsidiary → The Walt Disney Company, if Disney itself has a
  `dim_company` row) links to it; a studio with an *unresolvable* parent shows the name as plain
  text, not a dead link; a studio with none of the four fields renders no provenance block; Silver
  DQ and warehouse checks both pass; full test suite green.
- **Outcome (2026-08-30):** Shipped end to end. **Pre-work probe (live TMDB, no code):**
  `GET /company/{id}` returns exactly 8 keys, *all always present* — `description`/`headquarters`/
  `homepage` are `""` when empty (never omitted), `parent_company` is `null` or a 3-key
  `{id,name,logo_path}` stub. **Coverage is far lower than "studio bio" implies** — random sample
  of 90 of 1,398: description **1.1%**, headquarters 53%, homepage 29%, parent 0%; top 50 studios
  by film count (where page views land): description **4%**, headquarters **96%**, homepage 64%,
  parent 10%. User decision: carry all four anyway (one nullable column + a `{% if %}` costs
  nothing). Throughput ~2.5 req/s → the 1,398-company backfill took **~13 min**.
  **Bronze:** new `etl/bronze/ingest_companies.py` — `get_company_details()` one-line wrapper on
  `TMDBClient`; `ingest_companies()` mirrors `ingest_movie_details` (one JSON/id, write-as-you-go)
  but with the **deliberate exception** that it skips any `company_id` already present under
  `bronze/company_details/` in *any* prior partition (`_already_enriched_ids()` lists the whole
  prefix) — a studio's HQ/description essentially never drifts, unlike a film's votes. **Silver:**
  new `etl/silver/transform_companies.py` sweeps **every** `bronze/company_details/` partition
  (not one date — the cumulative enriched set is spread across them), normalises `""`→`None`,
  splits `parent_company` into `parent_company_id`/`_name`, writes one dated
  `silver/company_details/…/company_details.parquet`. **DDL:** new `16_company_details.sql`
  (idempotent `ADD COLUMN IF NOT EXISTS` ×5 on `dim_company`; folded into `01_dimensions.sql`);
  **no FK on `parent_company_id`** — a holding-company parent (Warner Bros. Entertainment #17,
  Viacom International #5308) often has no `dim_company` row, so it's a *soft* reference resolved
  at read time. **Loader:** `load_dim_company(session, df, details_df=None)` gained an optional
  second Silver source, **LEFT-joined** — an un-enriched company still upserts its 5 original
  columns; `load_dimensions()` reads `company_details` in a `try/except` (a partition written
  before this transform existed degrades to null detail columns, never crashes). Wired into
  **both** `run_pipeline.py` (new `_extract_company_ids()` re-reads the just-written movie-detail
  payloads for `production_companies[].id`) **and** `run_refresh.py` (the plan predates the
  Task 64 nightly path — `transform_companies` must run there too or the cumulative enrichment is
  wiped on every Neon write). **Django:** 5 `TextField/IntegerField(null=True)` on `Company`;
  `studio_detail()` resolves the parent with one extra query *only when* `parent_company_id` is
  set; new provenance block in `studio_detail.html` above the filmography — `description` as
  `.specimen-synopsis` prose, then a `.record-list` of Headquarters / Official site
  (`.ext-link`, reused) / Parent company (link when it resolves to a `dim_company` row, plain
  text otherwise). **Zero new CSS** — the block is a `<section class="sheet-section">` so the
  existing vertical-rhythm rules space it. Whole block disappears when a studio has none of the
  four (the Task 56 "render only when there's something to say" rule).
  **One bug found live:** the first backfill did a partial-column upsert (`company_id` + 5
  details only) and hit `NotNullViolation` on `dim_company.name` — Postgres checks NOT NULL on
  the *proposed* row **before** `ON CONFLICT` can rescue it, so any upsert omitting `name` fails
  even for a row that exists. Fixed by backfilling through the real
  `load_dim_company(conn, movie_companies_df, company_details_df)` path (includes `name`), which
  is also the exact code the nightly job now runs. **Live-verified:** Bronze **1,397/1,398**
  companies written (1 is TMDB id 67681, a 404 — deleted/merged; keeps null detail columns);
  Silver DQ `company_details` **4/4**; backfilled to **Neon and the replica** (DDL applied to
  both — the replica sync is data-only). `dim_company` on both: total **1,398**, null slugs
  **0** (slugs untouched), description **6**, headquarters **672**, homepage **426**, parent
  **8**. Of the 8 parents, 5 resolve to a `dim_company` row and link (Pixar→Walt Disney
  Pictures, MGM→Sony Pictures, United Artists→MGM, Paramount Vantage→Paramount, Sony Pictures
  Animation→Sony Pictures); 3 are plain text (Warner Bros. Pictures & Castle Rock → "Warner
  Bros. Entertainment"; Paramount → "Viacom International"). Route walk: `/`, `/movies/`,
  `/people/`, `/studios/`, `/analytics/`, `/movies/the-godfather/` and four studio pages all
  **200**, bad slug **404**; `/studios/warner-bros-pictures/` renders HQ + `↗` site link +
  "Warner Bros. Entertainment" as text; `/studios/pixar/` links its parent;
  `/studios/columbia-pictures/` shows the prose description; `/studios/will-vinton-studios/`
  (no data) renders no block. **Fresh-install check:** a scratch DB from `01`–`03` produces
  exactly the live 16 tables and `dim_company` with all 10 columns. Warehouse checks unchanged
  (Task 65 adds none — `company_details` feeds an existing dimension, not a new table).
  `pytest` **298 → 313** (+15: test_etl +12 — client wrapper, `ingest_companies` skip/continue,
  `transform_companies` normalise/sweep/null-drop, `load_dim_company` left-join/None/unenriched;
  test_data_quality +1 — all-null detail row still passes; test_django_views +4 — provenance
  render, parent link, parent plain-text, no-block). Docs: `docs/architecture.md` new **§3.9** +
  §3 intro; `README.md` (313 tests, `/studios/` route row). **Small known gap:** the backfill
  joined against the `2026-08-29` `movie_companies` partition (1,388 distinct companies), so ~10
  companies linked only in older partitions have null detail columns until a nightly run that
  includes them; `transform_companies` already has their Silver rows, so it self-heals.

---

### Feature — Browse the Films index by genre

> Raised by user request on 2026-08-30. `/movies/` today offers a title search, four sort
> segments (Newest / Rated / Revenue / A–Z) and a **country** `<select>`. The country facet has
> not earned its slot — genre is how people actually narrow a catalog ("the newest horror films",
> "the highest-grossing action films"), and the two filters compose with the existing sorts the
> same way, so this is a swap, not an addition.
>
> **Measured before planning (2026-08-30, live replica), so the design isn't built on guesses:**
>
> | fact | value |
> |---|---|
> | `dim_genre` rows | 19 |
> | genres with ≥1 film in the catalog | **18** — `Documentary` has **0** |
> | films with ≥1 genre row | 1,213 / 1,215 (the 2 known `fact_movie_metrics`-less films) |
> | biggest genres | Action 429, Drama 392, Adventure 376, Comedy 331, Thriller 314, Sci-Fi 284 |
> | duplicate `(movie_id, genre_id)` pairs in `fact_movie_metrics` | **7**, across **2** films |
>
> That last row is the one number that changes the code. `fact_movie_metrics`' PK is
> `(movie_id, date_id, genre_id)` and `date_id` is derived from the *release* date — so a film
> whose release date moved between ingestions keeps **both** rows. *Avatar Aang: The Last
> Airbender* holds `date_id` 20260725 (from `2026-07-06`) **and** 20260724 (from `2026-08-29`)
> for each of its 4 genres; *The Odyssey* the same for 3. So a genre join hands back that film
> twice, and `.distinct()` is **load-bearing here, not defensive** — without it those two films
> would appear twice in a filtered grid and be counted twice by the paginator.

#### [x] Task 70 — Replace the country filter on `/movies/` with a genre filter
- **Goal:** `/movies/` filters by genre, composing with all four existing sorts (newest / rated /
  revenue / A–Z) and with `?q=`, surviving pagination. The country filter is removed from this
  page. **Country data is not touched anywhere else** — `movie_detail`'s Countries /
  Country-of-origin provenance rows (Task 62), `dim_country`, `bridge_movie_country` and the
  "Films by production country" analytics panel all stay exactly as they are. This is a
  read-side, app-layer change: **no DDL, no ETL, no pipeline re-run, no new TMDB calls.**
- **Files:** `django_app/movies/views.py`,
  `movies/templates/movies/{movie_list,_movie_grid}.html`, `tests/test_django_views.py`
- **Steps:**
  1. **Remove the country filter from `movie_list()` only** — the `country` param, the
     `movie_countries__country_id` filter, `country_choices`, the `country` key in `base_query`,
     the `Country` import (`MovieCountry` stays, `movie_detail` still reads it), and the country
     paragraph in the view docstring. Drop the `<select>` from `movie_list.html`.
  2. **Genre membership lives only in `fact_movie_metrics`** — there is no `bridge_movie_genre`.
     Filter with `movies.filter(moviemetrics__genre_id=gid).distinct()`, the same join+`.distinct()`
     shape the country filter used, and **comment the `.distinct()` with the real reason**: the 2
     films above carry two `date_id` rows per genre. A comment saying "defensive" would be wrong
     and would invite someone to delete it.
  3. **URL shape: `?genre=science-fiction`, not `?genre=878`.** `dim_genre` has no slug column and
     this task adds none (a warehouse migration for a read-side filter would mean DDL against Neon
     *and* the replica, plus a loader change). Slugify the 19 names in the view instead —
     `{slugify(name): genre_id}` built from the same `values_list` that feeds the `<select>` — so
     the URL stays readable and consistent with every other slug-addressed page (Task 46), and no
     raw surrogate key appears in a user-facing URL (the no-internals-in-the-UI rule).
  4. **Validate against that map and fall back to no filter** on an unknown slug, exactly as
     `sort` / `gender` / `known_for` already do — not a 404, and not an empty grid.
  5. **Offer only genres that have at least one film.** `Documentary` has 0 and would be a choice
     that can never return anything. Use `.annotate(film_count=Count("moviemetrics__movie",
     distinct=True)).filter(film_count__gt=0)` so it compiles to `HAVING`, the Task 59 precedent.
     `distinct=True` matters for the same duplicate-`date_id` reason as step 2.
  6. `base_query` carries `genre` forward so the shared `_pager.html` keeps it across pages —
     extend the existing `urlencode` call, don't rebuild it.
  7. `_movie_grid.html`'s empty state: `{% elif country %}` → `{% elif genre %}`. Still reachable
     (e.g. `?q=zzz&genre=horror`), so keep the message rather than dropping the branch.
  8. `_sheet_header.html` sub copy on `movie_list.html` says "narrow by country" — say genre.
  9. **Zero new CSS and zero new JS.** The genre `<select>` takes the country `<select>`'s slot in
     the same `.field` wrapper inside the same `[data-live-filter]` form, so `initLiveFilter()`
     picks it up with no change — it re-fetches on any field change and already swaps `#movies-grid`.
- **Tests:** the 6 existing `movie_list` tests all mock `Country.objects` for `country_choices`
  and must be repointed at `Genre.objects`; `test_movie_list_filters_by_country` and
  `test_movie_list_country_survives_pagination` become their genre equivalents. Add: an unknown
  `?genre=` slug falls back to unfiltered, and genre + `?sort=revenue` composes (the actual
  feature — the filter must not disturb the ordering or the `imdb_rating` annotation).
- **Verify (live, against the replica):** `/movies/?genre=horror&sort=release` returns horror
  films newest-first; `/movies/?genre=action&sort=revenue` leads with the biggest action
  grossers; `/movies/?genre=action` reports **429** films across its pages, matching the measured
  count above, and *Avatar Aang* / *The Odyssey* each appear **once** (the `.distinct()` proof);
  `genre=` survives the pager's Previous/Next; the `<select>` lists **18** options, not 19;
  `?genre=nonsense` renders the full unfiltered catalog, 200; `/movies/` with no params is
  unchanged; a film page still shows its Countries row; `/analytics/`'s country panel still 200s.
- **Outcome:** Built exactly to spec, no deviations. `movie_list()` lost `country`,
  `country_choices`, the `movie_countries__country_id` filter and the `Country` import
  (`MovieCountry` kept — `movie_detail` still reads it); gained the genre `<select>` sourced from
  `Genre.objects.using("warehouse").annotate(film_count=Count("moviemetrics__movie",
  distinct=True)).filter(film_count__gt=0).order_by("genre_name")`, the exact `HAVING`-compiling
  shape Task 59 already established for offering only non-empty choices — `Documentary` (0 films)
  is correctly absent. The filter itself joins `fact_movie_metrics` directly
  (`movies.filter(moviemetrics__genre_id=genre_ids[genre]).distinct()`), since there is no bridge
  table for genre membership. `?genre=science-fiction` (never `?genre=878`) is built from
  `{slugify(name): genre_id}` over the same `values_list`, so no raw `genre_id` reaches a URL. An
  unknown slug resets to `""` before the filter runs — same fallback posture as `sort`/`gender`/
  `known_for`, not a 404 or an empty grid. The `.distinct()` carries the real reason in its
  comment rather than "defensive": `fact_movie_metrics`' PK is `(movie_id, date_id, genre_id)` and
  `date_id` tracks the *release* date, so *Avatar Aang: The Last Airbender* and *The Odyssey* each
  hold two `date_id` rows per genre from a release date that moved between ingestions (7 duplicate
  `(movie, genre)` pairs total) — without `.distinct()` both would double-render and double-count
  in a filtered, paginated grid. `movie_list.html`'s `<select>` reused the country one's exact
  `.field` slot inside the same `[data-live-filter]` form, so `initLiveFilter()` needed no change;
  `_movie_grid.html`'s empty state became `{% elif genre %}`, keeping the same message since it's
  still reachable (`?q=zzz&genre=horror`); the sheet-header sub-copy now reads "narrow by genre".
  **Zero new CSS/JS/template files** — confirmed via `git status`, which also shows
  `theoria.css`/`theoria.js` untouched. **Live-verified against the replica**:
  `/movies/?genre=horror&sort=release` 200, newest-first (Evil Dead Burn, Night of the Living
  Dead, …); `/movies/?genre=action&sort=revenue` 200, biggest grossers first (Avatar, Avengers:
  Endgame, Avatar: The Way of Water, …); `/movies/?genre=action` totals **429** films across
  **18** pages (17×24 + a 21-film last page) — exactly the measured figure in the feature's
  header table — with *Avatar Aang: The Last Airbender* and *The Odyssey* each landing on page 1
  and appearing **exactly once**, the `.distinct()` proof; the pager's `page=3` link on page 2
  carries `genre=action` forward (`?q=&sort=release&genre=action&page=3`); the `<select>` lists
  **18** options (Action … Western, no Documentary) plus "Any genre"; `?genre=nonsense` 200s the
  full unfiltered catalog (24 cards on page 1, same as no param at all); `/movies/` with no params
  renders unchanged; `/movies/the-godfather/` still shows its Countries row (country provenance,
  `dim_country`, `bridge_movie_country`, and the "Films by production country" analytics panel are
  all untouched, as scoped); `/analytics/` still 200s with that panel present. `pytest` 314 →
  **316** (+2 net: `test_movie_list_filters_by_country`/`test_movie_list_country_survives_pagination`
  became their genre equivalents 1:1, plus 2 genuinely new tests —
  `test_movie_list_unknown_genre_falls_back_to_unfiltered` and
  `test_movie_list_genre_composes_with_revenue_sort`; every other `movie_list` test's
  `Country.objects` mock was repointed at `Genre.objects`' longer
  `.annotate().filter().order_by().values_list()` chain). No doc truth-up needed beyond this file
  — grepped `README.md`/`docs/architecture.md` for the list-page country filter and found none;
  the `?country=`/`country_choices` mentions there are all about the warehouse schema or
  `movie_detail`'s provenance rows (Task 62), which this task doesn't touch.

#### [x] Task 71 — Genre chips on the movie page link to the filtered index
- **Goal:** A film's genre chips have been plain, unclickable text since **2026-08-14**, when the
  `/genres/` index and detail pages were removed and left them pointing nowhere. Task 70 gave them
  somewhere to point. Clicking "Horror" should land on the Films index filtered to Horror, newest
  first — **no new page**, just the index the reader already knows.
- **Files:** `django_app/movies/templates/movies/movie_detail.html`, `tests/test_django_views.py`
- **Outcome (2026-08-30):** One template change and one test. `<span class="chip">` became
  `<a class="chip" href="{% url 'movies:movie_list' %}?genre={{ genre.genre_name|slugify }}">`.
  Three decisions worth recording:
  **(1) No view change, and no `?sort=` on the link.** `/movies/` already defaults to
  `sort="release"` (newest first), which is exactly where a reader clicking a genre wants to land,
  so pinning `&sort=release` into the href would duplicate a default into 19 hrefs for nothing.
  **(2) The slug is built by Django's built-in `slugify` template filter**, which *is*
  `django.utils.text.slugify` — the same function `movie_list()` uses to build its
  `{slug: genre_id}` map. `dim_genre` has no slug column (Task 70 deliberately added none), so
  both sides must derive it, and deriving it through one shared function is what stops them
  drifting. A view-side change (attaching a slug to each `Genre` in `movie_detail()`) was
  considered and rejected: it would compute the same string a second way for no gain.
  **(3) Zero new CSS — and the reason is a small piece of history.** `.chip:hover` in
  `theoria.css` already sets `background: var(--lime-wash)`, `border-color: var(--lime-mark)` and
  `text-decoration: none` — a hover state written for links, left stranded when the chips were
  demoted to `<span>`s in 2026-08-14. Making them anchors again brought it back to life. Checked
  the cascade rather than assuming: `.chip`'s `color: var(--ink)` (0,1,0) beats the global
  `a`'s lime (0,0,1), and `a:hover`'s underline (0,1,1) is overridden by `.chip:hover` (0,1,1,
  later in source), so a chip renders identically at rest and gains only the hover it was
  always styled for.
  **Live-verified:** `/movies/the-godfather/` renders `?genre=drama` + `?genre=crime`;
  `/movies/inception/` renders `?genre=adventure`, `?genre=action`, `?genre=science-fiction`
  (multi-word slug round-trips). Following each: 200, the genre `<select>` arrives **preselected**
  to that genre, and results are newest-first (`?genre=drama` → three 2026 films). `?genre=tv-movie`
  returns its 2 films (Midnight Matinee 1988, Duel 1971) — the smallest genre, proving the path
  isn't only right for large ones. `pytest` 316 → **317** (1 new: chips render as links to the
  filtered index, and no bare `<span class="chip">` survives).

---

## Additional Reference

Full design rationale and original architecture decisions: `docs/architecture.md`
Learning log (updated after every task): `for_learning.md`
