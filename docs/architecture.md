# Theoria — Architecture & Decision Log

This is the canonical record of *why* Theoria is built the way it is — every non-obvious
architectural or technical decision, the alternative it replaced, and the evidence that drove the
change. It's written for someone evaluating the design (e.g. an interviewer) rather than someone
modifying the code line-by-line.

It is deliberately not the only document that touches history, but it is the one meant to answer
"why does it work this way" on its own, without cross-referencing the others:

- **`CLAUDE.md`** is the task-by-task build log — what was done, in what order, with live
  verification numbers. Useful for "when did X happen" or "what was task 46." Decisions narrated
  there get folded into this file rather than left to live only in a task's outcome paragraph.
- **`for_learning.md`** (local only, not tracked in git) is a teaching log — plain-language
  explanations of the DE/Python/SQL concepts each task used, aimed at re-explaining the work in an
  interview, not at justifying the choices made.

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
   │  genre metrics, decade stats, filmography, director ratings, collaboration edges
   ▼
PostgreSQL warehouse (star schema)
   │  dimensions + facts, upserted, watermark-tracked
   ▼
Django UI (read-only)
   movie/person/genre pages + analytics dashboard
```

Each arrow is a separate, independently testable stage with its own module
(`etl/bronze/*`, `etl/silver/*`, `etl/gold/*`, `etl/warehouse_loader/*`). `scripts/run_pipeline.py`
sequences all of them in-process for one `ingestion_date` — but every stage function can also be
imported and run on its own, which is what the unit tests do (mocking S3/TMDB/Postgres at the
boundary rather than mocking business logic).

### 2.1 Choosing the corpus: `movie/popular` vs `discover/movie`

Ingestion has two selectable sources (`scripts/run_pipeline.py --source`), and the choice is a
modelling decision rather than a tuning one.

`movie/popular` (`etl/bronze/ingest_movies.py`) returns whatever TMDB is featuring at the moment
you call it. It is fine for a "what's hot now" feed, but as the *only* source it produced a
catalogue of 112 films of which 77 were from the 2020s — so every trend-over-time query was
describing a dataset that barely had a time axis. No downstream transform can fix that: a
perfectly correct pipeline over a biased sample yields perfectly correct, biased answers.

`discover/movie` (`etl/bronze/ingest_discover.py`) accepts filters, so the population can be
*defined*: "the most-voted films of each release year from `DISCOVER_START_YEAR` to
`DISCOVER_END_YEAR`, with at least `DISCOVER_MIN_VOTES` votes". Because the endpoint caps how deep
a single query can page, the crawl partitions its **request predicate** by year and pages within
each window — the same technique used to split a large database read by key range. The current
warehouse holds ~1,200 films spread evenly at roughly 200 per decade.

The four `DISCOVER_*` values in `config.py` therefore define the dataset, not the runtime:
lowering `DISCOVER_MIN_VOTES` doesn't make the pipeline slower, it makes the warehouse describe a
different population.

### 2.2 Why S3, and why three layers instead of loading straight into Postgres

- **Bronze is the immutable source of truth.** Raw API responses are never edited in place. If a
  transform bug is discovered later, Silver/Gold can be rebuilt from Bronze without re-hitting the
  TMDB API. This is the standard "raw zone" pattern in a data lake.
- **Silver is where correctness lives.** Flattening nested JSON, casting types, deduplication, and
  the data-quality gate (`data_quality/silver_checks.py`) all happen here — once, in one place —
  rather than being re-implemented ad hoc by every downstream consumer.
- **Gold exists for read patterns that don't map cleanly onto the star schema**, or that are
  expensive to recompute per-request (e.g. the collaboration graph, §3.3). The warehouse is the
  primary read path for Django; most of Gold's output isn't loaded into Postgres (only
  `collaboration_edges` is, as of Task 49) — the rest mainly demonstrates the aggregation step
  you'd wire into a warehouse load in a larger system.

### 2.3 Why partitioning by `ingestion_date`

Every layer's S3 key is `s3://<bucket>/<layer>/<entity>/ingestion_date=YYYY-MM-DD/<file>`. This
gives:
- **Idempotent re-runs** — re-running a stage for a date overwrites only that date's partition.
- **Incremental processing** — `etl/incremental.py` lists partitions present in S3 and compares
  them against a stored watermark to find only the *new* ones (see §4).
- **Natural backfills** — if TMDB data for a past date needs reprocessing, only that partition is
  touched.

## 3. Warehouse schema (star schema)

```
                    ┌───────────┐                 ┌────────────────┐
                    │ dim_date  │                 │ dim_collection │
                    └─────┬─────┘                 └───────┬────────┘
                          │                               │ (nullable)
┌───────────┐       ┌─────┴──────────────┐        ┌───────┴────┐
│ dim_genre │──────▶│ fact_movie_metrics │◀───────│ dim_movie  │
└───────────┘       └────────────────────┘        └─────┬──────┘
                                                        │
                         ┌─────────────┐                │
┌────────────┐           │ fact_credit │◀───────────────┘
│ dim_person │──────────▶│             │
└─────┬──────┘           └─────────────┘
      │                  ┌────────────────────┐
      └─────────────────▶│ fact_collaboration │  (both FKs -> dim_person)
                         └────────────────────┘
```

**Dimensions** (`warehouse/ddl/01_dimensions.sql`): `dim_movie`, `dim_person`, `dim_collection`,
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
  `SELECT DISTINCT movie_id, ...` in a CTE before aggregating, and every Django view that reads a
  movie-level measure off this table (`movie_detail`'s rating, `actor_detail`/`director_detail`'s
  avg rating) applies the same `.values(...).distinct()` guard.
- `fact_credit(movie_id, person_id, department, job, character_name, ordering, ingestion_date)` —
  one row per credit, at the grain TMDB actually publishes. A director who also wrote and produced
  a film is three rows, and the PK says so.
- `fact_collaboration(person_a_id, person_b_id, films_together, first_year, last_year)` — derived
  in Gold rather than loaded from Silver; see §3.3.

All fact tables carry named foreign keys to every dimension they reference, and an index on each
FK column (PostgreSQL does not auto-index FKs). All also carry an `ingestion_date` column purely
for audit/traceability — it is *not* part of a uniqueness constraint, because legitimate data has
multiple rows per `(movie_id, ingestion_date)` (one per genre, one per credit). Duplicate-guarding
instead comes from the composite primary key plus `ON CONFLICT DO UPDATE` upserts in the loaders —
idempotent by construction, not by an extra constraint.

### 3.1 Resolved: `fact_cast`/`fact_crew` replace the earlier `fact_casting` cross-join

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

Both tables were themselves retired in Phase 10 by `fact_credit`, which models every credit
regardless of department (§3.2). They are described here because the *reasoning* still applies:
coupling two independent facts in one table makes one of them disappear whenever the other is
missing.

### 3.2 One person, every credit

`dim_actor` and `dim_director` split one human being across two tables according to whichever
credit happened to introduce them, and `fact_cast`/`fact_crew` did the same to their work. The
cost was not stylistic. `transform_people` decided who existed with a single line —
`if member.get("job") == "Director"` — and that line excluded **79,523 people** whose credits were
already ingested and sitting in Bronze. The loader then discarded **169,682 of 170,915 crew
credits** (99.3%) to keep only directors. Every editor, composer, cinematographer, production
designer and writer in the catalog was unrepresentable.

`dim_person` + `fact_credit` replace all four tables. Identity belongs to the person; what they
did on a given film is a *fact*, not an attribute of who they are. The warehouse went from 45,231
people and 64,031 credits to **122,685 people and 237,454 credits across 13 departments and 858
distinct job titles** — with no new API calls, because Bronze had held all of it since the first
ingestion run.

Unifying the identity also meant unifying the *namespace* Django addresses people by — see §7.2
for the URL consequence and the redirect design it forced.

### 3.3 The collaboration graph, and why the reduction matters more than the schema

`fact_collaboration` counts how often each pair of people has worked together. The scoping
decision matters more than the schema: pairing *every* credit on every film produces **33.1
million** edges on this corpus, and asserts that a caterer and a stunt double "collaborated".
Restricting to **key credits** — top-10 billing plus nine principal craft jobs — gives **181,538**.
That 180× reduction comes from deciding what a collaboration *is*, not from a `LIMIT`; the
constants live in `build_gold_datasets.py` as a definition, the same way `config.DISCOVER_*`
defines the corpus.

`fact_collaboration` is also the first thing in the project that **reads** the Gold layer. Gold had
been written on every pipeline run since Task 14 and consumed by nothing. The honest test for
whether a dataset belongs there is: expensive to compute, cheap to serve, and shaped for a read the
star schema can't answer directly. A quadratic expansion over every film is all three; the other
four Gold datasets fail that test, which is exactly why nothing reads them.

### 3.4 Franchises: `dim_collection`

`belongs_to_collection` was present in every Bronze movie-detail payload from the first ingestion
run and read at no layer until Task 50. It was promoted to its own dimension rather than left as
three denormalized columns on `dim_movie` (`collection_id`/`name`/`poster_path`) because a
franchise has its own identity, artwork, and slug — 17 Bond films would otherwise repeat the same
name, poster path, and slug computation 17 times with no single row to canonicalize it.

`dim_movie.collection_id` is a **nullable** FK: roughly half the catalog belongs to no franchise,
which is a property of films rather than missing data, so the schema doesn't force a sentinel
"no collection" row to avoid a null. `load_dim_collection()` runs before `load_dim_movie()` (the FK
points that way) and derives the dimension by de-duplicating Silver's already-denormalized
`collection_id`/`collection_name` columns — filtered on both id *and* name, since an id without a
name would violate `name NOT NULL` on rows where TMDB's payload was partially populated.

### 3.5 Fields that were already in Bronze and never reached the page

Two smaller gaps followed the same shape as §3.2 and §3.6: data TMDB had always returned, sitting
in Bronze, that no Silver column or warehouse column ever carried forward.

- **Images and taglines** (Task 36): `poster_path`, `backdrop_path`, `tagline` (movies) and
  `profile_path` (people) were being computed by nothing — the Silver transforms simply didn't
  extract them. Added to `_flatten_movie()`/the people transform and to `dim_movie`/`dim_person`
  with an idempotent `ADD COLUMN IF NOT EXISTS` migration, then backfilled by re-running Silver and
  the dimension load from immutable Bronze — no new TMDB calls.
- **Synopsis and score** (Task 41): `overview` was extracted by `transform_movies`, wrote
  successfully to Silver, and was even listed in `silver_checks`' expected schema — but `dim_movie`
  had no `overview` column, so the loader's explicit column list (a deliberate discipline: never
  `SELECT *`, never insert an implicit column list either) silently had nowhere to put it. This is
  the failure mode that discipline trades for: not selecting a column reads identically to not
  having one, so a schema gap here fails silently rather than loudly. Fixed by adding the column
  and re-running the existing loader — the rating (`fact_movie_metrics.rating`) had a column all
  along; it simply had never been read into a movie-page context.

### 3.6 Resolved: the credits-bridge dedup grain

A second, subtler bug lived in `etl/silver/transform_credits_bridge.py`, which deduplicated crew
rows on `(movie_id, person_id, credit_type)`. `credit_type` is only ever the literal `"crew"` — it
does not distinguish jobs — so any crew member holding more than one job on a film collapsed to a
single row, `keep="last"` deciding which. Directors very often also produce or write their own
films, so the surviving row was frequently *not* the "Director" one. The result: raw Bronze had a
director for 99 of 99 films, while only 47 reached `fact_crew`, and the "Top Rated Directors"
dashboard panel (which requires ≥3 films per director) was permanently empty.

The fix was to include `role` in the dedup key, so it matches the true grain of a credit: *a person
can hold several distinct jobs on one film, and each is a real fact.*

Two things are worth noting beyond the one-line change:

- **The data-quality check shared the bug's premise.** `silver_checks.py` validated uniqueness on
  the *same* wrong key, so rather than catching the error it confirmed the corrupted data looked
  correct. A check that encodes the same assumption as the code it checks cannot detect a fault in
  that assumption.
- **Every test and check passed throughout.** Nothing was corrupt — there were simply fewer rows
  than there should have been. The existing `bronze_to_silver` row-count checks compare *totals per
  entity*, which is why 9,282 crew rows in and 9,282 out looked perfectly healthy while the wrong
  52 directors were missing among them. Catching this class of bug needs *reconciliation* controls
  that assert a conserved quantity across a layer boundary, not just internal consistency.

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

### 4.1 Resolved: a batched slug rewrite that failed on a permutation, not a collision

`assign_slugs()` (§7.1) recomputes and rewrites an entire table's slugs in one batched
`executemany`. Postgres checks a unique index **after every row**, not after the statement, so
when a live run swapped which of two rows held a given slug (row A giving up `dee-wallace`, row B
taking it, in one batch), the intermediate state — both wanting it briefly ordered wrong — raised
a `UniqueViolation` even though the *final* table was perfectly unique. This had been latent since
Task 46; it never fired earlier because those tables had only ever been loaded in ways where
recomputed slugs happened to be identical. The fix is to clear the column for the affected rows
before rewriting it, inside the same transaction, so no batch ever asks Postgres to hold two
half-written unique values at once. Found by a live run, not a test — the bug only exists under
real concurrent-looking writes, which a mocked-session unit test can't reproduce.

### 4.2 The nightly refresh: a separate path for films already in the warehouse

Every Bronze ingest module sources its `movie_id`s from a *discovery* endpoint — `movie/popular`,
`discover/movie`. Nothing sources them from `dim_movie`. So a film already in the catalogue has
no refresh path: its rating, vote count and revenue are frozen at whatever they were the day its
partition was written, and the only way to move them is a full re-discovery run that also happens
to re-fetch everything.

**`scripts/run_refresh.py` is that missing path, and it is a separate orchestrator, not a flag on
`run_pipeline.py`.** Ingest *discovers* films; refresh *updates* known ones. Conflating the two
verbs behind one `--refresh` switch is what produced the gap in the first place. `run_refresh`
reuses every Silver, Gold and warehouse stage unchanged — the only substitution is at the head:
`etl/bronze/refresh_movies.py` reads `SELECT movie_id FROM dim_movie ORDER BY movie_id` and
writes the *same* `bronze/movie_details/` and `bronze/credits/` key shapes the ingest modules
write, so nothing downstream can tell the difference.

**One TMDB call per film, not two.** `TMDBClient.get_movie_details(..., append_to_response="credits")`
returns the detail payload with `credits` folded in; `refresh_movies` splits it back into the two
Bronze files. TMDB rate-limits per request, so this halves the call volume of the refresh (and is
available to the ingest path too). Measured throughput is ~4.76 req/s, so the full ~1,215-film
catalogue refreshes in roughly 4–5 minutes of API time.

**Why the volatile-metrics snapshot goes to S3, not a warehouse table.** `fact_movie_metrics`
has PK `(movie_id, date_id, genre_id)` where `date_id` is derived from the film's *release* date;
`ingestion_date` is only a column. A refresh therefore upserts each row in place and the previous
rating is gone — the warehouse holds "latest", never a history. `build_metrics_snapshot` writes
one row per film per run (`movie_id, snapshot_date, rating, vote_count, revenue, popularity`) to
`gold/metrics_snapshot/ingestion_date=…/` *before* the warehouse load overwrites those values.
Keeping this in Postgres would grow ~1,215 rows/day unbounded against a 0.5 GB managed tier, and
history-of-measurements is exactly what the lake layer is for. This is what makes "rating over
time" or "revenue still accumulating after release" answerable later without schema change.

**Why not drive the refresh from TMDB's `/movie/changes` feed.** It looks like the right tool —
a daily list of what changed — but a live probe of 400 changed films found it reports `status`,
`runtime`, `budget`, `revenue`, cast/crew and images, and **never** `vote_average`, `vote_count`
or `popularity`. TMDB excludes those by design because they move on nearly every film daily. The
changes feed's real job is discovery and structural edits (handled by the weekly
`run_pipeline --source discover`); it cannot see the two fields that actually go stale, so the
refresh re-fetches every film rather than trusting a feed that omits the point.

**Why Neon + GitHub Actions and not an AWS-native scheduler.** `config.py` already reads a single
`DATABASE_URL` and every stage is already idempotent per `ingestion_date`, so moving the
warehouse to a managed Postgres and running the existing scripts on a cron is an environment
change plus two YAML files — no new infrastructure, which keeps faith with §11's non-goals. ECS
Fargate + EventBridge, or self-hosted Airflow, would buy orchestration this project does not need
at this size. One operational wrinkle: GitHub silently disables scheduled workflows on a public
repo after 60 days without repository activity, so each run appends a line to
`ops/refresh-history.md` and commits it — the commit is the activity that keeps the schedule
alive.

### 4.3 The local read replica

Once the warehouse moved to Neon in `eu-central-1`, Django — running on a laptop — paid a
~90 ms round-trip on *every* query. A `SELECT 1` that was ~0.1 ms on the old local socket became
~86 ms; pages issuing 3–7 queries went from tens of milliseconds to 2–6 seconds. Nothing was
wrong with the data; the database had simply moved a continent away.

The fix keeps the app on the laptop and gives it a local copy to read:

```
TMDB / IMDb ──▶ GitHub Actions nightly job ──▶ Neon         (source of truth, cloud)
                                                 │
                          scripts/sync_warehouse_from_neon.py   (run on demand)
                                                 ▼
                                          local Postgres   ◀── Django reads this
```

`sync_warehouse_from_neon.py` is a full point-in-time snapshot, not an incremental merge: it
truncates every warehouse table and reloads it from Neon inside one transaction, with FK triggers
disabled for the load (`session_replication_role = replica`) since the snapshot is already
internally consistent. ~624k rows copy in ~60 s.

The sync is not scheduled — the laptop is usually off at 03:12 UTC when the nightly job runs — so
`manage.py serve` folds it into starting the site: `sync_if_stale()` compares
`max(ingestion_date)` on Neon against the replica and reloads *only* when Neon is ahead. A normal
restart is one ~90 ms date query and nothing else; the 60 s cost is paid once, the first time you
start the site after a nightly run. If Neon is unreachable the check is logged and skipped, so the
site still starts on the existing replica. (A `systemd --user` timer with `Persistent=true` calling
`sync_warehouse_from_neon --if-stale` would make it fully hands-off, catching up after the machine
wakes; not set up, since `serve` covers the felt need.)

`pg_dump` is not used: the laptop's client is v16, Neon is v18, and `pg_dump` refuses to read
from a newer server. A plain `COPY … TO/FROM STDOUT` streamed through libpq has no such check.

Two `DATABASE_URL`s result, which is the point: locally it names the fast replica, and in the
GitHub Actions job it names Neon. `NEON_DATABASE_URL` (local-only) is the sync source. The cloud
pipeline is unchanged — it still writes Neon directly and never syncs. Hosting the app next to
Neon instead (co-located in `eu-central-1`) is the other way to erase the latency; the replica was
chosen because it costs nothing and keeps the site laptop-local. §4.4 takes the other route as
well, for a different reason — being reachable at all — and the two coexist.

### 4.4 Hosting the read layer on Vercel

The site deploys to Vercel as a single Python function: Vercel finds `django_app/manage.py`,
resolves the entrypoint from `WSGI_APPLICATION`, runs `collectstatic` at build time and serves the
result from its CDN. Only the read layer moves. The pipeline stays on GitHub Actions, and that
split is forced rather than chosen: Vercel's Hobby functions cap at 300 s and a full refresh runs
~25 minutes. It is also the right split — ingestion is a scheduled batch job, not a request.

**The region is the whole latency argument, restated.** §4.3 measured ~90 ms per query from the
laptop to Neon in `eu-central-1`, which is what the local replica exists to avoid. A function
running in Vercel's default `iad1` would reproduce exactly that, and worse: `home()` issues seven
queries, so a transatlantic hop per query would make the hosted site slower than the laptop was
before the replica existed. Pinning `regions: ["fra1"]` co-locates the function with Neon and
collapses the round-trip to the same order as a local socket — so the hosted site reads Neon
*directly* and needs no replica at all. The replica keeps its job for local development; the two
answer the same question ("how do we not pay 90 ms a query?") in the two places it gets asked.

**Configuration required knowing which process needs what.** `config.py` previously demanded every
variable of every importer, so the web function could not boot without TMDB and AWS credentials it
would never use — a deployment holding S3 *write* credentials to serve a read-only page. Variables
are now grouped by role (core / web / etl) and enforced by `require_web()` / `require_etl()` at the
point that role starts. Nothing became quieter: a missing key still stops the process before any
work happens. The symmetric win is that the nightly job no longer needs a `DJANGO_SECRET_KEY`,
a wart that had been recorded and tolerated since the job was written.

**Two requirements files, for a hard reason.** Python function bundles are capped at 500 MB and are
not tree-shaken — everything reachable at build time ships. pyarrow, pandas, numpy and botocore
alone are ~275 MB, and no view imports one of them. `requirements.txt` is now the web runtime only;
`requirements-etl.txt` includes it and adds the pipeline stack.

**The nightly job would otherwise redeploy the site every night.** The refresh workflow commits a
line to `ops/refresh-history.md` so that repository activity keeps GitHub from disabling the
schedule after 60 idle days (§4.2). That commit carries `[skip ci]`, which stops Actions —
**Vercel does not honour it**. Left alone, every nightly run would rebuild and redeploy the site
for a file no page reads, discarding warm instances so the next visitor pays a cold start on both
Vercel and Neon. `vercel.json`'s `ignoreCommand` diffs the paths the site actually serves
(`django_app/`, `warehouse/queries/`, `config.py`, `requirements.txt`) and skips the build when
none of them changed. Verified against real history: the run-3 commit and the Task 64 close-out
commit both skip; the replica and `manage.py serve` commits both build.

**Deploy order is the inverse of what a Django habit expects.** Django never migrates the warehouse
— every model is `managed = False` and `WarehouseRouter.allow_migrate` refuses it — so Vercel will
never apply a schema change. A new column means: apply the DDL to Neon, run the loader, *then*
deploy the reader. The reverse order 500s every request until the column exists. Preview
deployments read the production warehouse, which is safe only because the site cannot write to it;
that same read-only design is what makes sharing one database across environments acceptable here.

Everything environment-specific keys off the platform's own `VERCEL` variable rather than a flag
someone has to remember: `DEBUG` is forced off regardless of `DJANGO_DEBUG`, `ALLOWED_HOSTS` gains
`.vercel.app` (preview hostnames are generated per commit and cannot be listed in advance), secure
cookies and `SECURE_PROXY_SSL_HEADER` switch on, `CONN_MAX_AGE` holds the Neon connection across
requests on a warm instance, and `/admin/` is not routed — it has nothing to administer and no
database to authenticate against, so the alternative is a guaranteed 500 on a public URL.

**The first deploy failed on an assumption nothing local could expose.** `manage.py` lives in
`django_app/`, so running anything through it puts that directory on `sys.path` and
`theoria_site.settings` imports — every local path, tests included, inherits that for free. The
platform imports `wsgi.py` by file path from the repository root instead, where `theoria_site`
resolves to nothing: `ModuleNotFoundError`, 500 on every route including `/favicon.ico`. The fix is
for the entrypoint to state its own import root (`sys.path.insert` of `BASE_DIR` before Django
resolves the settings module) rather than depend on who launched it. Verified by loading the file
the way the platform does — by path, from the repo root, with `django_app/` absent from `sys.path`
— which reproduces the exact production error against the old file and returns 200s against the new
one. Worth noting as a class of bug: a layout assumption held by *every* local entry point is
invisible to a test suite that also enters through it.

`ManifestStaticFilesStorage` is used in production only. It content-hashes every asset, so a CSS
change takes effect immediately rather than waiting out a returning visitor's cached copy, and it
resolves names through a manifest `collectstatic` writes — which means it fails loudly if the build
step was skipped rather than quietly serving something stale.

The remaining cost is Neon's free tier, where scale-to-zero cannot be disabled: after five idle
minutes the next request pays a cold start, measured at 20–45 s on this project. For a low-traffic
site that is most first visits. It is a plan setting, not an architectural problem.

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

All three composite-PK fact tables (`fact_movie_metrics`, `fact_credit`, `fact_collaboration`) don't
fit Django's one-primary-key-per-model assumption. Each model marks its `movie` FK as
`primary_key=True` purely to satisfy that constraint — the real uniqueness lives only in the
database's actual composite PK, and the resulting `fields.W342` warning is intentionally silenced
in `settings.py` with a comment explaining why, rather than worked around with a fake single-column
surrogate key that doesn't exist in the table.

The **analytics dashboard** (`analytics/views.py`) takes a different approach from the `movies`
app: instead of expressing its ten queries through the ORM, it reads the `.sql` files in
`warehouse/queries/` directly and executes them via a raw cursor. This avoids maintaining the same
logic twice (once in `.sql`, once as ORM query-building) for queries — like the multi-CTE
director/craft partnership join — that are naturally SQL-shaped. Every dashboard query carries an
explicit `LIMIT` (§2.1's corpus-growth lesson: an unbounded query that was harmless at 112 films
returned 1,304 rows into a fixed-height panel once the catalog reached 1,215).

## 7. URL and page design

### 7.1 Slugs instead of surrogate keys

Movie and person detail routes are addressed by a URL slug
(`/people/tom-holland/`) rather than the warehouse's numeric primary key
(`/actors/880/` — Task 46). This is a UI decision with a real technical constraint behind it: a
slug column has to stay stable across reruns of a table that grows (44,554 → 122,685 people across
this project's own history), or every previously-shared link breaks the next time the loader runs.

`assign_slugs(session, table, id_col, name_col)` therefore **recomputes every row's slug from the
whole table** on each load, not just the newly-loaded partition — checking collisions against only
the current batch would let a same-named person introduced in a later partition silently collide
with an existing slug, and the database's unique index would reject the load outright rather than
resolve it. Collisions are broken by walking the table in ascending id order and numbering repeats
(`john-smith`, `john-smith-2`, …), which is what makes the scheme idempotent: since only a strictly
larger id can ever be appended after the existing numbering (never inserted ahead of it), a given
row's slug never changes once assigned, unless a same-named row with a *smaller* id is later
discovered. `_slugify()` NFKD-folds accented characters to their ASCII base ("Zoë Kravitz" →
`zoe-kravitz`) rather than dropping them, so a franchise's foreign-language cast doesn't collapse
to a run of hyphens.

See §4.1 for the batched-rewrite bug this scheme surfaced under a live run.

### 7.2 Unifying `dim_actor`/`dim_director` into `dim_person` renumbered slugs — legacy routes redirect by id, not by slug

Merging the actor/director tables into `dim_person` (§3.2) reused the same `assign_slugs()`
machinery over a much larger, merged population, which **changed which id claims a given base
slug** for 381 people (a crew member with a lower TMDB id now claims the slug an actor or director
used to hold alone). `/actors/` and `/directors/` survive as **scopes** of `/people/` —
`Person.objects.filter(credits__department=...).distinct()` — rather than separate tables, and
their legacy detail URLs **301 redirect by the row's stable integer id**, never by re-mapping old
slug to new slug: a slug-to-slug redirect table built before the rename would have sent some of
those 381 URLs to the wrong person, silently. Redirecting by id sidesteps the whole problem, at the
cost of the redirect needing one `dim_person` lookup rather than being a static rewrite rule.

## 8. Frontend: one design system, not one stylesheet per page

Through Task 37 the app had two disjoint visual languages — a dark themed Home/Analytics and
unstyled system-ui everywhere else — because every new page had shipped with its own ad hoc CSS.
Task 38 replaced that with a single token system (`static/css/theoria.css`: colour, type, space,
motion) under one explicit contract: a page-specific stylesheet may *add* a new component, but may
never restyle a shared one. Every `body.page-*` override that had accumulated was deleted, because
that pattern is exactly how the two-theme split happened in the first place — a page overriding a
shared class only for itself is a fork nobody remembers making.

Two consequences worth naming as decisions rather than aesthetics:

- **Colour is a signal, not a palette.** Lime is reserved specifically as the "this value was
  measured" mark — meter bars, keyed posters, active nav — so reaching for lime anywhere else
  would have diluted the one place it was meant to mean something.
- **Shared behaviour moved into shared JS.** `initMeters()`, number formatting, and (later) the
  paged-section behaviour in §9 all live in `static/js/theoria.js` so any page can opt in via a
  `data-*` attribute rather than a per-page `<script>` reimplementing the same loop.

## 9. Movie page legibility: merging duplicate credits, paging in the browser

`fact_credit` (§3.2) was the right data model and immediately made the movie page unreadable at
scale: a person holding several jobs on one film rendered once per job across several department
sections (Christopher Nolan on *The Dark Knight* was 4 rows spread across 3 sections), and every
credit — up to 139 cast, up to ~980 crew on the worst film — rendered as a headshot card with no
ceiling. These are two different problems with two different fixes, and conflating them would have
hidden which fix did what:

- **Duplication is fixed by merging, not by hiding rows.** `_merge_crew()` groups a film's
  non-Acting credits by `person_id` and joins their jobs in department order under that person's
  single most senior department (`_department_rank()`) — Nolan now reads "Director / Screenplay /
  Story / Producer," once. Measured before committing to this as the actual problem: merging
  collapses 143.8 crew rows/film to 138.1 distinct people (~4%) — **merging fixes duplication, not
  volume.**
- **Volume is fixed by paging in the browser, not by truncating the response.** No credit is
  hidden from the payload; `initPagedSection()` (§8) shows a window of ten at a time and repaints
  on Next rather than issuing a request. This directly replaced a first, server-side implementation
  (`?cast_page=`/`?crew_page=`/`?crew=all` query params, `#cast`/`#crew` anchors) that was judged
  not smooth enough once built — a round trip per ten people read as sluggish even though it
  worked correctly.

**The pager is intentionally not one shared component.** `_pager.html` (server-side, page-number
query params) still serves `/movies/` and `/people/` — 1,215 and 122,685 rows, not a payload a
browser should ever be handed in one response. `_pager_client.html` serves one film's ~1,200-credit
maximum, which is small enough to send whole and page client-side. Sending the same total row count
through both mechanisms would be wrong in both directions: server-paging a single film adds a
round trip an in-browser page doesn't need, and client-paging the full movie or person index would
ship five- and six-figure payloads to render ten rows.

## 10. Testing philosophy

The full suite (210 tests, `pytest`) never touches a real network, S3 bucket, or Postgres
instance. Every ETL/loader test mocks the boundary (the `boto3` client, the `requests` session, the
SQLAlchemy session) and asserts on the transformation logic itself. Django view tests construct
real (unsaved) ORM model instances and patch each model's `.objects` manager, using
`django.test.Client` against real URLs — this exercises real view/template code without a live
`warehouse` connection. This keeps the suite fast and runnable anywhere (CI, a fresh laptop) with
zero external dependencies, at the cost of not catching integration issues between the mocked
boundary and the real service — those are instead caught by the periodic live pipeline run
(`scripts/run_pipeline.py`, see its Task 30.5 outcome in `CLAUDE.md` for the first such run).

Two bugs in this project were found only by a live run, never by the mocked suite, and are recorded
here rather than only in `CLAUDE.md` because the pattern generalizes: §4.1's batched-slug
permutation (Postgres enforces uniqueness per row, not per statement) and an earlier `/connect/`
adjacency build that returned a different — equally short, equally valid — shortest path on every
reload, because an unordered SQL read combined with Python's insertion-order-preserving dicts made
iteration order accidentally significant. Both are the same lesson from two different angles: a
mocked session can't reproduce behaviour that only exists under a real database's actual execution
order.

## 11. Explicit non-goals

No Spark, Kafka, Snowflake, Redshift, Lambda, Terraform, or Kubernetes. This project intentionally
stays single-machine: the pipeline processes a few hundred to a couple thousand movies per run,
values in the hundreds-of-megabytes-to-low-gigabytes range that fit comfortably in pandas
DataFrames. The architecture patterns (layered lake, star schema, idempotent upserts,
watermark-based incremental loads, quarantine-based data quality) are the same ones a distributed
version would use — swapping pandas for Spark and a single Postgres instance for
Redshift/Snowflake would be a scaling exercise, not a redesign.
