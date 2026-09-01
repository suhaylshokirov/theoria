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
new `_studio_header.html` partial, same shape as `_person_header.html`: a ~224px logo plate on the
left, the record (name, then the four stats) on the right. Reuses the `/studios/` grid card's
`--logo-plate`/`contain`/monogram-fallback treatment, incl. the fixed light plate that is never
redefined under `[data-theme]` so dark-on-transparent wordmarks stay legible in dark mode.
Template + CSS only, no view change. `pytest` **319**. Full detail in `for_learning.md`.
Since then (ad-hoc, 2026-09-01): **the person page filmography got the `/movies/` toolbar** —
a search box + Newest/Rated/Revenue/A–Z sort segments above the filmography grid on
`/people/<slug>/`, live-filtered by `initLiveFilter()` and server-paged, the same shape
`/studios/<slug>/` already had (Task 62). `person_detail()` filters/sorts the merged
`{"movie","job_display"}` filmography list in Python (it's already materialised and small; a
`GROUP BY` can't return those rows) via new `_sorted_filmography()` + `FILMOGRAPHY_SORTS`; new
`_person_filmography_grid.html` / `_person_filmography_results.html` partials mirror the studio
pair. Header stats stay computed over the whole filmography. **Zero new CSS/JS.** `pytest`
**324**. Full detail in `for_learning.md`.
Since then (2026-09-01): **Task 72 — People bios — code complete, tests green, live backfill
pending.** `dim_person` grew 7 → 13 columns (biography, birthday, deathday, place_of_birth,
homepage, imdb_id from a new `GET /person/{id}` Bronze source) plus a new `person_alias` table
for `also_known_as`. New `ingest_people.py` (with a `max_new` per-run cap the company path didn't
need) + `transform_people_details.py` (two Parquets, sweeps every partition) + `17_person_details.sql`
+ `load_dim_person(details_df=)` LEFT-join + `load_person_alias()` + `_extract_person_ids()` wired
into both orchestrators. Person page gained a bio block, a Born/Died/Born-in header row and an
"Elsewhere" IMDb/homepage row — **no `views.py` change, zero new CSS**. DDL on the local replica;
`pytest` **324 → 343**. **Left to run (needs live TMDB+AWS+Neon, ~2.3h):** DDL on Neon, the
priority-ordered ~35.8k-call backfill, Silver + `load_dimensions()` on Neon, replica re-sync.
Nothing renders a real bio until then. Full detail in the Task 72 block + `for_learning.md`.
Prior task: **Task 70 — replaced the `/movies/` country filter with a genre filter
(2026-08-30).
Last updated          : 2026-09-01
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

> The live shape as of Tasks 63 + 69 was **16 tables** (verified 2026-08-30 against
> `information_schema`). Task 72 adds a 17th, `person_alias` — its DDL is in `01_dimensions.sql`
> and `17_person_details.sql` and is applied to the local replica, but the live Neon warehouse
> stays at 16 until Task 72's backfill runs there.
> `dim_actor`, `dim_director`, `fact_cast` and `fact_crew` were dropped in Task 53; `fact_casting`
> was replaced in Task 35. `warehouse/ddl/01`–`03` bootstrap this schema; `04`–`17` are migrations
> for an existing DB (once `11` drops tables, "run every file in order" ≠ "build the current
> schema" — see README §2).

**Dimensions (8):**
- `dim_movie(movie_id PK, title, release_date, runtime, budget, revenue, original_language, status, overview, tagline, poster_path, backdrop_path, imdb_id, original_title, homepage, slug, collection_id FK)`
- `dim_person(person_id PK, name, gender, popularity, profile_path, known_for_department, slug, biography, birthday, deathday, place_of_birth, homepage, imdb_id)` — the last 6 from `GET /person/{id}` (Task 72), all nullable and sparse even among people with a photo. `imdb_id` has a non-unique index.
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

### Feature — People bios

> Raised by user request on 2026-09-01: give every actor, director and crew member a bio on
> their page, plus everything else TMDB carries about them as a person. `dim_person` has held
> only `name, gender, popularity, profile_path, known_for_department` since Task 53 — nothing
> biographical has ever been fetched for a person.
>
> **Source check, done before any planning:**
> - **TMDB `GET /person/{id}`** — probed live (Tom Hanks, id 31). Returns 14 keys, always all
>   present across a 200-person sample (one distinct key set) — `biography`/`homepage` are `""`
>   when empty (never omitted), `birthday`/`deathday`/`place_of_birth`/`imdb_id` are `null`,
>   `also_known_as` is always a list (possibly empty). Every `birthday`/`deathday` in the sample
>   parses as a clean ISO date — no partial dates, and `deathday` never appears without
>   `birthday`. `adult` was `False` on all 200 sampled — the same Phase 12 finding
>   ("always null/false, carries nothing") applies here too.
> - **IMDb's free bulk dataset has no biography field.** `name.basics.tsv.gz`
>   (`datasets.imdbws.com`, the same source Phase 15 already uses for ratings) is
>   `nconst, primaryName, birthYear, deathYear, primaryProfession, knownForTitles` — confirmed by
>   downloading and reading its header. Bios exist only on IMDb's rendered web pages (scraping is
>   against their ToS) or the paid enterprise API. Not a source for this feature; the one thing it
>   could add later is coarser birth/death *years* for people TMDB doesn't cover, joinable on the
>   `imdb_id` TMDB already returns — not worth its own task.
> - **TMDB is therefore the only source.** Availability was never the blocker — cost is, which is
>   why this was deferred at Task 43 (45k+ calls estimated there).
>
> **The cost problem, and the free fix — measured, not estimated:** `dim_person` holds
> **123,590** people; at the measured **4.35 req/s**, calling every one is **~7.9 hours**, and
> ~82% come back with nothing. `profile_path` — already stored, free — predicts a bio sharply
> (n=60 each side, so ±~13pp at 95%; direction is unambiguous):
>
> | group | rows | bio | birthday | place of birth |
> |---|---|---|---|---|
> | has `profile_path` | 35,782 | **62%** | 63% | 65% |
> | no `profile_path` | 87,808 | **7%** | 10% | 12% |
>
> The 29% of `dim_person` with a photo holds an estimated ~78% of every bio that exists. **User
> decision (2026-09-01): enrich everyone with a photo** (35,782 calls, ~2.3h), fetched in priority
> order — billed cast (`order < 10`) + directors/writers first, then the rest — capped per
> nightly run so it self-completes over several nights with no hand-run required, though the
> initial backfill runs live and in full rather than trickling in over a week.
>
> **Field coverage among the 35,782 photo-havers (n=200 sample), and where each lands:**
>
> | TMDB field | coverage | destination |
> |---|---|---|
> | `imdb_id` | 94% | `dim_person.imdb_id VARCHAR(20)` |
> | `birthday` | 66% | `dim_person.birthday DATE` |
> | `biography` | 64% | `dim_person.biography TEXT` |
> | `place_of_birth` | 64% | `dim_person.place_of_birth TEXT` |
> | `also_known_as` | 50% (205 aliases / 200 people, median 1, p90 3, max 7) | new `person_alias` table |
> | `homepage` | 16% | `dim_person.homepage TEXT` |
> | `deathday` | 14% | `dim_person.deathday DATE` |
>
> `also_known_as` is a list, not a scalar — folding it into a delimited string would break first
> normal form, so it gets its own table: `person_alias(person_id FK, alias, ordering,
> ingestion_date)`, PK `(person_id, alias)`. Neither `fact_` (no measure) nor `bridge_` (it
> doesn't join two dimensions, it attaches repeating text to one) — name it plainly and note why
> in the DDL header. `adult` is measured at 0% true and deliberately **not** carried, matching the
> Phase 12 precedent. `gender`, `popularity`, `known_for_department`, `name`, `profile_path` are
> already owned by `transform_people` from the credits pass — enrichment must not re-write them
> from this second source, or the column has two writers.
>
> `profile_path`/`order`/`job`/`department` are already present in the Bronze credits payload
> (verified: `GET /movie/{id}/credits`), so both the candidate set and the fetch priority can be
> derived from Bronze alone — the same shape as `_extract_company_ids()` in Task 65, no layer
> inversion, no warehouse read.

#### [ ] Task 72 — Bronze → Silver → warehouse → Django: person bios and vitals
- **Goal:** Every person with a `profile_path` gets a bio, birth/death dates, place of birth,
  aliases, IMDb link and homepage where TMDB has them; a person page with none of it renders no
  extra block, the same "only when there's something to say" rule as Tasks 56/65.
- **Files:** `etl/tmdb_client.py`, new `etl/bronze/ingest_people.py`, new
  `etl/silver/transform_people_details.py`, `data_quality/silver_checks.py`,
  `warehouse/ddl/01_dimensions.sql` + new `17_person_details.sql`,
  `etl/warehouse_loader/load_dimensions.py`, `data_quality/warehouse_checks.py`,
  `scripts/{run_pipeline,run_refresh}.py`, `django_app/movies/{models,views}.py`,
  `movies/templates/movies/_person_header.html`, `person_detail.html`,
  `tests/{test_etl,test_data_quality,test_warehouse_checks,test_django_views}.py`
- **Steps:**
  1. `TMDBClient.get_person_details(person_id)` — one-line wrapper on `self.get(f"person/{person_id}")`,
     identical shape to `get_company_details()`.
  2. `ingest_people(person_ids, ingestion_date, *, max_new=5000)` in a new
     `etl/bronze/ingest_people.py`, mirroring `ingest_companies()` exactly: one JSON per id under
     `bronze/person_details/ingestion_date=YYYY-MM-DD/<person_id>.json`, write-as-you-go,
     returns `(succeeded, failed)`. Same **deliberate exception** as company details — skip any
     `person_id` already enriched in *any* prior partition (a birthday doesn't drift). New here
     (companies had no cap): **`max_new`** caps how many new ids are fetched in one call, so a
     large backfill can never blow the 90-minute nightly job budget — callers pass ids already in
     priority order and the cap just truncates the list.
  3. `_extract_person_ids(movie_ids, ingestion_date, bucket)` in `run_pipeline.py`, beside
     `_extract_company_ids()`: re-reads the just-written Bronze credits files, keeps only
     `id`s with a non-null `profile_path`, and orders them — billed cast (`order < 10`) and
     directors/writers first, everything else with a photo after — so the per-run cap always
     spends itself on the people readers actually reach first. Wire into **both**
     `run_pipeline.py` and `run_refresh.py` (the Task 65 lesson: miss the refresh path and the
     nightly Neon write never enriches anyone new).
  4. New `etl/silver/transform_people_details.py`, sweeping **every** `bronze/person_details/`
     partition (the Task 65 `transform_companies` pattern — enrichment accumulates across
     partitions, not just today's). Writes two Parquets:
     `silver/person_details/person_details.parquet` (`person_id, biography, birthday, deathday,
     place_of_birth, homepage, imdb_id`) and `silver/person_aliases/person_aliases.parquet`
     (`person_id, alias, ordering`). Normalise TMDB's `""` to `None`; parse `birthday`/`deathday`
     with `errors="coerce"`, log and drop rows that don't parse rather than trusting the sample.
     Drop null-`person_id` rows with a warning, never crash.
  5. Two new `ENTITY_CONFIGS` entries in `silver_checks.py`, written from the **measured** payload
     shape above, not copied from the transform (the Task 40 lesson).
  6. `17_person_details.sql`: idempotent `ADD COLUMN IF NOT EXISTS biography TEXT, birthday DATE,
     deathday DATE, place_of_birth TEXT, homepage TEXT, imdb_id VARCHAR(20)` on `dim_person`
     (non-unique index on `imdb_id`, same reasoning as Task 55 — external key, not guaranteed
     unique) + `CREATE TABLE person_alias(person_id FK, alias, ordering, ingestion_date, PK
     (person_id, alias))` with an index on `person_id`. Fold the same into `01_dimensions.sql` for
     a fresh bootstrap.
  7. `load_dim_person()` gains an optional `details_df` **LEFT-joined** onto the existing
     credits-derived frame, exactly the `load_dim_company()` shape from Task 65 — an un-enriched
     person still upserts their five original columns and leaves the six new ones null. New
     `load_person_alias()` in `load_facts.py`-or-`load_dimensions.py` (match wherever
     `load_bridge_movie_company()` lives), FK-resolved against `dim_person`, quarantining misses.
  8. New FK check + row-count-sanity check for `person_alias` in `warehouse_checks.py`.
  9. Django: six fields on `Person` (`TextField(null=True)` / `DateField(null=True)` ×2) + a new
     `Alias` model (`managed=False`, `person_alias`). `_person_header.html` gains a Born / Died /
     Born in record row (only the pieces that resolve — the Task 56 "print only when there's
     something to say" rule, e.g. Born-only with no Died for the living). New
     `.specimen-synopsis` bio block on `person_detail.html`, same slot/treatment as
     `studio_detail.html`'s description, positioned above the toolbar+filmography. IMDb/homepage
     as an "Elsewhere" row reusing `.ext-link` verbatim from Task 56/65 — **no new CSS**. A person
     with none of the six fields renders no extra block at all.
  10. Backfill: run the Bronze pass live and in full (priority-ordered, resumable for free since
      re-running skips what's already enriched), then Silver + `load_dimensions()` against **both**
      Neon and the local replica (the Task 65 two-target precedent).
- **Verify:** live payload-shape + coverage figures already measured above (record final backfill
  numbers in the Outcome, not the sample estimates); `dim_person` gains all six columns with 0
  rows lost; `person_alias` populated, 0 rejects; Tom Hanks' page shows a real bio + Born row +
  IMDb link; a single-credit crew member with no photo shows no extra block, 200 not an error; a
  person with `deathday` but no `birthday` never occurs (matches the sample); Silver DQ and
  warehouse checks both pass; full test suite green; the nightly job's per-run cap is confirmed
  not to blow its time budget on a steady-state run (should fetch ~0 new people once the backfill
  is done, same as `ingest_companies()` today).
- **Not in scope:** re-fetching `adult` (measured 0% true, no destination); folding IMDb's bulk
  `name.basics` birth/death years in (TMDB's own dates cover the same ground more precisely for
  the people that matter here); a dedicated `/people/<slug>/aliases` view or any new page — the
  aliases live only in the warehouse for now, with no UI consumer specified.
- **Outcome (2026-09-01) — code complete, tests green; the live backfill is the one step left.**
  Built end to end as a near-copy of Task 65 (studio provenance), which was the point.
  **Bronze:** `TMDBClient.get_person_details()` (one-line wrapper); new `etl/bronze/ingest_people.py`
  — one JSON per person under `bronze/person_details/`, write-as-you-go, skips anyone enriched in
  *any* prior partition (the birthday-doesn't-drift exception `ingest_companies` established), and
  **the one thing companies didn't need: `max_new`** — callers pass ids already in priority order
  and the cap truncates the tail, so a 35.8k backfill self-completes over several nights instead of
  blowing the 90-min nightly budget. **Silver:** new `etl/silver/transform_people_details.py` sweeps
  **every** `bronze/person_details/` partition and writes two Parquets — `person_details.parquet`
  (one row/person: biography, birthday, deathday, place_of_birth, homepage, imdb_id — `""`→None,
  dates `errors="coerce"` then kept-with-a-null on the outlier rather than dropping the row) and
  `person_aliases.parquet` (one row per `(person_id, alias)` exploded from `also_known_as`, its list
  index kept as `ordering`). **Warehouse:** `17_person_details.sql` — six `ADD COLUMN IF NOT EXISTS`
  on `dim_person` + non-unique `imdb_id` index + new `person_alias(person_id FK, alias, ordering,
  ingestion_date, PK (person_id, alias))`; folded into `01_dimensions.sql`. `load_dim_person(df,
  details_df=None)` gained the optional LEFT-joined second source (exact `load_dim_company` shape);
  new `load_person_alias()` in `load_facts.py` beside the bridge loaders, FK-resolves against
  `dim_person` and quarantines misses. `warehouse_checks.py` +1 FK check + a row-count-sanity pair.
  Wired into **both** `run_pipeline.py` (new `_extract_person_ids()` — billed cast `order<10` +
  directors/writers lead, photo-havers only) and `run_refresh.py`. **Django:** six nullable fields
  on `Person` + a new `Alias` model; `_person_header.html` gained a Born/Died/Born-in `.record-list`
  (each row guarded on its own); `person_detail.html` gained a `.specimen-synopsis` bio + one
  combined "Elsewhere" row (IMDb `name/…` + homepage, `·`-separated). **No `views.py` change** — the
  `Person` the view already fetches carries the fields; the template reads them. **Zero new CSS.**
  `17_person_details.sql` applied to the **local replica** (verified: `dim_person` 13 cols,
  `person_alias` exists); ORM + Django `check` clean. `pytest` **324 → 343** (+19). **Still to run
  (needs live TMDB + AWS + Neon, ~2.3h, not doable from this session):** `17_person_details.sql` on
  Neon; the priority-ordered Bronze backfill (`python -m etl.bronze.ingest_people` via a
  `run_refresh` pass, or a `nightly-refresh` `workflow_dispatch` that now carries it); then Silver +
  `load_dimensions()` against Neon and re-sync the replica. Record the real coverage figures (not
  the n=200 estimates) here once it runs. Nothing renders a real bio until then.

---

## Additional Reference

Full design rationale and original architecture decisions: `docs/architecture.md`
Learning log (updated after every task): `for_learning.md`

---

### Feature — Trailers and clips on the movie page

> Raised by user request on 2026-09-01. A film page shows a poster, a backdrop strip, prose and
> records, but nothing moving. TMDB carries video metadata for effectively the whole catalog and
> **it arrives inside a payload we already fetch**, so this is the rare feature that costs no new
> API calls, no new secret and no new dependency — the same shape as the Phase 15 IMDb finding.
>
> **Measured live before planning (2026-09-01), so nothing here is assumed:**
>
> | fact | value |
> |---|---|
> | endpoint | `GET /movie/{id}/videos` → `{id, results[]}` |
> | payload keys | 10, **all always present** across 2,434 sampled rows (no optional fields) |
> | `append_to_response=credits,videos` | returns a `videos` block **byte-identical** to the standalone call (verified on Inception) — so **zero extra API calls** |
> | sample | 150 of 1,217 catalog films, 30.9s (~4.9 req/s) |
> | ≥1 video of any type | 148 / 150 (**98.7%**) |
> | ≥1 `type=Trailer` on YouTube | 148 / 150 (**98.7%**), of which `official:true` on 114 (76.0%) |
> | ≥1 Clip/Featurette/BTS/Bloopers | 123 / 150 (**82.0%**) |
> | films with zero videos | 2 — *365 Days* (664413), *On the Beach* (405871) |
> | avg videos per film | **16.2** → ~19,700 rows catalog-wide |
> | extras per film that has any | median **7**, mean 12.8, **max 89** |
> | types | Featurette 784, Teaser 546, Clip 539, **Trailer 317**, Behind the Scenes 238, Bloopers 10 |
> | sites | YouTube 2,431, **Vimeo 3** (all unofficial, all pre-2020) |
> | `size` | video **resolution** (1080/2160/720/480/360), *not* duration — TMDB publishes no duration |
> | `id` | 24-char hex, **2,434/2,434 distinct**; `key` also 2,434/2,434 distinct |
> | result ordering | **newest-first by `published_at`**, 132/132 multi-video films (none ascending, none unordered) |
>
> **IMDb was checked and has nothing to offer here.** Its bulk datasets are exactly seven files
> (`title.basics`, `title.akas`, `title.crew`, `title.principals`, `title.episode`,
> `title.ratings`, `name.basics`) — listed live, **none carries video data**. There is no public
> IMDb API, and the `vi…` id behind an IMDb trailer appears in none of the datasets, so there is
> no join key even in principle. IMDb stays what Phase 15 made it: the rating of record, nothing
> more. Recorded here so it is not re-researched.
>
> **Backfill is not possible and does not need to be.** Bronze is immutable, and every existing
> `movie_details` partition was written before `videos` was appended — those payloads have no
> `videos` key and will never gain one. The Silver transform must therefore treat a missing
> `videos` key as **zero rows plus a warning, never a crash**, and the feature lights up on the
> first partition written after Task 73 lands (the nightly `run_refresh` does this unattended).
> This ordering is real: **Task 75 cannot be live-verified until a post-Task-73 partition exists.**

#### [ ] Task 73 — Bronze + Silver: carry `videos` through the payload we already fetch
- **Goal:** Get the video metadata into Silver without a single new TMDB call.
- **Files:** `etl/tmdb_client.py` (no change expected — verify), `etl/bronze/ingest_movie_details.py`,
  `etl/bronze/refresh_movies.py`, new `etl/silver/transform_movie_videos.py`,
  `data_quality/silver_checks.py`, `scripts/{run_pipeline,run_refresh}.py`,
  `tests/{test_etl,test_data_quality}.py`
- **Steps:**
  1. **Both Bronze paths append `videos`, and they are currently asymmetric — check both.**
     `ingest_movie_details()` calls `client.get_movie_details(movie_id)` with **no**
     `append_to_response` (it makes a second call to `/credits` via `ingest_credits`), while
     `refresh_movies()` already passes `"credits"`. So: ingest passes `"videos"`, refresh passes
     `"credits,videos"`. Neither adds a request — both already make that exact call.
  2. **`videos` stays inline in the `movie_details` JSON; it does not become its own Bronze
     entity.** This is deliberate and differs from `credits`, so write down why: `credits` is
     split out by `refresh_movies._split_payload()` only because a standalone `bronze/credits/`
     entity already existed from `ingest_credits()` and the two paths must produce the same
     layout. Videos has no prior entity, and a nested array inside a movie payload is exactly
     what `production_companies`/`spoken_languages` already are — `transform_movie_links.py`
     reads them straight out of `bronze/movie_details`. Follow that precedent, not the credits
     one. `_split_payload()` therefore keeps `videos` on the details file and strips only
     `credits`, exactly as today.
  3. New `etl/silver/transform_movie_videos.py`, modelled on `transform_movie_links.py`: one
     pass over `bronze/movie_details` for the date (via `s3_utils.read_json_objects()`, the
     Task 64 Step 8 threaded reader — **not** a serial `get_object` loop), writing
     `silver/movie_videos/movie_videos.parquet` with
     `(movie_id, video_id, name, key, site, type, official, size, iso_639_1, iso_3166_1, published_at)`.
     Dedup key `(movie_id, video_id)` — its true grain, per Task 40 do not widen it "to be safe".
  4. **A payload with no `videos` key yields zero rows and one warning, never an exception** —
     every partition written before this task is in that state permanently (Bronze is immutable).
     A partition where *no* file has the key must still produce a valid empty Parquet with the
     right columns, so the loader downstream has something well-formed to read.
  5. Drop null-`movie_id`/null-`video_id` rows with a warning, same as `_write_link_table()`.
  6. New `ENTITY_CONFIGS["movie_videos"]` in `silver_checks.py`, **written from the measured
     payload shape in the table above** — all 10 keys always present, `site` and `type` from the
     observed vocabularies — not by mirroring the transform. That is the Task 40 lesson: a check
     copied from the transform confirms its bugs instead of catching them.
  7. Wire `transform_movie_videos()` into **both** `run_pipeline.py` and `run_refresh.py`, after
     `transform_movies`. Task 65's gap is the warning here: a transform wired into only one
     orchestrator is silently absent from the nightly path that actually keeps the site current.
- **Verify:** on a fresh partition — ~19,700 Silver rows across ~1,215 films, ~16/film; every
  `video_id` 24 hex chars; `site` ∈ {YouTube, Vimeo}; ~98.7% of films have ≥1 `type='Trailer'`;
  Silver DQ rises 32/32 → 36/36. Re-running the transform on an **old** partition (no `videos`
  key) writes an empty, well-formed Parquet and logs a warning rather than raising.
- **Outcome:**

#### [ ] Task 74 — Warehouse: `dim_movie_video`, and the project's first replace-on-load table
- **Goal:** Land the videos in Postgres with a load strategy that lets a film's video set *shrink*.
- **Files:** new `warehouse/ddl/18_movie_videos.sql`, `warehouse/ddl/01_dimensions.sql`,
  `etl/warehouse_loader/{common,load_dimensions}.py`, `data_quality/warehouse_checks.py`,
  `tests/{test_etl,test_warehouse_checks}.py`
- **Steps:**
  1. **The naming call, and it is not the one the last three tables used.** This table is neither
     a `fact_` (no measure — `size` is a resolution attribute, not something you sum or average)
     nor a `bridge_` (a bridge joins *two* dimensions; there is no `dim_video` and nothing will
     ever join to one). It is a **multi-valued attribute of `dim_movie`**: one film, many videos,
     each carrying only descriptive columns, read exclusively by joining down from `dim_movie`.
     That is dimension-shaped, so **`dim_movie_video`**. Record the rejected alternatives in the
     DDL header the way `15_movie_ratings.sql` does, so the choice reads as reasoned rather than
     careless: `bridge_` would assert a second dimension that does not exist, `fact_` would
     promise a measure that is not there.
  2. `dim_movie_video(movie_id FK, video_id VARCHAR(24), name TEXT, key TEXT, site TEXT, type TEXT,
     official BOOLEAN, size INTEGER, iso_639_1 VARCHAR(8), iso_3166_1 VARCHAR(8),
     published_at TIMESTAMPTZ, ingestion_date DATE)`, PK `(movie_id, video_id)`.
     **PK on TMDB's `video_id`, not on `key`** — both measured 2,434/2,434 distinct, but `key` is
     only unique *within a site* (a YouTube id and a Vimeo id share no namespace), while
     `video_id` is TMDB's own stable identifier for the row. Index on `(movie_id, type)`: every
     read is "this film's trailers" or "this film's clips".
     Same five columns added to `01_dimensions.sql` for a fresh bootstrap (Task 58/61/67 kept the
     bootstrap current; the fresh-install check in Task 76 depends on that habit holding).
  3. **`load_dim_movie_video()` must replace, not upsert, and this is the one genuinely new
     loader pattern in the feature.** Every existing loader calls `common._upsert()`, which can
     add and update but never delete. Videos are the first entity here that *shrinks*: TMDB
     removes videos, and YouTube keys rot when an upload is deleted or made private — a pure
     upsert would leave a dead embed on the page forever, silently, with no failing check.
     Add `_replace_by_parent(session, table, parent_col, parent_ids, columns, records)` to
     `etl/warehouse_loader/common.py`: `DELETE FROM <table> WHERE <parent_col> = ANY(:ids)`
     for **only the movie_ids present in this partition**, then a batched insert.
     **Scoped delete, never a blanket `TRUNCATE`** — a partition covers the films it ingested, and
     wiping films absent from it would destroy data the run knows nothing about.
  4. Resolve `movie_id` against `dim_movie` and **quarantine** unresolvable rows to
     `data_quality/rejected/`, never drop them — the standing rule since Task 58.
  5. Load from `load_dimensions.py` (it is a `dim_`), reading `silver/movie_videos` in a
     `try/except` so a partition written before Task 73 degrades to "no videos loaded" instead of
     crashing the nightly job — exactly the posture `load_dimensions()` already takes for
     `company_details`.
  6. `warehouse_checks.py`: one `_FK_CHECKS` entry (`dim_movie_video.movie_id → dim_movie`), a
     row-count-sanity check comparing against Silver's `nunique(movie_id)` (the Task 58 fix —
     this is a one-to-many table, so a raw row-count comparison would fail on every film with more
     than one video), and a load-sanity check.
  7. **Add a replace-semantics regression test.** Load a film with 3 videos, then load the same
     film with 2, and assert the warehouse holds **2** — not 3, and not 5. The whole reason this
     table does not use `_upsert` is invisible in the code once written, so it needs a test that
     names it, the same way Task 67 tested its grain.
- **Verify:** ~19,700 rows, 0 rejects; `SELECT COUNT(DISTINCT movie_id)` ≈ 1,215; The Godfather
  holds 26 rows; the 2 zero-video films hold 0 rows and are not errors; warehouse checks
  39/39 → 42/42. Apply the DDL to **both Neon and the local replica** before the loader runs
  (the replica sync is data-only — the Task 65 lesson).
- **Outcome:**

#### [ ] Task 75 — Django: the trailer, and a Clips section
- **Goal:** One trailer playing on the film page, and a Clips section under it for the 82% of
  films that have extras.
- **Files:** `django_app/movies/{models,views}.py`,
  new `movies/templates/movies/_video_embed.html`, `movie_detail.html`,
  `django_app/static/css/theoria.css`, `django_app/static/js/theoria.js`,
  `tests/test_django_views.py`
- **Steps:**
  1. `MovieVideo` model, `managed = False`, fake single PK on `movie` — the same treatment every
     composite-PK table here gets, with the comment explaining why.
  2. **The trailer pick is a 3-step ladder, and it lives in the view, not the loader.** Official
     YouTube Trailer → any YouTube Trailer → any YouTube Teaser, **newest `published_at` first**
     at each step. Measured: this resolves for **148/150** films — it never does worse than raw
     trailer coverage, and the two misses are the two films with no videos at all. TMDB already
     returns newest-first, but **order by `published_at DESC` explicitly rather than trusting
     insertion order** — nothing in the warehouse preserves array position, and "it happened to
     come back sorted" is not a guarantee. Picking in the view (one query, ~16 rows, chosen in
     Python) rather than the loader is the Task 56 judgment: which trailer to *show* is a
     rendering decision, and freezing it into a column would mean a re-load to change it.
  3. **The Clips section is everything that is not the chosen trailer**, filtered to
     `site='YouTube'`, ordered newest-first. Coverage 82%; median 7 per film but **max 89**, so it
     must page — reuse `_pager_client.html`, the same in-browser pager the Cast and Crew sections
     already use. Group by `type` or show flat? Show flat, newest-first: the four extra types
     (Clip / Featurette / Behind the Scenes / Bloopers) are a TMDB taxonomy, not something a
     reader is shopping by, and four sub-headings on a film with two videos is chrome.
     Render each video's `type` as a small label on the card so the distinction is still visible.
  4. **The 3 Vimeo rows are stored but not rendered.** A second embed path for 0.1% of rows that
     are all unofficial and all pre-2020 is not worth the template branch; the `site` column is in
     the warehouse so a later task can change its mind. Say this in the view, or someone will read
     the `site='YouTube'` filter as an accident.
  5. **Embeds are click-to-play, not 20 live iframes.** Ship a YouTube thumbnail
     (`https://img.youtube.com/vi/<key>/hqdefault.jpg` — verified live, 200, 11.7 KB) inside a
     `<button>`, and swap in a `<iframe src="https://www.youtube-nocookie.com/embed/<key>?autoplay=1">`
     on click. `youtube-nocookie.com` verified live (200). This keeps the page weight flat
     regardless of clip count, the same reasoning `_pager_client.html` already applies to cast
     headshots. **This is the feature's only new JS** — a small `initVideoEmbeds()` in
     `theoria.js`, in the same delegated-listener style as `initLiveFilter()`/`initMeters()`.
  6. New `_video_embed.html` partial shared by the trailer and the clip cards, rendering
     **nothing** when there is no video — the Task 56/68 "render only when there's something to
     say" rule. A film with no trailer shows no trailer block, no empty frame and no placeholder;
     a film with no extras shows no Clips section at all.
  7. **CSS is additive only** (Task 38's contract): a `.video-frame` with `aspect-ratio: 16/9`
     and a play affordance, plus clip-card rules. `.backdrop-strip` is the closest existing
     precedent (`aspect-ratio: 32/9`) — match its idiom, do not restyle it. Decide where the
     trailer sits relative to the backdrop strip: a trailer and a backdrop are two big pieces of
     art competing for the same slot, so **the trailer takes that position and the backdrop stays
     only when there is no trailer** is the likely right answer — confirm it against a rendered
     page, not in the abstract.
  8. Accessibility: the play button needs an accessible name naming the video
     (`aria-label="Play trailer: <name>"`), the thumbnail `alt=""`, and the swapped-in iframe a
     `title`. A bare thumbnail with a play triangle is silent to a screen reader — the same gap
     Task 68 fixed for the IMDb badge.
- **Verify:** `/movies/the-godfather/` plays a trailer and lists its extras;
  `/movies/365-days/` (zero videos, id 664413) renders neither block and 200s, not an error;
  the film with 89 extras pages in-browser without shipping 89 iframes; **query count is flat** —
  one extra query on `movie_detail`, verified by counting, not by inspection (the Task 68
  standard); all routes 200, bad slug 404; both themes checked.
- **Outcome:**

#### [ ] Task 76 — Live run, verification, doc truth-up
- **Goal:** The closing task, following Tasks 44, 53, 63 and 69.
- **Files:** `README.md`, `docs/architecture.md`, `CLAUDE.md`, `for_learning.md`
- **Steps:**
  1. **This phase's live run is not optional, unlike Task 69's.** Task 73 changes what Bronze
     contains, and no existing partition has it — so a real `run_refresh.py` (or a green
     `nightly-refresh` `workflow_dispatch`) is the *only* way videos reach the warehouse at all.
     Run it, against Neon, then sync the replica.
  2. Confirm the nightly path carries it unattended: a second run must **replace** a film's video
     set, not accumulate it (the Task 74 delete). Check one film's row count across two runs.
  3. Fresh-install check, empirically: a scratch DB from `01`–`03` must produce the live table
     list — now **18 tables** (17 if Task 72's `person_alias` has not landed yet). Per the Task 53 lesson, do not infer this from the README.
  4. Record the new DQ totals (Silver 32→36, warehouse 39→42) so a future reader does not misread
     the rise as drift.
  5. Full route walk, then docs: `docs/architecture.md` gets a new §3.10 (why videos ride the
     existing payload; why `dim_movie_video` is neither `fact_` nor `bridge_`; why this table
     replaces where every other one upserts), `README.md`'s warehouse table (+1 table) and test
     count, this file's Warehouse Schema section and Phase Map, and `for_learning.md`.
- **Outcome:**
