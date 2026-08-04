# Theoria — Learning Log

A running record of what each task built and the concepts behind it.

---

## Task 1 — Project scaffolding & environment

### What Was Built
The empty skeleton of the whole project: every folder it will eventually need
(`etl/`, `warehouse/`, `data_quality/`, `django_app/`, `tests/`, etc.), a pinned
dependency list, a place to store secrets safely, and one file that loads and
validates all configuration. Nothing does real work yet — this is the foundation
everything else sits on.

### Concepts Used
- **Virtual environment (`venv`)**: an isolated Python install just for this
  project, so its package versions can't clash with other projects on the machine.
- **Pinned dependencies**: writing exact versions (`pandas==2.2.3`) in
  `requirements.txt` so the project installs the *same* way on any machine — the
  difference between "works on my laptop" and reproducible.
- **Secrets management / 12-factor config**: keys and passwords live in a `.env`
  file that is *never* committed. `.env.example` is committed instead — it lists
  the variable *names* with dummy values so a new developer knows what to fill in.
- **Fail-fast / fail-loud configuration**: the program refuses to start if a
  required setting is missing, instead of crashing later with a confusing error
  deep in the code.
- **Single source of truth**: all environment values are read in exactly one
  module (`config.py`); nothing else touches `os.environ`. Change a setting once,
  not in ten places.
- **`.gitignore`**: tells git which files to never track (secrets, the venv,
  logs, compiled `.pyc` files).

### Key Code
`config.py` — `_require()` and the fail-loud block:
> `_require()` doesn't raise the moment it finds one missing variable. It appends
> the name to a `_missing` list and keeps going, so after all variables are
> checked we can raise *one* error listing every missing name at once. That means
> you fix all of them in a single pass instead of re-running, hitting the next
> missing var, re-running again. This "collect errors, then report together"
> pattern is common and underused.

`config.py` — `PROJECT_ROOT = Path(__file__).resolve().parent`:
> Paths are derived from where the file actually lives, not hardcoded like
> `/home/vntrxz/Projects/theoria`. The project still works if someone clones it
> into a different folder — a small habit that prevents a whole class of
> "works only on my machine" bugs.

`load_dotenv(PROJECT_ROOT / ".env")`:
> Reads the `.env` file into environment variables at import time. Real OS
> environment variables (e.g. those set in CI) take precedence, which is exactly
> what you want in production where there is no `.env` file at all.

### What to Study Next
Read about the **12-factor app** methodology, specifically factor III, "Config"
(https://12factor.net/config) — it explains *why* config belongs in the
environment and not in code. Then ask: how would this `config.py` behave on a
server that has no `.env` file but sets real environment variables instead?

---

## Task 2 — TMDB API client wrapper

### What Was Built
One reusable object, `TMDBClient`, that every future ingestion script will use to
talk to the TMDB movie database. Instead of each script writing its own HTTP code
(and its own bugs), they all go through this single door. It knows the base URL
and API key, automatically retries when the API is briefly unavailable, and raises
a clear, custom error when something is genuinely wrong — it never hides a failure.

### Concepts Used
- **API client wrapper**: a thin layer that hides the messy details of HTTP calls
  behind simple methods like `get_genres()`. Callers think in terms of *movies and
  genres*, not URLs and status codes.
- **Session reuse / connection pooling**: one `requests.Session` is reused across
  all calls. TCP/TLS handshakes are expensive; reusing the connection makes many
  calls (we'll make thousands) noticeably faster.
- **Retry with exponential backoff**: when the API returns a *transient* error
  (429 = rate-limited, 5xx = server hiccup), we wait and try again — 0.5s, then 1s,
  then 2s. Backing off gives an overloaded server room to recover instead of
  hammering it. For 429 we also honour the server's own `Retry-After` header.
- **Retryable vs. non-retryable errors**: a 401 (bad key) will *never* fix itself,
  so we fail immediately. Only transient codes are retried. Retrying everything
  would just delay an inevitable failure.
- **Custom exceptions (`TMDBAPIError`)**: a dedicated error type so callers can
  `except TMDBAPIError` specifically, and so failures carry the endpoint + status
  code in the message. Errors are re-raised, never swallowed.
- **Mocking in tests**: the unit tests fake the HTTP responses, so they test our
  retry *logic* without ever hitting the network — fast, deterministic, offline.

### Key Code
`etl/tmdb_client.py` — `get()`:
> The heart of the client. It loops `max_retries + 1` times. On a 200 it returns
> the JSON. On a *retryable* status it sleeps (backoff) and loops again. On a
> non-retryable status, or once retries run out, it raises `TMDBAPIError` with the
> endpoint and status. The loop structure is *why* this is robust: success, retry,
> and permanent-failure are three clearly separated paths, not tangled `if`s.

`etl/tmdb_client.py` — `_sleep_before_retry()`:
> Decides *how long* to wait before the next attempt. If the server sent a
> `Retry-After` header (common with 429s), it obeys that exact number. Otherwise it
> falls back to exponential backoff `backoff_factor * 2**attempt`. Setting
> `backoff_factor=0` in tests makes retries instant — that's why the test suite runs
> in 0.2s instead of seconds.

`etl/tmdb_client.py` — `request_params = {"api_key": self.api_key, **(params or {})}`:
> v3 TMDB auth puts the key in the query string. This line injects it into *every*
> request in one place, so no individual call ever has to remember to add it.

### What to Study Next
Look at the **`urllib3.util.retry.Retry` adapter** that `requests` can mount on a
Session — it implements backoff at the transport layer. Question to explore: what
are the trade-offs between hand-rolling retry logic (like we did, full control,
easy to unit-test) versus delegating it to the library adapter? When would you
prefer each?

---

## Task 3 — S3 writer utility (shared)

### What Was Built
One small module, `etl/s3_utils.py`, that every future ingestion and transform
script will call when it needs to put a file in S3. Instead of each script
knowing how to talk to AWS, how to turn data into JSON or Parquet bytes, and
where in the bucket a file should live, all three of those concerns now live in
exactly one place. Nothing reads from S3 yet — this is the shared "write" side
that Bronze (raw JSON) and Silver/Gold (Parquet) both depend on.

### Concepts Used
- **DRY / single source of truth**: the S3 path layout is defined once in
  `build_path()`. No script ever hand-assembles a key, so the convention can't
  drift between modules.
- **Lazy initialisation**: the boto3 client is created on first use, not at
  import time. Importing the module stays cheap and free of side effects (no
  network/credential work just because something did `import s3_utils`).
- **In-memory serialisation**: Parquet is written to a `BytesIO` buffer and
  uploaded in a single `PutObject`, so we never create temp files on disk.
- **Fail loud, never swallow**: writes raise on error so the *caller* can log
  exactly which object failed (important once we're looping over thousands of
  movie files).
- **Data lake layering**: the `<layer>/<entity>/ingestion_date=...` shape is how
  a data lake partitions data by stage (bronze/silver/gold) and by load date.

### Key Code
`etl/s3_utils.py` — `build_path()`:
> Returns `<layer>/<entity>/ingestion_date=YYYY-MM-DD/<filename>`. It accepts
> either a `date` object or a string so callers can pass `date.today()` without
> formatting it themselves. This one function is *why* the path convention can
> live in a single place — change the layout here and every script follows.

`etl/s3_utils.py` — `get_s3_client()`:
> Builds the boto3 client once and caches it in a module-level global, reusing it
> on every later call. Credentials and region come from `config.py`, never from
> `os.environ` directly — that's the project rule that keeps secrets in one place.

`etl/s3_utils.py` — `write_parquet()`:
> Serialises a DataFrame straight into a bytes buffer with pyarrow (`index=False`
> so the pandas index never leaks into the file) and uploads it. Writing to
> memory instead of a temp file means no cleanup and no disk dependency.

### What to Study Next
Read about **Hive-style partitioning** (the `key=value` directory naming we use
in `ingestion_date=2026-06-21`). Question to explore: when a query engine like
Athena or Spark reads this lake later, how does that `ingestion_date=...` folder
name let it skip files it doesn't need (partition pruning) instead of scanning
everything?

---

## Task 4 — Bronze ingestion: Genres

### What Was Built
The first real ingestion script: `etl/bronze/ingest_genres.py`. It calls the
TMDB API to fetch the official list of movie genres (Action, Comedy, Drama, etc.)
and writes the raw API response — untouched — as a JSON file in the Bronze layer
of the S3 data lake. This is the first step in the pipeline where data actually
lands in the lake.

### Concepts Used
- **Bronze layer**: the "raw" zone of a data lake. Data arrives exactly as the
  source sent it — no cleaning, no type-casting, no filtering. The point is that
  you always have the original to reprocess if your downstream transforms have a
  bug.
- **Idempotent ingestion**: running the script twice on the same day produces the
  same file with the same content. It doesn't accumulate duplicate records or
  crash on the second run. "Idempotent" means: same input → same output, no
  matter how many times you run it.
- **Separation of concerns**: the business logic (`ingest_genres()`) is a plain
  function that accepts a client and a date as arguments. The `__main__` block only
  handles CLI argument parsing and logging setup, then calls that function. This
  makes the logic testable without invoking a subprocess.
- **Dependency injection (light)**: `ingest_genres()` accepts an optional `client`
  argument. In production it builds one from `config.py`; in tests you pass in a
  mock. You never have to patch module-level globals to test the logic.
- **Monotonic timer for duration logging**: `time.monotonic()` is used instead of
  `time.time()` to measure elapsed time. Monotonic clocks only go forward — unlike
  wall-clock time, they can't jump backwards if the system clock is adjusted mid-run.
- **Run summary log**: the final log line records genre count, destination URI,
  and elapsed time. "Done" alone tells you nothing; a run summary tells you whether
  the run was correct and fast.

### Key Code
`etl/bronze/ingest_genres.py` — `ingest_genres()`:
> The function is the module's public API. It takes `ingestion_date` and `client`
> as parameters (defaulting to today and a real `TMDBClient`), so tests can
> inject fakes without patching. It fetches the genre payload, builds the S3 key
> via `s3_utils.build_path()`, and delegates the write to `s3_utils.write_json()`.
> The function owns only the *orchestration* — it never knows how HTTP or S3 work.

`etl/bronze/ingest_genres.py` — `if __name__ == "__main__"`:
> The entry point contains *no* business logic — it only sets up logging, parses
> `--date` from the command line, and calls `ingest_genres()`. This is the
> "one module, one responsibility" rule applied: the `__main__` block is an
> I/O adapter, not a logic layer. Because of this, the whole function is testable
> without spawning a subprocess.

`tests/test_etl.py` — `test_ingest_genres_writes_to_correct_s3_path()`:
> Passes a pre-built mock `TMDBClient` and patches `get_s3_client` so no network
> or AWS calls happen. Asserts the exact S3 URI returned — which encodes the date
> partition, entity name, and filename all at once. If someone changes how
> `build_path()` works, this test breaks immediately.

### What to Study Next
Study the concept of **pipeline idempotency** more broadly. Ask: what if TMDB
returns a *different* genre list tomorrow (e.g. they add a new genre)? Should
the Bronze layer keep the old file too, or replace it? Then read how systems like
Apache Airflow handle **backfills** — re-running a past date's pipeline with the
intent of refreshing the data.

---

## Task 5 — Bronze ingestion: Movies (paginated)

### What Was Built
`etl/bronze/ingest_movies.py` — an ingestion script that walks through the TMDB
"popular movies" list page by page and writes each page as its own JSON file in
the Bronze S3 layer. It returns the list of every `movie_id` it found, which the
next two tasks (movie details and credits) will use as their input. The number of
pages to fetch is controlled by `MAX_PAGES` in `.env` so you can fetch 5 pages in
development and 500 in production without changing any code.

### Concepts Used
- **Pagination**: APIs rarely return all records at once. TMDB's popular-movies
  endpoint returns 20 movies per page; to get a useful catalogue you request
  page 1, page 2, ... up to some limit. The script uses a simple `for page in
  range(1, max_pages + 1)` loop.
- **Write-on-success / partial failure tolerance**: each page is written to S3
  the moment it arrives, before the next page is fetched. If page 7 fails (network
  blip, rate limit), pages 1–6 are already safely stored. You don't lose everything
  because one page errored.
- **Configurable limits via `config.py`**: `MAX_PAGES` comes from the environment,
  not from a hardcoded number in the script. This is the "all config from one place"
  rule applied to ingestion tuning.
- **Collecting IDs across pages**: the function accumulates `movie_id`s from every
  successful page into a list and returns it. The caller (or the next step in the
  pipeline) uses that list to know which movies to fetch details for.
- **Zero-padded filenames**: pages are named `page_0001.json`, `page_0002.json`,
  etc. (`f"page_{page:04d}.json"`). Zero-padding keeps files in correct lexicographic
  order when listed — `page_0010` comes after `page_0009`, not after `page_00100`.

### Key Code
`etl/bronze/ingest_movies.py` — the `for page in range(...)` loop:
> Each iteration: fetch one page, write it to S3 immediately, extend `movie_ids`,
> increment `pages_written`. The `try/except` around this block catches any error
> on a single page, logs it with the page number, increments `pages_failed`, and
> continues to the next page. The already-written S3 objects are untouched —
> there is no transaction to roll back.

`etl/bronze/ingest_movies.py` — `f"page_{page:04d}.json"`:
> The `:04d` format spec zero-pads the page number to four digits. This matters
> because S3 and most filesystems list keys lexicographically: without padding,
> `page_10.json` would sort before `page_2.json`.

`tests/test_etl.py` — `test_ingest_movies_partial_failure_does_not_lose_written_pages()`:
> The mock client raises `RuntimeError` on page 2 mid-run. The test then asserts
> that `put_object` was called exactly twice (pages 1 and 3) and that the returned
> IDs still include those from both successful pages. This is the most important
> test: it proves the failure-isolation guarantee, not just the happy path.

### What to Study Next
Look up the **"at-least-once" vs "exactly-once" delivery** distinction in data
engineering. Our current approach is at-least-once: if the script is killed
*after* `put_object` succeeds but *before* `movie_ids.extend()` runs, the file
is in S3 but the ID is missing from the returned list. Ask: is that a problem for
our pipeline? How would you detect and fix it?

---

## Task 6 — Bronze ingestion: Movie details

### What Was Built
A script that takes a list of movie IDs (collected by Task 5) and fetches the
full detail record for each one from TMDB, writing a separate JSON file per
movie into the Bronze S3 layer. If one movie fails, the error is logged with
the specific ID and the script moves on — completed movies are never lost.

### Concepts Used
- **Per-entity Bronze files**: storing one raw JSON per source record (instead
  of one big blob) makes partial re-ingestion and downstream reads much simpler.
- **Fail-and-continue with identity logging**: catching exceptions per item,
  recording the failed ID, and keeping the success list separate. This pattern
  lets you retry only the failed subset rather than the full catalogue.
- **Return value as contract**: returning `(succeeded_ids, failed_ids)` instead
  of just logging means callers (e.g. a pipeline orchestrator) can act on
  failures programmatically without parsing log strings.
- **Idempotency at the file level**: same `movie_id` + same `ingestion_date`
  → same S3 key → safe to re-run without duplicating data.

### Key Code
`etl/bronze/ingest_movie_details.py` — `ingest_movie_details()`:
> The `for movie_id in movie_ids` loop writes each file *before* moving to the
> next ID. The `try/except` catches any failure, appends the ID to `failed`,
> logs `"movie_id=%d failed: %s"`, and continues. This is deliberate: we log
> the ID (not just "ingestion failed") so the pipeline knows exactly which
> records need a retry run — essential when you have thousands of movies.

`tests/test_etl.py` — `test_ingest_movie_details_logs_failed_movie_id_and_continues`:
> Injects a `RuntimeError` for movie_id 200 while 100 and 300 succeed. Asserts
> that `succeeded == [100, 300]`, `failed == [200]`, and only 2 S3 writes
> happened. This test proves the contract: one bad record does not abort the run
> and does not produce a partial file in S3.

### What to Study Next
Look up **partial retry patterns** in pipeline design: once you have a
`failed_ids` list, how do you persist it so a separate retry job can pick it up?
Common approaches include writing the failed IDs to a small JSON file in S3
(e.g. `bronze/movie_details/_failed/2026-06-22.json`) or storing them in a
simple database table. Think about which fits our single-machine setup better.

---

## Task 7 — Bronze ingestion: Credits (cast & crew)

### What Was Built
A script that fetches cast and crew credits for each movie from TMDB and writes
one raw JSON file per movie into the Bronze S3 layer. It follows the exact same
fail-and-continue pattern as Task 6: one failure does not abort the run, and
the failed IDs are returned so callers can retry them.

### Concepts Used
- **Separation of concerns at the entity level**: credits are a different entity
  from movie details, so they get their own S3 prefix (`bronze/credits/`) and
  their own ingestion module. This makes it easy to re-ingest credits without
  touching movie details, and vice versa.
- **Consistent interface design**: `ingest_credits()` has the same signature and
  return type as `ingest_movie_details()` — both accept `(movie_ids, ingestion_date, client)`
  and return `(succeeded_ids, failed_ids)`. A consistent interface means a
  future orchestrator can call both the same way without special-casing either.
- **TMDB credits structure**: the credits endpoint returns a dict with two keys —
  `cast` (ordered list of actors with `order`, `character`, etc.) and `crew`
  (list of crew members with `job`, `department`, etc.). Both are preserved as-is
  in Bronze; splitting and cleaning happens in Silver.

### Key Code
`etl/bronze/ingest_credits.py` — `ingest_credits()`:
> Calls `client.get_movie_credits(movie_id)` (which hits `movie/{id}/credits`)
> and writes the payload to `bronze/credits/ingestion_date=YYYY-MM-DD/<movie_id>.json`.
> The structure is identical to `ingest_movie_details` by design — same loop,
> same error handling, same return contract. Reusing the same pattern means less
> cognitive overhead and fewer bugs when reading the pipeline top-to-bottom.

### What to Study Next
Look at the **TMDB credits response** in detail: `cast[].order` is the billing
order (0 = top-billed), and `crew[].job` can be "Director", "Producer",
"Screenplay", etc. In Silver (Task 10), you'll need to filter crew to extract
only directors. Think now about what `crew[].department` values exist and how
you'd filter them — the TMDB docs list all departments.

---

## Task 8 — Ingestion logging & run summary

### What Was Built
A shared logging setup module (`etl/logging_config.py`) that all Bronze
ingestion scripts now use. Calling `setup_logging("ingest_genres")` from
a script's `__main__` block replaces the old one-liner `basicConfig` call and
gives every script two log destinations: the console (INFO+) and a rotating
file in `logs/` (DEBUG+).

### Concepts Used
- **Centralized logging configuration**: instead of each script calling
  `logging.basicConfig(...)` with its own format string, one function owns the
  setup. Change the format once → it applies everywhere.
- **Multiple handlers on the root logger**: Python's logging system lets you
  attach many handlers to one logger. Console shows only INFO+ (readable at a
  glance); the file captures DEBUG+ so detailed per-item writes are there for
  debugging without flooding the terminal.
- **Rotating file handler (`RotatingFileHandler`)**: limits each log file to
  5 MB, keeping 3 backups (`ingest_genres.log`, `.log.1`, `.log.2`). Without
  rotation, a long-running pipeline would fill the disk.
- **`mkdir(parents=True, exist_ok=True)`**: creates the `logs/` directory if
  it doesn't exist yet, without raising an error if it already does. The
  `parents=True` flag creates any missing intermediate directories too.

### Key Code
`etl/logging_config.py` — `setup_logging(script_name)`:
> Builds two `logging.Handler` objects, both using the same `Formatter`
> (timestamp + padded level + logger name + message). Attaches them to
> `logging.getLogger()` — the *root* logger — so every `logger = logging.getLogger(__name__)`
> in any ETL module automatically inherits both handlers without any per-module
> setup.

`etl/bronze/ingest_genres.py` (and the other three) — `__main__` block:
> The `from etl.logging_config import setup_logging` import is inside
> `if __name__ == "__main__":` on purpose. Importing it at module level would
> run setup code on every `import ingest_genres` (e.g. in tests), which would
> add handlers and create files even during test runs. Keeping it inside
> `__main__` means it only runs when the script is executed directly.

### What to Study Next
Read about Python's **logger hierarchy**: `logging.getLogger("etl.bronze.ingest_genres")`
is a child of `logging.getLogger("etl.bronze")`, which is a child of
`logging.getLogger("etl")`, which is a child of the root logger. Messages
propagate up by default. This is why attaching handlers to the root logger is
enough — you never need to touch child loggers. Try: what happens if you set
`propagate = False` on a child logger?

---

## Task 9 — Silver transform: Movies

### What Was Built
A transform script that reads every raw Bronze JSON file for a given date,
cleans and reshapes it into a flat table, and writes a single Parquet file
to the Silver layer. This is the first step from raw data to structured data
— Bronze is what the API returned; Silver is what the rest of the pipeline
can actually use.

### Concepts Used
- **Silver layer**: the "cleaned" zone of the data lake. Raw JSON is messy —
  nested objects, inconsistent types, empty strings where NULLs belong. Silver
  fixes all of that and stores one clean, typed row per business entity.
- **Flattening**: extracting a nested structure (e.g., `genres: [{id: 28, name: "Action"}]`)
  into a flat column (`genre_ids: [28]`). Every downstream query works on flat
  tables, not nested JSON.
- **Type casting with coercion**: `pd.to_numeric(series, errors="coerce")` turns
  bad values into `NaN` instead of crashing. `Int64` (capital I) is pandas'
  nullable integer type — it holds integers *and* `NaN`, unlike plain `int64`.
- **Deduplication**: `df.drop_duplicates(subset=["movie_id"], keep="last")` —
  if the same `movie_id` appears in two Bronze files (e.g., a retry wrote it
  twice), we keep exactly one row. The Silver layer must have one row per key.
- **Idempotency**: same date → same output S3 key → same content. Running the
  transform twice is safe because the second run overwrites the same Parquet
  file with the same data. No manual cleanup needed.
- **S3 list + paginator**: `client.get_paginator("list_objects_v2")` lets you
  iterate through all objects under a prefix even if there are thousands of
  them. Without pagination you'd only see the first 1,000 results.

### Key Code
`etl/silver/transform_movies.py` — `_flatten_movie(raw)`:
> Extracts exactly the columns the rest of the pipeline needs from the raw
> TMDB dict and renames `id` → `movie_id`. Everything else in the TMDB
> response is silently discarded here — you choose your schema at this point,
> not downstream.

`etl/silver/transform_movies.py` — `_cast_types(df)`:
> All type coercions are in one function, separated from the IO logic. This
> keeps `transform_movies()` readable and makes the type rules easy to test
> in isolation — just pass in a DataFrame, no S3 mock needed.

`etl/silver/transform_movies.py` — `transform_movies()`:
> Orchestrates the full pipeline: list → read → flatten → cast → deduplicate
> → write. Raises `FileNotFoundError` if there is nothing to process so the
> caller knows immediately rather than producing an empty Parquet file silently.

### What to Study Next
Parquet schema evolution: what happens when you add a new column to
`_flatten_movie` — does the downstream reader break? Read about PyArrow's
`schema` parameter in `to_parquet` and how `read_parquet` handles
missing columns.

---

## Task 10 — Silver transform: People (actors & directors)

### What Was Built
A Silver transform that reads all Bronze credits JSON files for a given
date, splits each payload into two entity types — actors (from the `cast`
array) and directors (from the `crew` array filtered to `job == "Director"`),
deduplicates each group on `person_id` (the same actor appears in many
movies' credits), and writes two separate Parquet files:
`silver/actors/…/actors.parquet` and `silver/directors/…/directors.parquet`.

### Concepts Used
- **Entity splitting**: one Bronze file contains two conceptually different
  entities (cast members and crew members). The Silver transform is responsible
  for separating them into the right tables rather than dumping everything
  into one place.
- **Cross-file deduplication**: because `person_id` 10 (e.g. "Alice") can
  appear in the credits of hundreds of movies, all those Bronze files each
  contain a row for her. Collecting all rows first and then calling
  `drop_duplicates(subset=["person_id"])` collapses them to one canonical row
  per person. This is different from Task 9 where duplicates were only possible
  within a single date's files.
- **Defensive empty-DataFrame handling**: if no movie in the batch had a
  Director in the crew, `director_rows` would be an empty list. Building a
  `pd.DataFrame([])` and then calling `_cast_people_types` on it works fine
  because the cast-type logic operates column-by-column and gracefully handles
  zero rows. The alternative — skipping the write — would leave downstream
  code wondering whether the Silver file is missing or just empty.
- **Idempotency**: same date → same S3 keys, same content. Safe to re-run.

### Key Code
`etl/silver/transform_people.py` — `_extract_directors()`:
> Iterates the `crew` list and keeps only entries where `job == "Director"`.
> This filter lives in its own function (not inline in the main transform)
> because it represents a business rule — "a director is a crew member with
> job=Director" — that may need to expand later (e.g. "Co-Director"). Keeping
> it isolated makes it easy to test and change independently.

`etl/silver/transform_people.py` — `transform_people()`:
> Collects all cast and crew rows into two plain Python lists before building
> DataFrames. This pattern (accumulate → DataFrame → transform) is preferred
> over building the DataFrame incrementally inside the loop because appending
> rows one-by-one to a DataFrame is slow (O(n²) copies). One `pd.DataFrame(rows)`
> call at the end is O(n).

### What to Study Next
TMDB's `gender` field uses an integer code (0 = unset, 1 = female, 2 = male,
3 = non-binary). In the warehouse `dim_actor` we might want a human-readable
string instead. Look at pandas `.map()` for applying a lookup dict to a column
(`df["gender"].map({0: "unset", 1: "female", 2: "male", 3: "non-binary"})`),
and think about whether that conversion belongs in Silver or in the warehouse
loader.

---

## Task 11 — Silver transform: Genres

### What Was Built
A Silver transform that reads the Bronze genre list JSON (one file per date) and writes a clean `genres.parquet` to the Silver layer. Each row is a `(genre_id, genre_name)` pair — exactly what the `dim_genre` warehouse dimension needs.

### Concepts Used
- **Silver layer**: The "cleaned and typed" zone of the data lake. Bronze is raw and immutable; Silver is where we fix types, drop nulls, and deduplicate so downstream code can trust the data.
- **Single-file source vs multi-file source**: Movie details and credits are one file *per movie ID*, so the transform lists S3 objects with a paginator. Genres are one file *per ingestion date* — a known, fixed key — so we just call `get_object` directly on the exact key.
- **Explicit `FileNotFoundError`**: If the Bronze file doesn't exist, we raise with a clear message rather than letting boto3 throw a cryptic `NoSuchKey` exception. This makes pipeline failures easier to diagnose.
- **`pd.StringDtype` (`"string"`)**: pandas nullable string type. Unlike plain `object`, it distinguishes `None`/`pd.NA` from the string `"None"`, which matters when writing to Parquet or a database column.

### Key Code
`etl/silver/transform_genres.py` — `_read_bronze_genres()`:
> Builds the exact S3 key using `s3_utils.build_path()` (the single source of truth for key conventions) and calls `get_object` directly. If the key doesn't exist, it catches the boto3 `NoSuchKey` exception and re-raises a Python-native `FileNotFoundError` — so callers don't need to know boto3 exception types.

`etl/silver/transform_genres.py` — `_cast_genre_types()`:
> Casts `genre_id` to `Int64` (nullable integer — handles `None` without converting to `float`) and `genre_name` to pandas `"string"` (nullable string). This is the same coerce-don't-crash pattern used throughout the Silver layer: `errors="coerce"` turns unparseable values into `pd.NA` rather than raising.

### What to Study Next
Look at how Parquet handles pandas nullable types (`Int64`, `string`) versus plain Python types when the file is read back. Run `pd.read_parquet` on a file written with `Int64` columns and inspect `df.dtypes` — does it round-trip perfectly, or does pandas infer a different type on read? Understanding this avoids surprises in the warehouse loader.

---

## Task 12 — Silver transform: Credits bridge

### What Was Built
A Silver-layer transform that reads every Bronze credits JSON file for a given date and produces a single "bridge" Parquet table linking movies to people. Each row records one credit: who appeared in which movie, whether they were cast or crew, what their role was, and in what order they appear (for cast). The output is `silver/credits_bridge/ingestion_date=YYYY-MM-DD/credits_bridge.parquet`.

### Concepts Used
- **Bridge (associative) table**: A table whose job is to hold the many-to-many relationship between two entities — here, movies and people. Neither `dim_movie` nor `dim_actor`/`dim_director` can store this; the bridge holds the link.
- **Composite deduplication key**: Rows are deduplicated on `(movie_id, person_id, credit_type)` rather than a single column, because the same person can legitimately appear as both an actor and a crew member in the same movie — those are two distinct credits, not duplicates.
- **Referential integrity checking**: Before writing, the transform optionally checks whether every `movie_id` and `person_id` in the bridge actually exists in the upstream Silver tables. Rows that reference unknown IDs are called "orphans". The rule here is flag-don't-crash: log a warning so the issue is visible, but don't drop the row or abort the job — the warehouse loader can enforce the constraint more strictly later.
- **Soft vs. hard failures**: Null IDs are always dropped (a row with no movie_id is meaningless). Unknown-but-valid IDs are only flagged. This distinction — hard failure on nulls, soft warning on referential issues — is a common data engineering pattern.

### Key Code
`etl/silver/transform_credits_bridge.py` — `_extract_bridge_rows(payload)`:
> Takes one TMDB credits JSON payload and returns one dict per cast/crew member. The key insight is that `payload["id"]` is the `movie_id` — it lives at the root of the payload, not inside each member. Without this, all bridge rows would have `movie_id=None`.

`etl/silver/transform_credits_bridge.py` — `_check_referential_integrity(df, known_movie_ids, known_person_ids)`:
> Accepts optional sets of valid IDs. If provided, it finds rows whose ID values are not in those sets and logs them as warnings. The parameters are optional (`None` by default) so callers who don't have the Silver people/movies data handy can skip the check — the function degrades gracefully rather than failing.

`tests/test_etl.py` — `test_transform_credits_bridge_flags_orphan_movie_ids`:
> Uses pytest's `caplog` fixture to assert that a warning log message was emitted when `known_movie_ids={999}` but the data contains movie 550. This tests behaviour (a log warning fires) not just output (the Parquet file) — an important pattern for testing observability code.

### What to Study Next
Look up what a **surrogate key** is and how it differs from a natural key.

---

## Task 13 — Silver data quality checks

### What Was Built
A standalone data quality module (`data_quality/silver_checks.py`) that reads all five Silver Parquet tables for a given date and validates them. For each table it runs four check types: schema (are the expected columns there?), nulls (do required columns have any missing values?), duplicates (is the primary key truly unique?), and ranges (do numeric values fall within sensible bounds?). Rows that fail are tagged with a `rejection_reason` column and written to local Parquet files in `data_quality/rejected/` for later investigation — they are quarantined, never silently dropped.

### Concepts Used
- **Data Quality checks as first-class code**: Rather than assuming clean data, we validate explicitly. This is how production pipelines catch upstream API changes, ETL bugs, or corrupt files before they silently pollute the warehouse.
- **Boolean masks**: Each check function (e.g., `_null_mask`, `_range_mask`) returns a pandas `pd.Series` of `True/False` values — one per row — that marks which rows are bad. Masks are cheap to create and combine with `|` (bitwise OR) to union multiple failure types.
- **Quarantine pattern**: Bad rows are never deleted or silently skipped. They go to `data_quality/rejected/` with a `rejection_reason` label. This preserves evidence — you can look at exactly which rows failed and why, and replay them after fixing the issue.
- **Dataclass as a result type**: `CheckResult` is a Python `@dataclass` — a lightweight class that holds data (`entity`, `check`, `passed`, `bad_count`, `message`) with no boilerplate. Using a dataclass instead of a plain dict makes the return type self-documenting.
- **Graceful degradation**: If one Silver file can't be read (e.g., the transform failed and the file doesn't exist yet), the check records a `load` failure and moves on to the next entity. The whole run doesn't abort.

### Key Code
`data_quality/silver_checks.py` — `_range_mask(df, ranges)`:
> Takes a dict of `{column: (min, max)}` and returns a boolean mask of rows where any column is out of bounds. Crucially, it converts with `pd.to_numeric(errors="coerce")` before comparing — so text noise produces `NaN`, not a crash — and only checks `not_null` rows for the bounds, so a null value is not reported as an out-of-range failure (that's the null check's job).

`data_quality/silver_checks.py` — `_run_entity_checks(df, entity, cfg, ...)`:
> Runs all four check types for one entity, collects bad-row DataFrames with their `rejection_reason` label, then calls `_write_rejects` once at the end. This means one reject file per entity (not one per check), and a row that fails multiple checks appears once — not four times.

### What to Study Next
Look up the difference between **data validation at ingestion time** vs **data quality checks after the fact**. The pattern here is post-hoc: we write Bronze first, transform to Silver, then check Silver. An alternative is to validate on read inside the transform and reject before writing. Think about which approach is better for a streaming pipeline vs a batch pipeline, and why the quarantine-not-delete rule matters in both cases. The `movie_id` and `person_id` in this bridge are natural keys (they come from TMDB). In the warehouse, the dimension tables may use their own surrogate keys (auto-increment integers). The warehouse loader (Task 19) will need to join bridge rows against dimensions to swap natural keys for surrogate keys before inserting into `fact_casting` — understanding why this matters is the core of Task 18–19.


---

## Task 14 — Gold layer: aggregated datasets

### What Was Built
A Gold-layer transform script (`etl/gold/build_gold_datasets.py`) that reads all five Silver Parquet files for a given date and computes four pre-aggregated analytical datasets, writing each as a Parquet file to the Gold layer in S3:

1. **genre_metrics** — for each genre: how many films belong to it, the average rating of those films, and their combined revenue.
2. **decade_stats** — for each release decade (1990s, 2000s, etc.): how many films, average rating, and total revenue.
3. **actor_filmography** — for each actor: how many films they appeared in and their average rating across those films.
4. **director_ratings** — for each director: how many films they directed, their average rating, and total revenue of their films.

These datasets live between Silver and the warehouse. They answer common analytical questions in a single table scan rather than requiring the Django app to do expensive joins at query time.

### Concepts Used
- **Gold layer purpose**: The Gold layer is the "answer-ready" layer — it stores pre-computed aggregations that are directly useful for dashboards or analytics queries. Silver is clean and normalised; Gold trades storage for query speed.
- **Explode**: `df.explode("genre_ids")` turns one row with `genre_ids=[28, 12]` into two rows — one with `genre_id=28` and one with `genre_id=12`. This is how you handle list-valued columns in a flat relational model. Without explode, you cannot group by genre.
- **GroupBy + named aggregations**: `df.groupby("genre_id").agg(movie_count=("movie_id", "count"), avg_rating=("vote_average", "mean"))` produces a summary table in one step. Named aggregations (the `result_col=(source_col, func)` syntax) make the output columns self-explanatory.
- **Join pattern (bridge table)**: To get a director's films, you can't join movies directly to directors — there's no direct FK. You must go through the bridge table: `bridge → movies` (to get ratings/revenue) and `bridge → directors` (to get names). This is the standard many-to-many join pattern.
- **Separation of concerns**: Each aggregation is its own function (`_build_genre_metrics`, `_build_decade_stats`, etc.). The public entry point `build_gold_datasets()` orchestrates them. This makes each aggregation easy to test and change independently.
- **Idempotency**: Running the script twice for the same date overwrites the same S3 keys with the same content — no duplicate rows accumulate.

### Key Code
`etl/gold/build_gold_datasets.py` — `_build_genre_metrics(movies, genres)`:
> Explodes the `genre_ids` list column so each movie appears once per genre, then merges with the genres table to get names, then groups by `(genre_id, genre_name)` to compute count, avg rating, and total revenue. The explode step is the key insight — without it you'd be grouping on a list, which pandas cannot do.

`etl/gold/build_gold_datasets.py` — `_build_decade_stats(movies)`:
> Extracts the year from `release_date`, computes `decade = year // 10 * 10` (integer floor division drops the units digit), then groups by decade. Integer floor division is a clean way to bin continuous values into fixed-width buckets without any if/else logic.

`etl/gold/build_gold_datasets.py` — `build_gold_datasets()`:
> Reads all five Silver files, calls the four aggregation functions, then writes each result with `s3_utils.write_parquet()`. Returns a dict of `{dataset_name: s3_uri}` so the caller knows exactly where each file landed. Raises `FileNotFoundError` immediately if any Silver input is missing — fail loud, not silently.

### What to Study Next
The Gold layer here is built on top of Parquet files in S3. In production pipelines, this same aggregation would often be done as a SQL `CREATE TABLE AS SELECT … GROUP BY …` inside a data warehouse. Study what **materialised views** are in PostgreSQL (Task 22 will use SQL aggregations directly). Ask: when is it better to pre-aggregate into Gold vs compute on the fly in SQL? The answer involves data volume, query frequency, and how often the source data changes.

## Task 15 — PostgreSQL Setup & Connection Layer

### What Was Built
A single Python module (`warehouse/db.py`) that manages the connection between the Python application and the PostgreSQL database. It gives every other module a clean, safe way to talk to the database without each one having to manage its own connection details.

### Concepts Used
- **Connection pool**: Instead of opening a new database connection for every query (slow), SQLAlchemy keeps a pool of reusable connections. `pool_size=5` means up to 5 open at once; `max_overflow=2` allows 2 extra under load.
- **Singleton pattern**: `get_engine()` creates the engine once and returns the same object every time. Avoids creating multiple pools that waste memory and connections.
- **Context manager (with statement)**: `get_session()` is decorated with `@contextmanager`. This guarantees the session is always committed or rolled back and always closed — even if code inside crashes.
- **`pool_pre_ping=True`**: Before handing a connection from the pool to your code, SQLAlchemy sends a cheap "SELECT 1" to check it's still alive. Prevents mysterious errors when the DB drops idle connections.
- **Transaction**: A group of SQL statements that either all succeed (commit) or all fail (rollback). `get_session()` wraps every block of work in one transaction automatically.

### Key Code
`warehouse/db.py` — `get_session()`:
> Uses Python's `contextmanager` to yield a Session to the caller. The `try/except/finally` block is the key: if the code inside `with get_session()` raises any exception, `rollback()` is called (undoing all changes in that transaction) and the exception re-raises to the caller. If no exception, `commit()` saves all changes. `close()` runs in `finally` — no matter what — so the connection is always returned to the pool.

`warehouse/db.py` — `get_engine()`:
> The `global _engine` pattern is the singleton. On the first call `_engine is None`, so it calls `create_engine()` with the full URL from `config.DATABASE_URL`. Every subsequent call just returns the already-created engine. `reset_engine()` sets it back to `None` so tests can start fresh without one test's engine leaking into another.

### What to Study Next
Read the SQLAlchemy 2.0 docs on the difference between **`Session`** and **`Connection`**. `Session` (used here) is the ORM-level object that tracks Python objects and maps them to DB rows. `Connection` is the lower-level object that just runs raw SQL. Task 18 (loading dimensions) will use `Session` with ORM models, but Task 22 (analytics SQL) will likely use `Connection.execute(text(...))` for raw SQL. Understanding when to use which is fundamental.

---

## Task 16 — DDL: Dimension tables

### What Was Built
A single SQL file (`warehouse/ddl/01_dimensions.sql`) that creates all five dimension tables in PostgreSQL. Running this file bootstraps the warehouse schema so the loaders (Tasks 18+) have tables to insert into.

### Concepts Used
- **Star schema — dimension tables**: In a star schema the "dim_" tables hold descriptive attributes about the things you measure (movies, actors, genres, dates). They are small relative to fact tables and change slowly.
- **Surrogate vs. natural key**: `movie_id`, `actor_id`, etc. come directly from TMDB (natural keys). `dim_date.date_id` is an integer surrogate (`YYYYMMDD`) — it's human-readable and sorts correctly without a join.
- **`IF NOT EXISTS`**: Makes the DDL idempotent — re-running it never errors or overwrites existing data. This is the SQL equivalent of the idempotent ingestion rule applied to schema management.
- **Named constraints (`CONSTRAINT pk_…`)**: Naming the PRIMARY KEY makes error messages and pg_catalog queries readable. Anonymous constraints get generated names like `dim_movie_pkey` — still fine, just less explicit.
- **`NUMERIC(10,4)` for popularity**: Floating-point types (`FLOAT`, `DOUBLE`) accumulate rounding errors. `NUMERIC` stores exact decimal values, which matters for ranking and comparison queries.

### Key Code
`warehouse/ddl/01_dimensions.sql` — `dim_date` table:
> Uses an integer `date_id` (YYYYMMDD) as the surrogate key instead of the `DATE` type. This is a standard data-warehouse pattern: integer lookups are faster than date comparisons in large fact tables, and the value is self-documenting when you read query results.

`warehouse/ddl/01_dimensions.sql` — `IF NOT EXISTS` on every `CREATE TABLE`:
> Without this guard, re-running the script on an existing database raises an error and aborts. With it, the script is safe to run as many times as needed — the schema converges to the desired state rather than requiring manual teardown first.

### What to Study Next
Read the PostgreSQL docs on [data types](https://www.postgresql.org/docs/current/datatype.html) — specifically the difference between `NUMERIC`, `FLOAT`, and `REAL`. Then ask: why do most warehouses store monetary values as `BIGINT` cents rather than `NUMERIC` dollars?

---

## Task 17 — DDL: Fact tables

### What Was Built
A SQL file (`warehouse/ddl/02_facts.sql`) that creates the two fact tables in PostgreSQL — `fact_movie_metrics` and `fact_casting` — plus indexes on every foreign key column. These are the central tables of the star schema: every analytical query will join through them.

### Concepts Used
- **Star schema — fact tables**: Fact tables store measurable events or snapshots (a movie's rating on a given date, a casting relationship). They reference dimension tables via foreign keys and are typically much larger than dimensions.
- **Composite primary key**: Neither fact table has a single natural PK column. `fact_movie_metrics` is uniquely identified by `(movie_id, date_id, genre_id)` — one row per movie-date-genre combination. Using all three as the PK enforces uniqueness and acts as a free compound index.
- **Named FOREIGN KEY constraints**: `CONSTRAINT fk_fmm_movie FOREIGN KEY (movie_id) REFERENCES dim_movie(movie_id)` tells PostgreSQL to reject any insert whose `movie_id` doesn't exist in `dim_movie`. Named constraints make error messages actionable ("violates fk_fmm_movie" vs. an anonymous generated name).
- **Indexes on FK columns**: PostgreSQL does *not* automatically index foreign key columns (unlike primary keys). Without these indexes, a join like `fact_movie_metrics JOIN dim_genre USING (genre_id)` requires a full sequential scan of the fact table. `CREATE INDEX IF NOT EXISTS` adds the index idempotently.
- **`IF NOT EXISTS` on indexes**: Same idempotency benefit as on tables — re-running the file never errors on an already-created index.

### Key Code
`warehouse/ddl/02_facts.sql` — composite PK on `fact_movie_metrics`:
> `CONSTRAINT pk_fact_movie_metrics PRIMARY KEY (movie_id, date_id, genre_id)` — three columns together form the key because a single movie appears across multiple dates and multiple genres. A surrogate auto-increment PK would also work, but a natural composite PK doubles as a uniqueness guard and eliminates accidental duplicate loads.

`warehouse/ddl/02_facts.sql` — FK indexes:
> `CREATE INDEX IF NOT EXISTS idx_fmm_genre_id ON fact_movie_metrics (genre_id)` — this is the pattern for every FK column. When the query planner joins `fact_movie_metrics` to `dim_genre`, it uses this index for an index scan instead of scanning millions of fact rows. The rule of thumb: every FK column in a fact table needs an index.

### What to Study Next
Read the PostgreSQL docs on [index types](https://www.postgresql.org/docs/current/indexes-types.html). The indexes created here are the default B-tree, which is correct for equality and range lookups on FK columns. Ask: when would you choose a BRIN index over a B-tree for a fact table? (Hint: think about `date_id` and physical row ordering.)

---

## Task 18 — Loader: Dimensions

### What Was Built
`etl/warehouse_loader/load_dimensions.py`, the first script that writes into PostgreSQL. It reads the Silver Parquet files (movies, actors, directors, genres) for a given date and loads them into the `dim_*` tables created in Tasks 16–17. It also generates and loads `dim_date`, a full calendar table, independently of any TMDB data.

### Concepts Used
- **Upsert (`INSERT ... ON CONFLICT DO UPDATE`)**: Instead of checking "does this row exist? update or insert accordingly" in Python (two round-trips, race conditions), PostgreSQL does it atomically in one statement. If a row with the same primary key exists, its non-key columns are overwritten with `EXCLUDED.<col>` (the value that *would* have been inserted); otherwise a new row is created. This is what makes the loader idempotent — running it twice for the same date produces the same warehouse state, not duplicate rows.
- **Batch execution vs. row-by-row inserts**: `session.execute(text(sql), records)` where `records` is a list of dicts sends one INSERT statement with many parameter sets in a single round trip (`executemany` under the hood), instead of looping and issuing one query per row. This matters once you're loading thousands of movies.
- **Surrogate key generation for a calendar dimension**: `dim_date` isn't derived from source data at all — it's *manufactured*. `_build_calendar()` uses `pd.date_range()` to produce every day in a range and derives `year`/`month`/`day`/`decade` and the `YYYYMMDD` integer key from each date. This is the standard way to build a date dimension in any warehouse: generate it once, upfront, wide enough to cover any date you'll ever join against.
- **Type coercion at the Python/SQL boundary**: pandas' nullable types (`Int64`, `NaT`) aren't the same as Python's `None`, and psycopg2 doesn't know how to bind `pd.NA` or `NaT`. `_records()` converts a DataFrame slice to `object` dtype and replaces anything `pd.notnull()` rejects with `None`, so every value handed to SQLAlchemy is a native Python type.
- **Separating "what to load" from "how to upsert"**: `_upsert()` is a single generic function parameterized by table name, primary-key columns, and column list. Each `load_dim_*()` function just picks the right columns/renames and delegates — the ON CONFLICT SQL is written once, not five times.

### Key Code
`etl/warehouse_loader/load_dimensions.py` — `_upsert()`:
> Builds `INSERT INTO {table} (cols) VALUES (:col1, :col2, ...) ON CONFLICT (pk_cols) DO UPDATE SET col = EXCLUDED.col` from just a table name and column lists, then executes it once against the full record list. Centralizing this in one function means the conflict-handling logic is tested and correct in one place, and every dimension loader is a thin wrapper around it.

`etl/warehouse_loader/load_dimensions.py` — `_build_calendar()`:
> `date_id = full_date.strftime("%Y%m%d")` turns a date into a sortable, human-readable integer surrogate key — matching the `dim_date.date_id` column defined in the Task 16 DDL. Generating the *entire* range up front (not just dates seen in movie data) means future fact rows can always find a matching `dim_date` row without needing to re-run this loader.

`etl/warehouse_loader/load_dimensions.py` — `load_dim_actor()` / `load_dim_director()`:
> Both read from the *same* Silver schema (`person_id`, `name`, `gender`, `popularity` — actors and directors are both just "people" until this point) and only differ in which target table and PK column name they use. Renaming `person_id` → `actor_id`/`director_id` at load time keeps the Silver layer generic while the warehouse schema stays explicit about roles.

### What to Study Next
Read up on **transaction isolation** for concurrent upserts: if two loader runs for overlapping dates executed at the same time, could they deadlock or produce inconsistent results? Look at PostgreSQL's `ON CONFLICT` locking behavior and how `pool_pre_ping`/connection pooling (already in `warehouse/db.py`) interacts with long-running batch transactions.

## Task 19 — Loader: Facts

### What Was Built
`etl/warehouse_loader/load_facts.py` reads the Silver `movies` and `credits_bridge` Parquet files for a given date and loads the two fact tables — `fact_movie_metrics` and `fact_casting` — resolving every natural key against the dimension tables loaded in Task 18, and quarantining any row whose keys don't resolve instead of inserting garbage or crashing.

### Concepts Used
- **Fact table grain**: A fact table's "grain" is what one row *means*. `fact_movie_metrics`'s grain is `(movie_id, date_id, genre_id)` — since a movie can have multiple genres, one Silver movie row explodes into multiple fact rows, one per genre. Getting the grain wrong (e.g. one row per movie) would silently break any query that joins through `dim_genre`.
- **Referential integrity enforcement in the loader, not just the database**: PostgreSQL's `FOREIGN KEY` constraints (Task 17) would reject a bad insert anyway, but that fails the *entire* batch statement, including all the good rows in it. Checking membership against `_existing_ids()` (a set of valid PKs pulled from the DB) in Python first means bad rows are filtered out individually, and the good rows in the same batch still load.
- **Quarantine over silent drop, again**: same pattern as `data_quality/silver_checks.py` — bad rows get a `rejection_reason` column and are written to Parquet under `data_quality/rejected/`, never just discarded. This is a recurring project rule because losing the *reason* a row failed makes debugging a "why is my count low" bug much harder later.
- **Resolving a schema mismatch through a cross join**: `fact_casting`'s PK requires both `actor_id` and `director_id` non-null, but the Silver bridge table has one row per person (an actor row *or* a director row), never both together. The fix — cross-joining every actor with every director credited on the same movie — is a real data-modeling trade-off, not a mechanical translation: it changes what a "row" means (an actor-director pairing, not a single casting credit) to fit the fact table's declared grain. This was a genuine design decision, not something derivable purely from the code, so it was worth explicitly deciding rather than guessing.

### Key Code
`etl/warehouse_loader/load_facts.py` — `_build_movie_metrics_rows()`:
> For each Silver movie row, converts `release_date` into the same `YYYYMMDD` integer used by `dim_date`, then loops over `genre_ids` emitting one fact row per genre that exists in `valid_genre_ids`. Any failure — unknown movie, missing/unmatched date, empty or unknown genre — appends a row to `rejects` with a specific `rejection_reason` instead of raising.

`etl/warehouse_loader/load_facts.py` — `_build_casting_rows()`:
> Splits the bridge DataFrame into `cast_df` (credit_type == "cast") and `director_df` (credit_type == "crew" and role == "Director"), groups cast rows by `movie_id`, and for each movie's actor group pairs every actor with every director found for that same `movie_id`. A movie with credited actors but zero credited directors rejects its actor rows with reason `"no director for movie"` rather than inserting a row with a fabricated director.

`etl/warehouse_loader/load_facts.py` — `_existing_ids()`:
> `SELECT {pk_col} FROM {table}` against the session, returned as a Python `set`. This is what lets the loader check "does this ID exist in the dimension?" as an O(1) membership test in Python instead of relying on the database to reject bad rows one at a time inside the FK constraint.

### What to Study Next
Look at how this loader's row-by-row Python-side FK check would scale: `_existing_ids()` pulls the *entire* PK column into memory. For a small learning project (thousands of movies) this is fine, but think about what breaks at millions of rows, and what the alternative would look like — e.g. doing the FK filter as a SQL anti-join (`LEFT JOIN ... WHERE dim.pk IS NULL`) instead of pulling IDs into pandas/Python sets.

---

## Task 20 — Incremental load logic

### What Was Built
A new `etl/incremental.py` module that lets each warehouse loader remember which Silver `ingestion_date` partition it last finished processing (a "watermark"), and discover which newer partitions in S3 it hasn't loaded yet. `load_dimensions.py` and `load_facts.py` each gained a `*_incremental()` wrapper that uses this to process only new partitions, instead of requiring the caller to pass a specific `--date` every time.

### Concepts Used
- **Watermarking**: instead of re-scanning all historical data on every run, the pipeline records a single "high water mark" value (here, a date) per loader in a small `etl_watermarks` table. Each run only processes data *after* that mark, then advances it. This is the standard pattern behind almost all "incremental" or "delta" data pipelines — the alternative (reprocessing everything every time) doesn't scale once there's a year of daily partitions sitting in S3.
- **Partition discovery via S3 `Delimiter`**: `list_available_partitions()` calls `list_objects_v2` with `Delimiter="/"`, which makes S3 return `CommonPrefixes` — the "folder names" one level below the given prefix — instead of every individual object key. This turns "list all files under `silver/movies/`" into "list all `ingestion_date=...` partitions under `silver/movies/`" without touching a single actual file, which matters once a partition holds many objects.
- **Idempotent upsert vs. duplicate-preventing constraint — these are not the same tool**: the task asked for a `UNIQUE(movie_id, ingestion_date)` constraint on the fact tables. Adding it literally would have broken correct data: `fact_movie_metrics` explodes one movie into several rows (one per genre) at the *same* `ingestion_date`, so a real `UNIQUE(movie_id, ingestion_date)` would reject the second genre row as a duplicate. The tables were already protected against reprocessing the same partition twice by their existing composite primary key plus `ON CONFLICT DO UPDATE` (from Task 18/19) — re-running a partition just re-writes the same rows, it never inserts new ones. So `ingestion_date` was added as a plain (non-unique, indexed) audit column recording provenance, and the literal constraint was deliberately not added. This is a case where following an instruction exactly would have been a bug — worth noticing when a stated implementation detail conflicts with a data model already in place.
- **Partial-progress safety**: `*_incremental()` advances the watermark once *per partition*, immediately after that partition's load succeeds — not once at the end of the whole batch. If partition 3 of 5 throws, partitions 1–2 are already committed and their watermark is saved, so a retry only redoes work from partition 3 onward.

### Key Code
`etl/incremental.py` — `pending_partitions()`:
> Reads the loader's stored watermark, lists every partition actually present in S3, and returns the subset strictly newer than the watermark (or every partition, if there's no watermark yet — i.e. first run). This is the one function both loaders call to decide "what do I still need to do."

`etl/warehouse_loader/load_facts.py` — `load_facts_incremental()`:
> Loops `pending_partitions()` in ascending date order, calls the existing single-date `load_facts()` for each, and only *then* calls `set_watermark()` for that date, in a separate short-lived session. Reusing the already-idempotent `load_facts()` unchanged (rather than rewriting fact-loading logic) means Task 19's row-by-row FK-quarantine behavior is preserved exactly for every partition processed this way.

### What to Study Next
This watermark is coarse — one date per loader, no concept of "partially loaded partition." Think about what would need to change to make a single partition's load itself resumable/idempotent at the *row* level (not just partition level) if the process died halfway through writing `fact_casting` for a given date — would the existing `ON CONFLICT DO UPDATE` upsert already handle a safe re-run of that one partition, or is something missing?

---

## Task 21 — End-to-end data quality validation

### What Was Built
`data_quality/warehouse_checks.py`, a validation module that runs *after* the loaders have already run and asks two different questions than `silver_checks.py` did: "do the foreign keys in the fact tables actually resolve?" and "did every stage of the pipeline (Bronze → Silver → Gold → Warehouse) end up with a *sane* number of rows for a given date?" It produces one flat list of `CheckResult`s and an overall pass/fail, exactly like Task 13's Silver checks, but pointed at the warehouse and the whole pipeline instead of a single layer.

### Concepts Used
- **Defense-in-depth validation**: PostgreSQL's `FOREIGN KEY` constraints (Task 17) already make it *impossible* to insert a `fact_casting` row with an `actor_id` that isn't in `dim_actor` — the database will reject the statement. So why re-check it in Python? Because the constraint only protects against bad inserts going *forward*; it says nothing about corruption introduced another way (a restored backup, a manual `UPDATE`, a migration that dropped the constraint temporarily). A checker that re-verifies invariants the database already enforces is redundant in the happy path and the whole point in every other path — this is the same reasoning behind writing tests for code that "obviously can't be wrong."
- **Orphan detection via `LEFT JOIN ... WHERE dim.pk IS NULL`**: `_count_orphans()` joins each fact table to its dimension and counts rows where the join found *no match*. This is the standard SQL idiom for "find rows in A that have no corresponding row in B" — an anti-join expressed with a `LEFT JOIN` plus a null filter, rather than a slower `NOT IN` subquery.
- **Row-count sanity checks aren't always strict equality**: naively, you'd expect "Bronze count == Silver count == Warehouse count." That's wrong here for a structural reason — `dim_movie` etc. *accumulate* across every ingestion_date via upsert (Task 18), so the warehouse table for one day's partition will almost always have *more* rows than that one Silver file (all the previous days' movies are still there). The correct invariant isn't equality, it's monotonic: Silver can never have more rows than Bronze provided (nothing is fabricated in a transform), and the warehouse can never have fewer rows than the Silver partition just loaded (nothing legitimately disappears on upsert). Picking the right invariant — not just "the numbers should match" — is the actual engineering judgment call in this task.
- **Distinguishing "no data yet" from "a checker failure"**: `check_gold_sanity()` and `check_fact_load_sanity()` both special-case `silver_movies_count == 0` — if there was truly no Silver input for a date, an empty Gold dataset or zero fact rows is *correct behavior*, not a bug. Only flag it as a failure when there *was* Silver data and downstream is empty anyway (that's the sign of a loader silently swallowing everything). Skipping this distinction would make the checker cry wolf on every day with no new ingestion, which teaches people to ignore it.

### Key Code
`data_quality/warehouse_checks.py` — `_count_orphans()`:
> Runs `SELECT COUNT(*) FROM {fact_table} f LEFT JOIN {dim_table} d ON f.{fk_col} = d.{dim_pk} WHERE d.{dim_pk} IS NULL` for each of the six FK relationships in the star schema. Any non-zero count means a fact row's foreign key doesn't exist in the referenced dimension — something the `FOREIGN KEY` constraint should already prevent, so a non-zero result here is a signal something bypassed normal insert paths.

`data_quality/warehouse_checks.py` — `_check_entity_counts()`:
> Reads the Silver Parquet for one entity, compares its row count against the Bronze object count for that date (must not exceed it), then compares it against the current warehouse table's total row count (must not be *less than* it, since the warehouse accumulates). Both comparisons use `<`/`>` rather than `==` on purpose — see "row-count sanity checks aren't always strict equality" above.

`data_quality/warehouse_checks.py` — `check_fact_load_sanity()`:
> Counts rows in each fact table filtered to `WHERE ingestion_date = :date`. If Silver had real rows for that date but the fact table shows zero for that same date, that's flagged as a failure — a loader that silently drops everything (e.g. every row fails an FK lookup) looks identical to "clean, quiet day" unless you check this explicitly.

### What to Study Next
This module was written and unit-tested against fully mocked S3/DB state — it has *not* yet been run against a real multi-partition Bronze→Silver→Gold→Warehouse pipeline, because (same blocker as Tasks 19–20) the S3 bucket currently only has Bronze `movies/` data. Once Bronze `movie_details`/`credits` and the Silver transforms have actually been run for a real date, re-run `python -m data_quality.warehouse_checks --date <that date>` and see whether the row-count invariants hold in practice — a live run is the real test of whether the chosen invariants (not strict equality) are actually correct, versus just plausible on paper.

---

## Task 22 — Analytics SQL queries

### What Was Built
Seven standalone `.sql` files in `warehouse/queries/` answering concrete business questions over the star schema: top-rated directors, most productive actors, revenue by genre, movies by decade, director rating trend over time, actor collaboration frequency, and genre growth over time. These are meant to be run directly against the warehouse (or later wired into the Django Analytics dashboard in Task 30) — no Python wrapper was written, since the task only calls for SQL files.

### Concepts Used
- **Grain mismatch and double-counting**: `fact_movie_metrics` is exploded to one row per `(movie_id, genre_id)` (from Task 19, so a movie's rating/revenue can be joined against every genre it belongs to). That means naively `AVG(rating)` or `SUM(revenue)` grouped by director/decade would count a movie with 3 genres three times. The fix used everywhere here is a `WITH movie_ratings AS (SELECT DISTINCT movie_id, rating ...)` CTE — collapse back to one row per movie *before* aggregating, then join that clean set to whatever dimension you're grouping by. This is a general lesson: before writing `SUM`/`AVG` over a joined result, always ask "what is the grain of the table I'm aggregating, and does my join fan it out?"
- **Self-join for pairwise relationships**: `actor_collaboration_frequency.sql` joins `fact_casting` to itself (`fc1`/`fc2`) on `movie_id` to find every pair of actors who share a movie. The join condition `fc1.actor_id < fc2.actor_id` (strict inequality, not `!=`) does two things at once: it excludes an actor pairing with themselves, and it keeps only one direction of each pair (so actor A paired with B appears once, not once as A-B and once as B-A). This pattern — self-join plus an ordering predicate on the join key — is the standard way to enumerate unordered pairs from a one-column-per-row table in SQL.
- **CTEs (`WITH ... AS (...)`) as named, reusable subqueries**: every query here uses a CTE rather than a bare subquery in the `FROM` clause, purely for readability — it lets the "de-duplicate to movie grain" step be named and read top-to-bottom instead of buried inline. It doesn't change the query plan meaningfully in PostgreSQL for these simple cases, but naming intermediate steps makes SQL much easier to review later.
- **Verifying SQL without real data**: with no Silver/warehouse data loaded yet (same blocker since Task 19), correctness of *results* can't be checked. What can be checked is that each query is syntactically valid and executes against the real schema — done by running all seven through `warehouse.db.get_session()` via a short throwaway script, confirming each returns `0 rows` with no error rather than a `column does not exist` or type error. This catches schema-mismatch bugs even with an empty database; it does not catch logic bugs (e.g. picking the wrong join condition) that would only show up with real rows to eyeball.

### Key Code
`warehouse/queries/actor_collaboration_frequency.sql`:
> `JOIN fact_casting fc2 ON fc1.movie_id = fc2.movie_id AND fc1.actor_id < fc2.actor_id` — the `<` is what turns a self-join (which would otherwise produce every ordered pair including self-pairs) into "each unordered pair exactly once."

`warehouse/queries/top_rated_directors.sql`:
> `WITH movie_ratings AS (SELECT DISTINCT movie_id, rating, vote_count FROM fact_movie_metrics)` — this is the recurring fix for the genre-fanout problem; every query that touches movie-level rating/revenue reuses this shape.

### What to Study Next
Once real Bronze→Silver→warehouse data exists, run all seven queries and sanity-check the actual output — in particular, check whether `movies_by_decade.sql`'s `LEFT JOIN` to `movie_ratings` (used so movies with no fact rows still show up in the decade count) produces the count you'd expect versus an `INNER JOIN`. Also worth studying: `EXPLAIN ANALYZE` on `actor_collaboration_frequency.sql` once `fact_casting` has real volume — a self-join can get expensive, and this is a good first real query to learn to read a PostgreSQL query plan on.

## Task 23 — Django project & `core` app

### What Was Built
The first piece of the Django UI: a real Django project (`theoria_site`) living inside `django_app/`, wired to talk to two different databases — Django's own small sqlite database for things like the admin login, and the existing PostgreSQL warehouse (read-only) for all the movie data. A `core` app was added as a home for shared plumbing (the base page template, the database router), and a page skeleton (`base.html`) with a nav bar for Home / Movies / Analytics was created for every future page to extend.

### Concepts Used
- **Multi-database Django**: Django can talk to more than one database at once via the `DATABASES` setting, each identified by an alias (`default`, `warehouse`). A **database router** (a small class with `db_for_read`/`db_for_write`/`allow_migrate`) tells Django which alias a given app's models should use, and can refuse to let `migrate` touch a database at all — this is how the warehouse stays read-only from Django's side even though nothing in Postgres itself is locked down.
- **Namespace/module shadowing**: Python resolves imports by walking `sys.path` in order. Because `manage.py` adds the current directory to `sys.path`, a plain folder named `core/` sitting there is enough for `import core` to succeed — even with no code in it — which collided with Django's internal `django.core` package and made the `startapp` command refuse the name. This is why the app had to be built by hand instead.
- **Single source of truth for config**: rather than re-typing `SECRET_KEY`/`DEBUG`/DB credentials into `settings.py`, the settings module imports the existing `config.py` (adding the repo root to `sys.path` first) so there is exactly one place secrets and env-derived values are read from.
- **`managed = False` models (preview for Task 24)**: Django normally owns the tables behind its models (creates/alters them via migrations). For tables that already exist and are owned by something else — here, the ETL warehouse loaders — models are marked `managed = False` so Django only ever reads/writes rows, never touches schema.

### Key Code
`django_app/theoria_site/settings.py`:
> `_warehouse_url = urlparse(config.DATABASE_URL.replace(...))` splits the single SQLAlchemy-style connection string already used by the ETL/warehouse code into the pieces Django's `DATABASES` dict wants (`NAME`, `USER`, `PASSWORD`, `HOST`, `PORT`), so the connection string is still defined in exactly one place (`config.py`) even though two different libraries (SQLAlchemy and Django) each want it in a different shape.

`django_app/core/routers.py` — `WarehouseRouter.allow_migrate()`:
> Returns `False` whenever `db == "warehouse"` or the app is one of the warehouse-backed apps, and `None` (meaning "no opinion, let another rule decide") otherwise. Returning `None` rather than `True` matters — it lets Django's default behavior handle every other combination instead of this router silently claiming authority over databases it doesn't care about.

### What to Study Next
Read Django's own docs page on ["Multiple databases"](https://docs.djangoproject.com/en/5.1/topics/db/multi-db/), specifically the router methods table — the fact that returning `None` vs `False` vs `True` all mean different things is a common source of subtle bugs. Also worth trying: temporarily comment out `DATABASE_ROUTERS` and run `manage.py migrate`, then check (via `psql` or `connections['warehouse']`) whether Django tried to create its `auth_user`/`django_session` tables inside the warehouse — seeing the router's absence break something is the fastest way to understand what it was actually protecting.

---

## Task 24 — `movies` app: models

### What Was Built
Django ORM model classes for every table in the PostgreSQL warehouse — `Movie`, `Actor`, `Director`, `Genre`, `Date` for the dimensions, and `MovieMetrics`, `Casting` for the two fact tables. These models let the rest of the Django app (views, templates) query the warehouse using normal Python/ORM syntax (`Movie.objects.filter(...)`) instead of hand-writing SQL everywhere, while guaranteeing Django never tries to create, alter, or drop any of these tables — the ETL pipeline already owns that schema.

### Concepts Used
- **`managed = False`**: tells Django "this table already exists, don't generate migrations for it, don't touch its schema — only read/write rows." This is the ORM equivalent of read-only access; it complements (doesn't replace) `WarehouseRouter.allow_migrate()` from Task 23, which blocks migrations at the database-routing layer. Two independent walls around the same guarantee.
- **`db_table` / `db_column`**: override the default table/column names Django would guess from the class/field name, so the model can point at the exact existing `dim_movie`, `fact_casting`, etc. tables and columns without renaming anything in Postgres.
- **Composite primary keys and their absence in Django**: Postgres lets a table's primary key span multiple columns (`fact_movie_metrics`'s real key is `(movie_id, date_id, genre_id)`), but Django's ORM requires exactly one field marked `primary_key=True` per model — it has no native concept of a composite key. The workaround here is to mark one FK (`movie`) as the Django-level "pk" purely so the model is valid, while the actual uniqueness constraint is enforced only by the database, never by the ORM.
- **`ForeignKey(on_delete=models.DO_NOTHING)`**: normally `on_delete` decides what Django does to child rows when a parent is deleted (`CASCADE`, `SET_NULL`, etc.). Since this app never deletes anything (read-only, unmanaged), `DO_NOTHING` is the honest choice — it tells the ORM to not even try to enforce delete behavior it will never trigger.
- **System check framework**: `manage.py check` runs a set of correctness rules over your models before you ever hit the database. It caught `fields.W342` (a FK marked `unique=True`/`primary_key=True` behaves like a `OneToOneField`) — a true statement about the *model*, even though it's not true about the *data* (multiple `fact_movie_metrics` rows do share a `movie_id`). `SILENCED_SYSTEM_CHECKS` is the sanctioned way to say "I've seen this warning, I understand why it fires, and it doesn't apply here" instead of restructuring the model to make the checker happy.

### Key Code
`django_app/movies/models.py` — the `movie` field on `MovieMetrics` and `Casting`:
```python
movie = models.ForeignKey(
    Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True
)
```
> This single line is doing two unrelated jobs at once: (1) declaring a real foreign key relationship to `dim_movie` for query convenience (`metrics.movie.title`), and (2) satisfying Django's "every model needs one pk field" rule. Job (2) is a technicality, not a true statement about uniqueness — worth remembering the next time a composite-key legacy table needs an ORM model, since this exact trick will come up again.

`django_app/theoria_site/settings.py` — `SILENCED_SYSTEM_CHECKS = ['fields.W342']`:
> A short, commented list of check IDs Django should skip. The comment above it explains *why* the warning fires and *why* it's safe to ignore — the important habit here is that silencing a check should always come with a reason written down next to it, not just the bare check ID.

### What to Study Next
Django added real composite primary key support in 5.2 (`CompositePrimaryKey`) — this project is pinned to 5.1, which is why Task 24 needed the `primary_key=True`-on-one-FK workaround. Look up what `CompositePrimaryKey` looks like in 5.2+ and compare it to the workaround used here — would upgrading remove the need for `SILENCED_SYSTEM_CHECKS` entirely? Also worth trying once real data exists (post Task 19–22 blocker): `Casting.objects.using("warehouse").select_related("movie", "actor", "director")` and watching the generated SQL with `django.db.connection.queries` — this is the N+1-query problem Task 26 will need to avoid.

## Task 25 — Home page

### What Was Built
The site's landing page at `/`: a view that pulls four numbers out of the warehouse (total movies, actors, directors, and the average movie rating) and a template that displays them.

### Concepts Used
- **Multi-database routing in the ORM**: every query explicitly calls `.using("warehouse")` because the project has two databases configured (`default` = sqlite for Django's own admin/session tables, `warehouse` = the real Postgres star schema). Without `.using()`, Django would fall back to `default` (via `WarehouseRouter`, which would then refuse the query since these models don't belong there).
- **Aggregation vs. iteration**: `Movie.objects.using("warehouse").count()` and `MovieMetrics.objects.aggregate(Avg("rating"))` both push the computation down to a single SQL `COUNT(*)` / `AVG(...)` query in Postgres, rather than pulling every row back into Python and counting/summing there. This matters a lot as the table grows — one round trip and one number back, not thousands of rows.
- **URL namespacing**: `movies/urls.py` sets `app_name = "movies"`, which lets templates reference `{% url 'movies:home' %}` instead of a hardcoded `/` — useful once more apps (analytics) add their own `home`-like names, since namespacing keeps `home` in `movies` from colliding with `home` in another app.
- **Template inheritance**: `home.html` uses `{% extends "base.html" %}` + `{% block content %}`, so the nav bar and page shell are defined once and every page (this one, and Tasks 26–30) just fills in its own middle section.

### Key Code
`django_app/movies/views.py` — `home()`:
> Four independent queries, each aggregated in the database rather than in Python, assembled into a plain dict and handed to `render()`. No business logic beyond "ask the warehouse for four numbers" — that's the whole job of a Django view: gather context, pick a template, return a response.

`django_app/movies/templates/movies/home.html`:
> `{% if avg_rating %}...{% else %}—{% endif %}` guards against `None`, which is exactly what `Avg("rating")` returns when the underlying table is empty (as it is right now, pending real Silver data) — SQL `AVG()` over zero rows is `NULL`, not `0`, so this isn't a hypothetical edge case, it's the current live state of the app.

### What to Study Next
Once real data exists, add a second view that needs a JOIN instead of a flat aggregate (Task 26's movie detail page: movie + genres + cast) and compare the query count via `django.db.connection.queries` with and without `select_related`/`prefetch_related` — that's the concrete N+1 lesson this project has been building toward since Task 24's docstring first mentioned it.

## Task 26 — Movie Details page

### What Was Built
A detail page for a single movie at `/movies/<id>/`: title, release date, runtime, budget, revenue, status, its genres, and a cast/crew table — all pulled from the warehouse in exactly three queries, with a proper 404 when the id doesn't exist.

### Concepts Used
- **`get_object_or_404`**: wraps "fetch one row, or return a clean HTTP 404 if it's missing" instead of writing `try/except Movie.DoesNotExist` by hand in every view. It still needs `.using("warehouse")` on the queryset passed in, since the shortcut just calls `.get()` under the hood.
- **Reverse foreign-key traversal**: `Genre.objects.filter(moviemetrics__movie_id=movie_id)` walks *backwards* across the `MovieMetrics.genre` FK — Django auto-generates the lowercase-model-name accessor (`moviemetrics`) on `Genre` even though `Genre` itself never declares that relationship. This is how you ask "which genres does this movie have" when the FK direction only goes fact → dimension, not the other way.
- **`.distinct()` after a fan-out join**: `fact_movie_metrics` has one row per `(movie_id, genre_id)`, so filtering by `movie_id` alone would return the same genre multiple times if the underlying join weren't already grouped — `.distinct()` collapses that back to one row per genre.
- **Avoiding N+1 with `select_related`**: without it, looping over `cast` in the template and touching `credit.actor.name` / `credit.director.name` would fire one extra `SELECT` per cast row (N+1 queries: 1 to get the rows, N more to get each actor and director). `select_related("actor", "director")` tells Django to pull all three tables in a single `JOIN`ed query up front.

### Key Code
`django_app/movies/views.py` — `movie_detail()`:
```python
cast = (
    Casting.objects.using("warehouse")
    .filter(movie_id=movie_id)
    .select_related("actor", "director")
)
```
> This is the line that turns what would otherwise be a classic N+1 bug into a single query. `select_related` only works for FK/one-to-one relationships (it does a SQL JOIN) — it wouldn't work for a many-to-many or reverse FK, where `prefetch_related` (a second, separate query) is the right tool instead.

`django_app/movies/urls.py` — `path("movies/<int:movie_id>/", views.movie_detail, name="movie_detail")`:
> The `<int:movie_id>` path converter both extracts the id from the URL and validates it's an integer before the view ever runs — a non-numeric id in the URL never reaches `movie_detail` at all, Django 404s it earlier in routing.

### What to Study Next
Once real Silver/warehouse data exists, run this view with `django.db.connection.queries` (or `django-debug-toolbar`) turned on and confirm it's really 3 queries, not more — then intentionally delete the `select_related` call and watch the query count grow with cast size, to see the N+1 problem happen for real instead of just reading about it.

## Task 27 — Actor Details page

### What Was Built
An actor's page at `/actors/<id>/`: their filmography (every movie they were credited in) plus three computed career stats — total film count, average rating across their films, and the year range their career spans.

### Concepts Used
- **Fan-out from a bridge/fact table**: `fact_casting` stores one row per `(movie_id, actor_id, director_id)` — so an actor in a movie with two directors would appear twice for that movie if queried naively. `Casting.objects.filter(actor_id=...).values_list("movie_id", flat=True).distinct()` collapses that back to one id per movie before it's ever used to pull `Movie` rows, the same fan-out problem Task 26 solved for genres.
- **Aggregation over a de-duplicated subquery**: `fact_movie_metrics` has one row per `(movie_id, genre_id)` too, so a plain `Avg("rating")` filtered by a list of movie ids would weight multi-genre movies more heavily. `.values("movie_id", "rating").distinct()` first collapses to one row per movie (same rating value repeats across that movie's genre rows, so distinct on the pair keeps exactly one), *then* `.aggregate(Avg("rating"))` runs on top of that — Django compiles this as a `SELECT AVG(rating) FROM (SELECT DISTINCT movie_id, rating FROM ...) `, doing the de-dup and the average both in the database, not in Python.
- **`Min`/`Max` as SQL aggregates**: `career_span = filmography.aggregate(earliest=Min("release_date"), latest=Max("release_date"))` — same idea as `Avg` in Task 25, just a different aggregate function; Postgres computes the min/max release date across the actor's filmography in a single query rather than Python calling `min()`/`max()` on a list of fetched rows.
- **Reusing a queryset across multiple aggregates without re-querying**: `filmography` (a `Movie` queryset) is used for the table render, `.count()`, *and* the `Min`/`Max` aggregate — each of those triggers its own SQL query when evaluated (querysets are lazy), so this is three queries against the same filtered set, not one queryset object doing triple duty for free. Worth remembering when reasoning about a view's total query count, same theme Task 26 introduced with `select_related`.

### Key Code
`django_app/movies/views.py` — `actor_detail()`:
```python
movie_ratings = (
    MovieMetrics.objects.using("warehouse")
    .filter(movie_id__in=movie_ids)
    .values("movie_id", "rating")
    .distinct()
)
avg_rating = movie_ratings.aggregate(avg_rating=Avg("rating"))["avg_rating"]
```
> This is the line that keeps the average correct in the presence of the genre fan-out. The comment above it in the source explains why a bare `.aggregate(Avg("rating"))` on the unfiltered join would silently give multi-genre movies extra weight — a subtle correctness bug that wouldn't show up until real multi-genre data exists (still blocked, per every prior task's Outcome).

### What to Study Next
Once real data exists, compare the actual SQL Django generates for the `.values().distinct().aggregate()` pattern (via `str(queryset.query)` or `connection.queries`) against writing the equivalent by hand as a raw `.sql` file — Task 22 already has hand-written SQL for exactly this kind of aggregation (`top_rated_directors.sql`, etc.), so it's a good exercise to see whether the ORM-generated query matches what you'd have written directly, and whether one is more readable/efficient than the other.

## Task 28 — Director Details page

### What Was Built
A director's page at `/directors/<id>/`, structurally identical to Task 27's actor page: filmography, film count, average rating, and career span — just filtered on `director_id` instead of `actor_id`.

### Concepts Used
- **Recognizing when a pattern is a genuine mirror, not a near-miss**: `fact_casting` stores `(movie_id, actor_id, director_id)`, so swapping which FK column you filter on (`director_id=` vs `actor_id=`) is enough to get an equivalent view — no new fan-out shape, no new aggregation logic. Confirming this before writing anything (rather than re-deriving the query design from scratch) is itself the lesson: once a pattern is proven correct once (Task 27), re-verifying its assumptions instead of blindly copy-pasting is what makes reuse safe.
- **Same fan-out, different FK**: a movie with multiple actors under one director would otherwise repeat that movie once per actor when filtering `Casting` by `director_id` — the same `.values_list("movie_id", flat=True).distinct()` collapse from Task 27 is required here for the identical structural reason (fact table granularity is per actor/director *pair*, not per movie).

### Key Code
`django_app/movies/views.py` — `director_detail()`:
> Byte-for-byte the same query structure as `actor_detail()` — `get_object_or_404` on `Director`, distinct `movie_id`s from `Casting` filtered by `director_id`, then the same distinct-then-average pattern over `MovieMetrics` and the same `Min`/`Max` career span. The only differences are the model (`Director` vs `Actor`) and the filter field (`director_id` vs `actor_id`) — everything else, including *why* each `.distinct()` is needed, carries over unchanged from Task 27's reasoning.

### What to Study Next
Now that three detail pages (movie, actor, director) share almost the same shape — fetch one row, resolve a fan-out to distinct movie ids, aggregate over those ids — consider whether a small shared helper (e.g. a `_person_filmography_stats(model, id_field, id_value)` function) would reduce duplication, or whether keeping them separate is actually clearer since the three views may diverge later (e.g. Task 29's genre page needs a *different* aggregation — top movies + revenue trend, not just count/avg/span). Good exercise in judging premature abstraction versus real duplication.

## Task 29 — Genre Details page

### What Was Built
A genre's page at `/genres/<id>/`: the genre's average rating and movie count, its top-10 rated movies, and a revenue-by-year trend table.

### Concepts Used
- **"Reuse Gold-layer aggregates" doesn't always mean "read the Gold file"**: `etl/gold/build_gold_datasets.py` already computes genre-level `avg_rating`/`total_revenue`/`movie_count` — but it writes that result to Parquet in S3, and Django's `warehouse` database connection only points at Postgres. There's no loader that pushes Gold datasets into the warehouse. So "reuse" here means reusing the *aggregation logic* (group by genre, average rating, sum revenue) re-expressed as an ORM query against `fact_movie_metrics`, not literally opening the same file. Recognizing which parts of a spec are structural intent versus a specific implementation detail is the actual skill.
- **`annotate()` + `values()` + `annotate()` for grouped aggregates**: `.annotate(year=ExtractYear(...)).values("year").annotate(total_revenue=Sum(...))` is Django's idiom for "GROUP BY" — the first `annotate` computes a per-row value, `.values("year")` sets the GROUP BY column, and the second `.annotate()` runs the aggregate *within* each group. Getting the order right matters: an `.annotate()` after `.values()` groups by the `values()` fields; before it, it's a per-row column.
- **`ExtractYear` as a database function**: pulls the year out of a `DateField` as part of the SQL query (`EXTRACT(YEAR FROM ...)` in Postgres) rather than fetching full dates into Python and grouping there — same "push the work into the database" theme as `Avg`/`Sum`/`Min`/`Max` in Tasks 25–28.
- **Checking fan-out risk before reusing a "distinct" habit, not applying it reflexively**: Tasks 26–28 needed `.distinct()` because their fact-table filters could return more than one row per movie (multi-genre, multi-director). Here, `MovieMetrics` is already filtered to one specific `genre_id`, so each movie contributes at most one row — grouping directly on the filtered queryset for the revenue trend is safe without an extra distinct step. `movie_count` still uses `.values("movie_id").distinct().count()` defensively, but the revenue aggregation doesn't need it.

### Key Code
`django_app/movies/views.py` — `genre_detail()`:
```python
revenue_by_year = (
    metrics.filter(movie__release_date__isnull=False)
    .annotate(year=ExtractYear("movie__release_date"))
    .values("year")
    .annotate(total_revenue=Sum("movie__revenue"))
    .order_by("year")
)
```
> This single queryset compiles to one SQL query: join to `dim_movie`, extract the year, group by it, sum revenue per group, order by year — no Python-side looping or dict-building. Compare this to Task 27/28's `.values("movie_id", "rating").distinct()` pattern: both are "group in the database, not in Python," just for different shapes of grouping (dedup vs. bucket-by-year).

### What to Study Next
Once real Silver/warehouse data exists, compare this view's generated SQL (`str(revenue_by_year.query)`) against the pandas `groupby` in `_build_genre_metrics()` — same aggregation, two different engines (Postgres vs. pandas). Worth understanding *when* you'd want the Gold/pandas version instead (e.g. genre metrics reused across many pages, expensive to recompute per request) versus computing it live per-request as this view does — that's the general tradeoff between pre-aggregation and on-demand querying that Gold layers exist to solve.

## Task 30 — Analytics Dashboard

### What Was Built
A single `/analytics/` page with seven panels — one per Task 22 query (top-rated directors, most productive actors, revenue by genre, movies by decade, director trend over time, actor collaboration frequency, genre growth over time) — plus two Chart.js line/bar charts (avg rating by decade, revenue by genre) fed from the same query results.

### Concepts Used
- **Reusing hand-written SQL instead of re-deriving it in the ORM**: Tasks 25–29's views all built queries through Django's ORM. These seven queries already exist as reviewed, commented `.sql` files (with CTEs to de-duplicate `fact_movie_metrics`'s per-genre fan-out) — re-expressing a self-join like `actor_collaboration_frequency.sql` (`fc1.actor_id < fc2.actor_id`) in the ORM would be more code for an identical result. The project rule "all analytics SQL lives in `.sql` files" is honored literally: the view reads and executes the files as-is, rather than treating them as a spec to reimplement.
- **Raw SQL via Django's multi-database connection**: `django.db.connections["warehouse"]` gives a plain DB-API cursor into the same Postgres warehouse the ORM models point at, without going through any model — appropriate when a query (grouped self-join, multiple CTEs) doesn't map cleanly onto a single model's fields.
- **`cursor.description` for dynamic column names**: since the view doesn't know each query's output shape ahead of time, `[col[0] for col in cursor.description]` reads the actual column names Postgres returned, and `dict(zip(columns, row))` turns each row into a dict the template can address by key (`row.total_revenue`) instead of by fragile positional index.
- **`json_script` template filter for chart data**: passing Python lists into a `<script>` block naively risks HTML/JS injection if any value contains user-influenced text (here it's just numbers/labels from a trusted DB, but the habit matters). `{{ data|json_script:"id" }}` renders a `<script type="application/json">` tag Django escapes safely, and the page's own script reads it back with `JSON.parse(...).textContent` — the standard safe pattern for handing server data to client JS.
- **Decimal isn't JSON-serializable**: Postgres `NUMERIC` columns come back as `decimal.Decimal` via psycopg2; `json_script` (like `json.dumps`) can't serialize those directly, so the two chart datasets are explicitly cast to `float` in the view before being handed to the template, while the plain HTML tables render the original `Decimal` values untouched (no precision loss there, since Django templates print them fine).

### Key Code
`django_app/analytics/views.py` — `_run_query()`:
```python
def _run_query(filename):
    sql = (QUERIES_DIR / filename).read_text()
    with connections["warehouse"].cursor() as cursor:
        cursor.execute(sql)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
```
> One tiny helper covers all seven panels: read the `.sql` file's text, execute it verbatim against the warehouse connection, and shape the result as a list of dicts. No query-specific code lives in the view at all — the SQL files are the single source of truth for both *what* to compute and *how*.

### What to Study Next
This view re-executes all seven queries (some with multi-way joins) on every request, with no caching — fine for a learning project against an empty warehouse, but worth studying Django's per-view or low-level cache framework (or a materialized view refreshed by a scheduled job) as the standard fix once a dashboard like this needs to serve real traffic against a populated warehouse.

## Task 30.5 — First real end-to-end pipeline run

### What Was Built
`scripts/run_pipeline.py`: a single orchestration script that runs every existing ETL stage in order — Bronze ingestion, all four Silver transforms, Gold aggregation, both warehouse loaders, both DQ check suites — for one `ingestion_date`. Ran it live against real TMDB data (100 movies) for the first time in the project, populating the warehouse and every Django page built in Tasks 25–30 with real content instead of empty states.

### Concepts Used
- **Orchestration vs. new logic**: every stage function (`ingest_movies`, `transform_movies`, `load_facts`, etc.) already existed, already had unit tests, and already worked correctly in isolation. This task added zero new *business* logic — it only sequenced existing calls. Recognizing "this is a wiring problem, not a build problem" avoided reinventing anything already proven correct.
- **In-process function calls vs. CLI subprocess chaining**: `ingest_movie_details()` and `ingest_credits()` both require a `--movie-ids` list on their CLI, but nothing in the pipeline persists `ingest_movies()`'s returned ID list to a file — each script is designed to be one independent process, so chaining them via shell (`python -m etl.bronze.ingest_movies && python -m etl.bronze.ingest_movie_details --movie-ids ???`) has no natural way to pass that list along. Calling all three as plain Python functions inside one script instead makes `movie_ids` just a local variable — no serialization, no glue script, no CLI limitation to work around.
- **Upsert idempotency as a design property, not a runtime check**: every warehouse loader uses `INSERT ... ON CONFLICT DO UPDATE` (Tasks 18–19), so `run_pipeline()` doesn't need any "have I already run this?" guard logic itself — re-running for the same date is safe by construction, and that safety was already unit-tested when the loaders were built. Trusting a lower layer's proven guarantee instead of re-verifying it here is the same reasoning as Task 28's "don't re-derive, reuse the proof."
- **Reading a full pipeline's real DQ output for the first time**: with actual multi-partition data, `fact_casting` rejected ~46% of its bridge rows (1781 of 3852) — not a bug, but the documented consequence (Task 19) of resolving `fact_casting`'s NOT NULL `actor_id`/`director_id` pair by cross-joining each movie's actors with its directors: a movie with credited actors but *no* credited director in TMDB's data contributes zero valid casting rows, and every one of its actor rows gets quarantined. Seeing the real reject count made a previously abstract design tradeoff concrete.

### Key Code
`scripts/run_pipeline.py` — `run_pipeline()`:
```python
movie_ids = ingest_movies(ingestion_date=ingestion_date, max_pages=max_pages)
succeeded_details, failed_details = ingest_movie_details(movie_ids, ingestion_date=ingestion_date)
succeeded_credits, failed_credits = ingest_credits(movie_ids, ingestion_date=ingestion_date)
```
> `movie_ids` is a plain Python list passed directly into the next two calls — no file write, no S3 round-trip, no CLI argument parsing in between. This is the entire fix for the "how do movie_details/credits know which IDs to fetch" gap: it was never a missing feature in the ingestion scripts, just a question of *how* they're invoked.

### What to Study Next
Now that real data exists, Task 31 (Tests) should account for `fact_casting`'s reject rate as expected behavior rather than a symptom to chase — worth writing an assertion that reject counts stay *bounded* (e.g. under some percentage) rather than zero, since zero rejects would actually be suspicious given the known data shape. Also worth studying: at what data volume would `python -m scripts.run_pipeline` become too slow to run synchronously (this 100-movie run took ~2.5 minutes, dominated by per-movie TMDB API calls), and what that implies about needing a real workflow scheduler (Airflow, Dagster) instead of a single script — one of the explicit non-goals this project chose to skip, but useful to understand *why* those tools exist.

---

## Task 31 — Tests

### What Was Built
A gap check across the whole `tests/` suite before writing anything: the Silver-transform-fixture and DQ-check-catches-a-bad-row requirements from the task list were already satisfied by tests added incrementally in Tasks 9–14/13. The only real gap was `tests/test_django_views.py`, which didn't exist. Added it with 10 tests covering all five `movies` views (home, movie/actor/director/genre detail — each with a 404 case) and the `analytics` dashboard. Full suite: 159/159 passing.

### Concepts Used
- **Testing unmanaged/read-only ORM models without a live database**: `movies/models.py`'s models point at a real Postgres warehouse, but the rest of this project's tests never touch real infrastructure (S3 and the DB engine are always mocked). The same rule applies here: every `Model.objects` manager (`Movie.objects`, `Actor.objects`, etc.) is replaced with a `MagicMock` for the duration of each test, so a view can be driven end-to-end through Django's real URL routing without ever opening a socket to Postgres.
- **Constructing model instances without hitting the database**: `Movie(movie_id=1, title="Test Movie", ...)` just builds a plain Python object in memory — Django only talks to the database when you call `.save()`, `.delete()`, or run a queryset. This means fixture objects can be *real* `Movie`/`Actor`/`Genre` instances (with all their real field behavior) instead of hand-rolled mocks, which is both simpler and closer to what the view actually receives at runtime.
- **`django.test.Client` + `response.context`**: Django's test client can inspect what context dict a view passed to `render()` via `response.context`, but only because `django.test.utils.setup_test_environment()` connects a `template_rendered` signal listener that records it. That wiring is normally done for you by Django's own test runner or the `pytest-django` plugin — since this project runs plain `pytest` (no `pytest-django` in `requirements.txt`, matching the rest of the suite's minimal-dependency style), it had to be called explicitly in `setup_module`/`teardown_module`.
- **Mocking a chained QuerySet API**: view code like `Casting.objects.using("warehouse").filter(...).select_related(...)` is a chain of method calls, and each step on a `MagicMock` auto-creates a new child mock. The pattern used throughout the new tests is to set `.return_value` only on the *last* call in a chain (e.g. `casting_mgr.using.return_value.filter.return_value.select_related.return_value = [casting]`), leaving every intermediate step as an unconfigured (but harmless) auto-mock.
- **`Http404` as a control-flow exception**: `get_object_or_404()` doesn't return a sentinel on a miss — it raises `django.http.Http404`, which Django's URL resolver catches and turns into a real 404 response. The 404 tests patch `get_object_or_404` with `side_effect=Http404()` to simulate a missing row without needing a real "not found" query result.

### Key Code
`tests/test_django_views.py` — `test_movie_detail_returns_200_with_expected_context()`:
```python
with patch("movies.views.get_object_or_404", return_value=movie), patch.object(
    Genre, "objects", new=MagicMock()
) as genre_mgr, patch.object(Casting, "objects", new=MagicMock()) as casting_mgr:
    genre_mgr.using.return_value.filter.return_value.distinct.return_value = [genre]
    casting_mgr.using.return_value.filter.return_value.select_related.return_value = [casting]
    response = client.get(f"/movies/{movie.movie_id}/")
```
> Three independent things are mocked at exactly the boundary where the view talks to the database — the primary object lookup, and the two related-object queries — while everything else (URL resolution, view logic, template rendering) runs for real. This is the difference between a *unit* test of the view function and an *integration* test that would need a live warehouse; here we get view-logic + template correctness without the infrastructure dependency.

`tests/test_django_views.py` — `setup_module()` / `teardown_module()`:
```python
def setup_module(module):
    setup_test_environment()

def teardown_module(module):
    teardown_test_environment()
```
> Without this pair, `response.context` would simply not exist on responses returned by `client.get(...)` — the signal that populates it is opt-in infrastructure, not something `django.test.Client` does unconditionally. This is a small but easy-to-miss piece of Django internals when writing tests outside of `TestCase`/`pytest-django`.

### What to Study Next
Look at what `pytest-django`'s `client` and `db` fixtures actually do under the hood (they call the same `setup_test_environment()`/`teardown_test_environment()` functions, plus wrap each test in a transaction when `db` is requested) — understanding the manual version here makes the "magic" fixture version much less mysterious. Also worth exploring: Django's `override_settings` decorator, which would let a future test point the `warehouse` connection at a real (but disposable) SQLite or Postgres test database instead of mocking the ORM — a heavier but more end-to-end alternative worth weighing against the mocking approach used here.

---

## Task 32 — Documentation

### What Was Built
No code changed — this task wrote the two documents a new contributor (`README.md`) and an outside reviewer (`docs/architecture.md`, new file) would each need. The README is a runbook: env setup, applying the three DDL files, running the pipeline end to end, running the Django server, running tests. `docs/architecture.md` is a design write-up: the data-flow diagram, the star schema with an ER sketch, and — most importantly — an explicit explanation of the two "gotchas" a reader would otherwise have to reconstruct from scattered task outcomes: why `fact_movie_metrics` needs a `SELECT DISTINCT` before aggregating (one row per genre), and why `fact_casting` rejects ~46% of candidate rows (the actor×director cross-join has no rows to produce for a movie with no credited director). Verified nothing broke by re-running the full suite (159/159 still passing, as expected for a docs-only change).

### Concepts Used
- **Writing for the reader, not for yourself**: the task-by-task outcome notes in `CLAUDE.md` are a faithful build log — useful for "what happened and when" — but a poor architecture document, because they're organized by *when something was built* rather than *why the system looks the way it does*. `docs/architecture.md` reorganizes the same underlying facts around the questions a reviewer actually asks: what does data do, why is it shaped this way, what would break if a layer were skipped.
- **Runbook vs. design doc as two different artifacts**: a README answers "how do I get this running," and should be a sequence of commands a stranger can copy-paste in order. An architecture doc answers "why is this correct," and should read as prose organized around design decisions and their trade-offs, not as a command list. Conflating the two (cramming design rationale into a README, or turning an architecture doc into a step-by-step tutorial) makes both worse at their actual job.
- **Documenting a known limitation vs. hiding it**: the `fact_casting` cross-join reject rate could be framed as a bug to eventually fix, but it's actually a direct, understood consequence of a schema decision (modeling casting as actor×director pairs) meeting a real data gap (some movies have no credited director in TMDB's data). Writing this down explicitly — with the *why*, not just the *what* — is what turns "an interviewer might ask about this weird 46% number" into a talking point that demonstrates understanding of the trade-off, rather than something to hope nobody notices.

### Key Code
No functions changed this task. The one design artifact worth pointing to is the ASCII data-flow diagram and star-schema ER sketch added to the top of `docs/architecture.md` — a diagram often communicates "what joins to what" faster than prose, and forces you to notice asymmetries (e.g. that `dim_date` only connects to one fact table, not both) that are easy to miss when reading DDL top to bottom.

### What to Study Next
Compare this project's `docs/architecture.md` against a real company's public engineering design-doc template (many are public — e.g. Google's design doc culture, or Stripe's/Airbnb's public engineering blogs on data platform design) to see what sections professional teams include that this one doesn't (rollout plan, alternatives considered and rejected, monitoring/alerting strategy) — useful context for Task 33, which is about production-readiness cleanup (config, logging, dependencies) rather than documentation, but touches the same "what would make this deployable" question from a different angle.

---

## Task 33 — Logging, config, and dependency cleanup

### What Was Built
An audit task, not a build task — no source files changed. Grepped the whole codebase for three categories of "config smell": hardcoded credentials/URLs (AWS key patterns, TMDB/S3/Postgres connection strings typed literally instead of read from `config.py`), scripts that skip `logging_config.setup_logging()`, and a `requirements.txt` that's drifted from what's actually imported and installed. Found nothing to fix — every check came back clean — but running the audit is itself the deliverable: it's the difference between *asserting* a project follows its own rules (as `CLAUDE.md`'s "Coding Rules" section does) and actually *verifying* it does, task 32 and earlier having accumulated 32 tasks worth of code where drift could easily have crept in unnoticed.

### Concepts Used
- **Config centralization as a testable property, not just a convention**: "all config from `config.py`" is easy to write down as a rule and easy to silently violate one script at a time (a stray `os.getenv("SOME_VAR")` added under deadline pressure, say). The way to actually enforce it isn't code review vigilance alone — it's a periodic grep sweep (`os.environ`, `getenv`, literal `postgresql://`, literal `s3://bucket-name`, AWS key regexes) that catches drift mechanically, the same way a linter catches style drift.
- **Why `pip freeze` isn't always safe to pipe straight into `requirements.txt`**: `pip freeze` dumps *every* package installed in the active environment, not just the ones your code imports. This venv had `graphify` (a separate CLI tool, plus its own tree-sitter/networkx/RapidFuzz dependency tree) installed alongside the project's real dependencies — a blind `pip freeze > requirements.txt` would have silently made "the graphify tool happens to be installed" into a declared dependency of a Django/pandas project, which is exactly the kind of accidental-coupling bug that makes a `requirements.txt` untrustworthy over time. The safer check is the *inverse* direction: grep actual `import`/`from` statements across the codebase, confirm every entry in `requirements.txt` is used, confirm every import is covered, then spot-check pinned versions against `pip show` rather than regenerating the whole file from environment state.
- **Indirect dependencies still need to be understood, even if they're not imported directly**: `pyarrow` never appears in an `import pyarrow` statement anywhere in this codebase, but it's still a required package — pandas uses it as a named engine (`df.to_parquet(..., engine="pyarrow")`) rather than importing it as a module directly. Auditing "is this dependency used" has to account for this indirect-usage pattern, not just grep for `import <name>`.

### Key Code
No functions changed. The verification command worth remembering is the two-directional check used here:
```bash
# forward: what does the code import?
grep -rhoE "^import [a-zA-Z0-9_]+|^from [a-zA-Z0-9_]+" --include="*.py" . | sort -u
# backward: what's pinned?
cat requirements.txt
```
> Diffing these two lists by hand (rather than trusting either one alone) is what catches both "declared but unused" and "used but undeclared" drift — the two failure modes a `requirements.txt` can silently develop over 30+ tasks of incremental changes.

### What to Study Next
Look into `pip-tools` (`pip-compile`) or `pipdeptree` as tools that automate exactly this kind of drift-check — `pipdeptree` in particular can show which installed packages are *not* depended on by anything else in the environment, which would have flagged the graphify-related packages automatically instead of requiring a manual import-list diff. Also worth studying: what a `requirements.txt` for this project would look like if it pinned transitive dependencies too (a full `pip freeze` scoped to a *clean* venv built only from `requirements.txt`, rather than this session's shared venv) — the trade-off between full reproducibility (pin everything) and readability (pin only direct dependencies, as this file currently does).

## Task 34 — Frontend rebuild (Workstream C: browsable + styled + visual)

### What Was Built
The Django site went from "type an integer ID into the URL bar" to an actual browsable
product. Four new list pages (`/movies/`, `/actors/`, `/directors/`, `/genres/`) with
search, sorting, and pagination; every detail page restyled and cross-linked (movie →
genres → actors → directors and back); a single hand-written CSS file with automatic
dark mode; and the analytics dashboard reorganized into cards. Templates already render
poster/backdrop/headshot images via a new `tmdb_image` filter — those columns don't exist
in the warehouse yet (that's Workstream B), and Django templates resolve missing
attributes to empty strings, so the image blocks simply don't render until the data
arrives. No pipeline code changed at all.

### Concepts Used
- **Pagination (`django.core.paginator.Paginator`)**: never send the whole table to the
  browser; `get_page()` clamps bad page numbers instead of crashing.
- **Query-string state**: search (`?q=`) and sort (`?sort=`) live in the URL, so results
  are shareable/bookmarkable; pagination links must re-carry `q` and `sort` or the
  filter resets on page 2.
- **NULLS LAST ordering (`F(...).desc(nulls_last=True)`)**: in Postgres, `DESC` puts
  NULLs *first* by default — without this, movies missing a release date would lead
  the "newest" list.
- **Annotation across a fact table (`Max("moviemetrics__rating")`)**: `dim_movie` has no
  rating column; rating lives in `fact_movie_metrics` (one row per genre), so sorting
  movies by rating requires a join + aggregate, done in SQL via `annotate`, not in Python.
- **Separation of data and presentation**: the warehouse stores only TMDB's *relative*
  image path; the CDN base URL and size are presentation concerns applied at render time
  by a template filter (base URL still from `config.py` — no hardcoded URLs rule).
- **Graceful degradation**: templates guard every image with `{% if %}`, so the same
  templates work before and after Workstream B lands.

### Key Code
`django_app/movies/views.py` — `movie_list()`:
> Builds the queryset in stages — filter by `?q`, annotate only when the sort actually
> needs the join (`sort == "rating"`), then order and paginate. The `MOVIE_SORTS` dict
> whitelists sort values, so a hand-crafted `?sort=` can never inject an arbitrary
> `ORDER BY` expression.

`django_app/movies/views.py` — `_person_list()`:
> Actors and directors need the identical list behavior against different tables, so one
> private helper takes the model class + title + URL name as parameters. Two views become
> two one-liners instead of copy-pasted twins.

`django_app/movies/templatetags/tmdb_images.py` — `tmdb_image`:
> A 4-line filter that turns `/abc.jpg` into `https://image.tmdb.org/t/p/w342/abc.jpg`
> and returns `""` for empty input. Putting this in one place means templates never
> concatenate URLs by hand, and swapping CDN or size later is a one-file change.

### What to Study Next
Django's template variable resolution order (dict lookup → attribute → method → index):
a test broke because a `MagicMock` "answered" the dict lookup that a real model object
would have failed, returning garbage before attribute lookup was ever tried. Read the
"Variables" section of the Django template language docs and re-explain why
`{{ m.movie }}` on a mock behaves differently than on a model instance.

## Task 36 + 37 — Image fields Silver → warehouse, and the live re-run that lit them up

### What Was Built
The site's templates already asked for movie posters, backdrops, taglines, and actor/director
headshots — but nothing showed, because those fields were being thrown away back at the Silver
cleaning step. This task carried them all the way through: TMDB Bronze JSON → Silver Parquet →
Postgres warehouse → Django ORM → template. No new API calls: every one of these fields was
already sitting in the raw Bronze payloads we'd downloaded months ago. Then (Task 37) we altered
the live database to add the new columns and re-ran the whole pipeline so the warehouse actually
filled in.

### Concepts Used
- **Additive schema migration**: adding columns to a table that already has data and is in use.
  You can't just re-run `CREATE TABLE` (it's `IF NOT EXISTS`, so it no-ops). You need an
  `ALTER TABLE ... ADD COLUMN`. Making it `ADD COLUMN IF NOT EXISTS` keeps it idempotent — safe
  to run twice — the same discipline every ETL script in this project follows.
- **Backfill vs. schema change**: two separate steps that people conflate. Adding the *column*
  (a schema change) leaves every existing row NULL. The values only appear after you re-run the
  load that *writes* them (the backfill). Task 36 was the schema+code; Task 37 was the backfill.
- **Graceful degradation**: the templates guard every image with `{% if movie.poster_path %}`, so
  before the column existed the attribute resolved to empty and the page just skipped the image
  instead of crashing. This is why Workstream C (frontend) could safely ship *before* the data
  existed.
- **Normalising sentinel values**: TMDB returns `""` (empty string), not `null`, for a missing
  poster. `raw.get("poster_path") or None` collapses both the missing key and the empty string to
  a real `None`, so the database stores NULL rather than a meaningless empty string.

### Key Code
`etl/silver/transform_movies.py` — `_flatten_movie()`:
> Added three keys (`tagline`, `poster_path`, `backdrop_path`), each wrapped in `... or None`.
> This is the single point where the raw API shape becomes our clean row shape — the fields were
> present in `raw` all along, we just weren't copying them across.

`warehouse/ddl/04_add_image_columns.sql`:
> Five `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements. Kept in its own numbered file rather
> than editing history: `01_dimensions.sql` was also updated so a *fresh* bootstrap gets the
> columns from the start, but an already-deployed database needs the ALTER — both paths converge on
> the same final schema. This mirrors how real migration tooling (Alembic, Django migrations) keeps
> "create from scratch" and "evolve what's live" consistent.

`etl/warehouse_loader/load_dimensions.py` — `load_dim_movie()` / `load_dim_actor()`:
> Only change was appending the new column names to the `columns` list. The generic `_upsert()`
> helper builds its INSERT and its `ON CONFLICT DO UPDATE` straight from that list, so widening the
> list was all that was needed — a payoff of having written one reusable upsert instead of a
> hand-typed SQL string per table.

### What to Study Next
Real migration tools track *which* migrations have already run (a `django_migrations` table, or
Alembic's `alembic_version`) so they never apply the same ALTER twice or skip one. We fake that
here with `IF NOT EXISTS`. Read the Django migrations docs (or Alembic's autogenerate) and ask:
what does a version-tracking table buy you that `IF NOT EXISTS` doesn't — especially for a change
that *isn't* naturally idempotent, like renaming a column or backfilling a value?

## Task 38 — Frontend redesign: one design system, white + lime

### What Was Built
The site worked but looked like two different products stapled together. Home and Analytics
opted out of the global stylesheet (`{% block body_class %}`) and loaded their own dark theme
with their own fonts; the other eight pages fell back to `theoria.css` and rendered as
unstyled `<h1>Movies</h1>` with a bare `<select>`. This task replaced both with **one**
design system — plain white paper, one lime green — applied to all ten pages.

The organising idea: Theoria is a film *archive with a measuring instrument pointed at it*,
not a cinema. So the vocabulary is contact sheets, catalog cards, accession numbers and
shelf labels. Lime is reserved as the **measurement mark** — it appears on a meter bar, a
keyed poster, the active nav item, and nowhere else. The home hero is the whole catalog
rendered as a contact sheet with the top-rated films keyed in lime, the way an archivist
marks a strip.

Deleted along the way: drifting film grain, a flickering lamp glow, a blinking reel dot and
a 48-second infinite marquee. Ambient motion reads as noise; the redesign spends its whole
motion budget on one page-load moment.

### Concepts Used
- **Design tokens**: every colour, type size, space step and duration is a CSS custom
  property on `:root`. Nothing is hardcoded at a use site, so a palette change is one edit.
- **Contrast ratios as a hard constraint, not a taste call**: each ink/lime pair was
  *computed* against white before use (`--ink` 19.7:1, `--lime-text` 5.0:1, `--lime-mark`
  3.1:1). `--lime` itself is only 1.5:1 — far below the 3:1 floor for a chart mark — so it
  is used as a *fill behind a printed number*, never as a mark on its own. The printed value
  is the "relief channel" that makes the pale bar legal.
- **Sequential vs categorical colour**: each chart carries one series, so it takes one hue,
  not a multi-colour palette, and needs no legend — the heading names it.
- **CSS specificity discipline**: page stylesheets may only *add* components, never restyle
  a shared one. This is what stops section margins cancelling each other out.
- **Progressive enhancement**: meters and count-ups are drawn by JS over markup that is
  already correct and readable without it.
- **Single source of truth across languages**: `analytics.js` reads its palette out of the
  CSS custom properties instead of duplicating five hex values in JavaScript.

### Key Code
`django_app/static/css/theoria.css` — the token block and `td[data-meter] .meter-track`:
> The meter is a `.meter-track` wrapper with a `.meter-fill` inside it. The wrapper reserves
> the right-hand strip where the number sits, so the fill's percentage width is relative to
> the *bar's* space rather than the whole cell — a 100% bar lands exactly at the track edge
> and can never run under its own digits. The fill grows leftward from the number, so the
> **left** edge is the one that moves with the value; that edge carries the lime rule. (An
> earlier version marked the right edge, which put every bar's rule in the same place and
> therefore encoded nothing.)

`django_app/templates/base.html` — the active-nav block:
> Uses `request.resolver_match.url_name`, not `request.path`. Path matching breaks on home
> (`/` is a prefix of every route), and matching the full `view_name` would drop the
> highlight the moment you opened a detail page. Grouping the list and detail names —
> `{% if vn in "movie_list,movie_detail" %}` — keeps "Films" lit on `/movies/120/`.

`django_app/movies/views.py` — `movie_detail()`:
> `fact_casting` stores one row per *(actor, director)* pair, so a film with two credited
> directors listed every actor twice on the page. `.distinct()` can't fix this — the rows
> genuinely differ by `director_id` — so the view orders by billing and keeps the first
> credit per actor. This is the same cross-join whose ~46% reject rate Task 35 will remove
> at the schema level; this is the display-side workaround until then.

### What to Study Next
The cast duplication above is a symptom of a modelling decision, not a UI bug: a bridge
table that cross-joins two dimensions can't represent "this actor was in this film" without
also asserting a director. Read up on **factless fact tables** and on why a many-to-many
bridge usually gets its own grain (one row per credit) rather than a composite of two roles.
Then look at Task 35's plan to split `fact_casting` into `fact_cast` + `fact_crew` and ask:
which queries get *simpler*, and which need a join they didn't need before?

## Task 35 — Split `fact_casting` into `fact_cast` + `fact_crew`

### What Was Built
Investigated a bug report ("movies are missing their Cast section") and traced it to the
warehouse schema, not a display bug: 58 of 112 movies (52%) had zero cast rows in the
warehouse. Fixed it by replacing the single `fact_casting` fact table with two independent
ones, `fact_cast` and `fact_crew`, and re-ran the loader against the live database — cast
coverage went from 54 to 99 of 112 movies, with the loader's reject count for cast rows
dropping from 1,714 to 0.

### Concepts Used
- **Grain of a fact table**: the grain is "what does one row mean." `fact_casting`'s grain
  was *(movie, actor, director)* — a triple, not a credit. That single decision is what
  forced a cross-join to populate it, since TMDB never hands you that triple directly.
- **Cross-join as an anti-pattern for independent facts**: joining two unrelated lists
  (cast, crew) to satisfy one table's NOT NULL constraints makes each list's presence
  depend on the other's, even though "this actor was in this film" and "this person
  directed this film" are true independently of each other.
- **Quarantine, not drop**: rejected rows (here, cast rows that couldn't find a director)
  were never silently discarded — they were written to `data_quality/rejected/` with a
  `rejection_reason`. That's *why* this bug was diagnosable at all: the reject file's 1,714
  rows all sharing one reason (`"no director for movie"`) pointed straight at the cause.
- **Defense-in-depth data quality checks**: `data_quality/warehouse_checks.py`'s FK checks
  and fact-load-sanity checks are redundant with the database's own FOREIGN KEY constraints
  and NOT NULL rules — they exist to catch corruption from *outside* the normal loaders
  (bad backups, manual edits), not to replace the constraints.
- **Idempotent re-migration**: because every load in this project is an `INSERT ... ON
  CONFLICT DO UPDATE` upsert, re-running `load_facts()` against the same Silver partition
  after a schema change was safe to do live, with no need to re-run Bronze/Silver ingestion.

### Key Code
`etl/warehouse_loader/load_facts.py` — `_build_cast_rows()` / `_build_crew_rows()`:
> The old `_build_casting_rows()` grouped bridge rows by movie, then nested a loop over
> directors inside a loop over actors — that nested loop *was* the cross-join, and the
> `if directors.empty: reject every actor row` branch right before it was the bug. The new
> functions each iterate their own bridge subset (`credit_type == "cast"` /
> `credit_type == "crew" and role == "Director"`) and validate only the one dimension they
> care about. Neither function's signature even takes the other dimension's valid-ID set
> anymore — the independence is enforced by the function signature, not just by the logic
> inside it.

`data_quality/warehouse_checks.py` — `check_fact_load_sanity()`:
> Previously this function had one check block for `fact_casting`, compared against the
> *whole* Silver credits_bridge count. After the split it needs the bridge count broken
> into a cast subset and a director-crew subset (`run_warehouse_checks()` computes both by
> filtering the same DataFrame twice), so that `fact_cast` having zero rows and `fact_crew`
> having zero rows are two separate, independently-triggerable failures — mirroring the
> production fix at the data-quality layer, not just the loader.

### What to Study Next
While verifying this fix I found a second, unrelated bug in the same neighborhood:
`etl/silver/transform_credits_bridge.py` deduplicates crew rows on
`(movie_id, person_id, credit_type)`, but `credit_type` is only ever `"cast"` or `"crew"` —
never per-job. A person credited with two crew jobs on one movie (very common for
directors, who often also write or produce) collapses to a single row during
`drop_duplicates()`, silently losing whichever job title didn't survive — including
"Director" itself. This is why big, well-known films like *The Lord of the Rings* and
*The Dark Knight* now show a full cast but no "Directed by" line. Study **surrogate vs.
natural dedup keys**: what would the correct uniqueness key be here (hint: it needs `role`
or `job` in it, not just `person_id` + a coarse `credit_type`), and what would break if you
added it — would any *legitimate* duplicate you're currently relying on stop being
collapsed?

## Task 39 — Backfill missing poster/backdrop/headshot images

### What Was Built
Investigated a second bug report from the same conversation ("movies/actors/directors are
missing pictures") and found it was a completely different kind of problem than Task 35's
Cast bug, even though the symptom ("some record has a gap") looked similar. No code was
wrong here — the fix was re-running two already-correct Silver transforms against an
already-immutable Bronze partition, then re-upserting dimensions from the result.

### Concepts Used
- **Schema drift across partitions**: Task 36 added `poster_path`/`backdrop_path`/
  `tagline`/`profile_path` to the transform code and the warehouse DDL, but the *existing*
  Silver Parquet for the `2026-07-06` partition had already been generated by the *old*
  code, before those columns existed anywhere. Code changes don't retroactively apply to
  data already sitting in a data lake — only re-running the transform does.
- **Partition coverage gaps**: `dim_movie`/`dim_actor`/`dim_director` accumulate via
  upsert across every partition ever loaded, but only for the rows *present in that
  partition*. TMDB's "popular movies" list is a live, shifting ranking — the 100 movies
  returned on `2026-07-06` and the 100 returned on `2026-07-09` overlapped but weren't
  identical. A movie/person unique to the older partition only ever gets the columns that
  existed in the schema *at the time that partition was last loaded*, unless that
  partition is reprocessed.
- **Verify against the immutable source before assuming a data gap**: before writing any
  fix, the raw Bronze JSON was read directly for a "missing poster" movie (id 122, LOTR:
  Return of the King) to check whether TMDB itself lacked the image, or whether it was
  present in Bronze and lost somewhere downstream. It was present — that one check ruled
  out "this is just sparse TMDB data" and pointed at the pipeline instead, before any code
  was touched.
- **Idempotent Silver rebuild as a safe remediation**: because Bronze is immutable and
  Silver is documented as "rebuilt from source, never hand-edited," re-running
  `transform_movies()`/`transform_people()` for an old `ingestion_date` is a sanctioned,
  ordinary operation — not a special-case backfill script. The same is true of
  `load_dimensions()`'s upsert on the warehouse side.

### Key Code
`etl/silver/transform_movies.py` / `transform_people.py` — no changes made this task, which
is itself the lesson:
> Both functions take `ingestion_date` as a plain parameter and always re-derive their
> output entirely from Bronze. That design is what made this fixable with zero new code —
> the fix was *calling* `transform_movies(ingestion_date=dt.date(2026, 7, 6))` again, not
> patching anything. A transform that reads from a partition-scoped, immutable source and
> writes idempotently is trivially safe to replay after the code that produces it changes.

### What to Study Next
Both this task and Task 35 trace back to the same underlying property: a star schema's
dimension tables are a *rolling accumulation* over many partitions, not one big batch load.
That means "does this record have complete data" depends on *which partition last touched
it*, not just on today's code being correct. Read up on **slowly changing dimensions
(SCD)** — in particular, what a "Type 1" SCD update (overwrite in place, no history) means
for a column added after some rows already exist, and how a data warehouse would normally
detect and re-backfill "this dimension row predates this column" systematically, rather
than an engineer noticing it by inspecting a rendered web page.


## Task 40 — Fix the crew dedup key so directors survive Silver

### What Was Built
No new feature — a one-line fix to a *silent* data-loss bug, plus the DQ check and the Gold
aggregation that were both quietly agreeing with it.

The symptom: only 47 of 112 movies in the warehouse had a director. *The Dark Knight* and
*LOTR: The Fellowship of the Ring* both rendered with no "Directed by" line, and the
analytics dashboard's "Top Rated Directors" panel was permanently empty — its query needs
directors with 3+ films, and **zero** directors qualified.

The cause was in `transform_credits_bridge.py`, which deduplicated crew rows on
`(movie_id, person_id, credit_type)`. But `credit_type` is only ever the literal string
`"crew"` — it does *not* distinguish jobs. So for anyone credited with more than one job on
the same film, all their rows collapsed to a single row, `keep="last"` picking whichever
one happened to sort last. Directors very often also produce or write their own films: I
measured **65 of 99 movies** where the director held a second job. In those cases the
"Director" row was thrown away and a "Producer" row survived in its place.

Bronze — immutable, and therefore still holding the truth — had a `job == "Director"` crew
member for **99 out of 99** movies the whole time. The data was never missing; the
transform was deleting it on every run.

### Concepts Used
- **Grain**: the level of detail one row represents. The real grain of a credit is
  *(movie, person, credit type, job)* — "Nolan directed film X" and "Nolan produced film X"
  are two distinct facts. The dedup key claimed the grain was coarser than it is, so pandas
  did exactly what it was told and discarded a real fact as a "duplicate".
- **A dedup key is a declaration, not a filter.** `drop_duplicates(subset=...)` is you
  asserting "these columns uniquely identify a row." If that assertion is wrong, you get no
  error — you get silent, permanent data loss that looks like clean data downstream.
- **Self-consistently wrong data passes every test.** All 174 tests passed. All 20 Silver
  DQ checks passed. All 22 warehouse checks passed. FK integrity passed. Nothing was
  *corrupt* — there were simply fewer rows than there should have been, and no check
  compared the count against the source.
- **Idempotent replay from an immutable source**: fixing the code was enough, because
  Silver is always rebuilt from Bronze. No data was recovered from a backup; it was
  recomputed.

### Key Code
`etl/silver/transform_credits_bridge.py` — the dedup call:
> ```python
> df = df.drop_duplicates(
>     subset=["movie_id", "person_id", "credit_type", "role"], keep="last"
> )
> ```
> Adding `role` (which holds the job title for crew rows) makes the key match the true
> grain. One person can now legitimately hold several crew credits on one film.

`data_quality/silver_checks.py` — `ENTITY_CONFIGS["credits_bridge"]["pk_cols"]`:
> This had to change in the same commit, and *why* is the interesting part. The duplicates
> check validated uniqueness on the **same wrong key**. It wasn't an independent check that
> failed to catch the bug — it encoded the identical false assumption, so it confirmed the
> bug looked correct. Had it been left alone, it would have started failing on every
> legitimate multi-job row after the fix. A DQ check that shares its premise with the code
> it checks cannot catch an error in that premise.

`etl/gold/build_gold_datasets.py` — `_build_director_ratings()`:
> Filtered `credit_type == "crew"` but never `role == "Director"`, while
> `load_facts._build_crew_rows()` filtered on both. Two layers, two different definitions of
> "a director credit", drifting apart unnoticed because nothing compared them. Added the
> missing filter so Gold and the warehouse now agree.

### Results
Rebuilt Silver from Bronze and reloaded facts for both partitions:
- `fact_crew`: 54 → **128 rows**
- Movies with a director: 47/112 → **111/112** (the one remaining, *A Lustful Night*,
  genuinely has no director in Bronze — real source sparsity, not a bug)
- Directors with 3+ films: 0 → **1**, so "Top Rated Directors" renders for the first time
  (Christopher Nolan, 4 films, avg 8.22)
- 0 rejected rows; Silver DQ 20/20, warehouse 22/22, tests 176/176

### What to Study Next
This bug was invisible to every automated check because all of them validated *internal
consistency* and none validated *conservation* — that what came out matches what went in.
Read about **reconciliation / row-count controls** in ETL: the practice of asserting
`count(distinct movie_id with a director in Bronze) == count(in fact_crew)` across a layer
boundary. `warehouse_checks.py` already has the right instinct with its `bronze_to_silver`
row-count checks, but they only compare *totals per entity*, which is why 9,282 crew rows
in and 9,282 out looked perfect while the wrong 52 directors were among them. Next question
to explore: what would a check look like that catches "the right number of rows, but the
wrong ones"?

## Task 41 — Put the score and the synopsis on the movie page

### What Was Built
The movie detail page now shows a rating, a vote count, and a plot synopsis. Before this,
a film's page listed Released / Runtime / Language / Budget / Revenue — and never told you
whether the film was any good or what it was about. For a "mini IMDb", that's the two most
important things on the page.

Neither required a single new API call. Both values had been flowing through the pipeline
for months:

- `vote_average` / `vote_count` were already loaded into `fact_movie_metrics`. The view
  simply never queried them.
- `overview` was extracted in `transform_movies._flatten_movie()` and written to the Silver
  Parquet (99/99 non-null), listed in `silver_checks` expected columns — and then dropped,
  because `dim_movie` had no `overview` column, so `load_dim_movie()`'s explicit column
  list couldn't carry it.

That second one is a good illustration of a rule cutting both ways. "Never `SELECT *`" and
"name columns explicitly" prevent accidental schema coupling, but they also mean a column
can exist in every lake layer and still never reach the warehouse, silently, with no error
anywhere — because *not selecting a column is indistinguishable from not having one*.

### Concepts Used
- **Additive schema migration**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on a live table.
  Adding a nullable column is a backwards-compatible change — existing rows get `NULL`,
  existing queries are unaffected, and no downtime or table rewrite is needed.
- **Dual-write the DDL**: the column goes in `01_dimensions.sql` (so a fresh bootstrap
  builds it correctly) *and* in a new numbered migration `06_add_overview.sql` (so the
  already-live database gets it). Same pattern as `04_add_image_columns.sql`.
- **Backfill by replay, not by patch**: no `UPDATE` statement was written. Re-running
  `load_dimensions()` for each existing partition re-upserted every movie with the new
  column populated from Silver, which already had the data.
- **Fact grain vs. display grain**: covered below — the most subtle part of this task.

### Key Code
`django_app/movies/views.py` — `movie_detail()`:
> ```python
> metrics = (
>     MovieMetrics.objects.using("warehouse")
>     .filter(movie_id=movie_id)
>     .values("rating", "vote_count")
>     .distinct()
>     .first()
> )
> ```
> `fact_movie_metrics` is at `(movie_id, date_id, genre_id)` grain, so a three-genre film
> has *three* rows — each carrying the same rating. The instinct is `.aggregate(Avg(...))`,
> and it would even print the right number, because averaging identical values returns that
> value. But it would be right by accident: rating is a **movie-level** measure that has
> been fanned out by the genre dimension, not a set of measurements to average. Taking one
> row via `.values(...).distinct().first()` says what's actually true. This is the same
> collapse the Task 22 analytics SQL does with `SELECT DISTINCT movie_id, rating` in a CTE.

`etl/warehouse_loader/load_dimensions.py` — the `dim_movie` column list:
> Adding `"overview"` to this list is the entire loader change. The `_upsert()` helper
> builds `INSERT ... ON CONFLICT (movie_id) DO UPDATE` from the list, so the new column is
> inserted for new rows *and* updated for existing ones — which is what makes the backfill
> a re-run rather than a migration script.

### Results
- `dim_movie.overview` populated for **109/112** movies (the 3 blanks are genuinely empty
  in TMDB, verified against Bronze).
- `/movies/155/` (The Dark Knight) renders its synopsis, `Rating 8.5 / 10`, `36,040 votes`,
  and `Directed by Christopher Nolan` — all four fields were absent before Tasks 40–41.
- Tests 177/177. One existing loader test had to change: it asserted the exact set of
  upserted `dim_movie` columns, and its fixture *already contained* `overview` — the test
  was pinning the bug in place.

### What to Study Next
Two of the last three tasks (36, 41) were the same shape: a field present in Bronze and
Silver but missing from the warehouse, found by a human looking at a rendered page. That's
a **schema drift** detection gap. Look into how tools like dbt or Great Expectations handle
this — specifically, a test that asserts *every column in the Silver contract has a
corresponding warehouse column*, so the pipeline itself complains when a transform starts
producing a field no loader consumes. Then ask the harder version: should an unconsumed
Silver column be an error, or is it legitimate for the lake to carry more than the
warehouse needs?

## Task 42 — `discover/movie`: designing the corpus instead of accepting one

### What Was Built
A second Bronze ingestion source. Until now the entire warehouse was built from TMDB's
`movie/popular` endpoint, which returns whatever is popular *at the moment you call it*.
That produced a catalogue of 112 films, **77 of them from the 2020s** and exactly one from
before 1960. Every "trend over time" analytic was therefore describing a corpus that barely
had a time axis.

`etl/bronze/ingest_discover.py` uses TMDB's `discover/movie` endpoint instead, which accepts
filters. Iterating one release year at a time and asking for the most-voted films of that
year, with a vote-count floor, turns ingestion into a **deliberate sampling decision**:
"the 20 most-voted films of each year from 1970 to 2026, with at least 300 votes."

Result: 1,140 films discovered, evenly spread at roughly 200 per decade.

### Concepts Used
- **Corpus / population design.** The most important idea in this task. No transform, join
  or aggregate can recover information the extraction step never collected. A perfectly
  correct pipeline over a biased sample produces perfectly correct, biased answers.
  `movie/popular` doesn't sample the population "films" — it samples "films trending today",
  and no amount of downstream SQL fixes that.
- **Windowed / partitioned extraction.** APIs cap how deep you can page, so you cannot pull
  5,000 records from one query. You partition the *request predicate* (here, by release
  year) and page within each window. This is the same technique as partitioning a large
  database read by key range — the constraint is the API's, but the pattern is general.
- **Fail-and-continue with early exit.** Each page is flushed to S3 before the next request,
  so a failure in year 1994 never loses 1970–1993. And when a year reports `total_pages: 1`,
  the loop breaks instead of requesting empty pages — cheap, but it saves one wasted HTTP
  call per sparse year.
- **Deduplication at the source.** A film can be returned under more than one year window,
  so ids are deduplicated *while preserving discovery order* using a `set` for membership
  plus a `list` for order, rather than `list(set(...))`, which would scramble it.
- **Additive, not replacing.** `ingest_movies()` was left completely intact and remains the
  default. `--source discover` selects between them, and everything downstream —
  detail/credits ingestion, all four Silver transforms, Gold, the loaders — is byte-identical
  in both paths, because both sources return the same thing: a plain `list[int]` of ids.

### Key Code
`etl/bronze/ingest_discover.py` — the year loop:
> ```python
> for year in range(start_year, end_year + 1):
>     for page in range(1, pages_per_year + 1):
>         payload = client.discover_movies(page=page, release_year=year, min_votes=min_votes)
>         ...
>         if not results or page >= payload.get("total_pages", page):
>             break
> ```
> The nested loop *is* the windowing strategy. The outer loop moves the filter; the inner one
> pages within it. This is what makes an unbounded catalogue reachable through a paginated API.

`config.py` — the `DISCOVER_*` block:
> These four values are not tuning knobs like `MAX_PAGES`; they are a **definition of the
> dataset**. Changing `DISCOVER_MIN_VOTES` from 300 to 50 doesn't make the pipeline slower,
> it makes the warehouse describe a different population — more obscure films, lower average
> ratings, different genre mix. Worth being conscious that a config value can silently be a
> research decision.

### Results (live run, 30.6 minutes end to end)
| | before | after |
|---|---|---|
| films | 112 | **1,215** |
| actors | 3,548 | **44,554** |
| directors | 120 | **677** |
| cast credits | 3,345 | **62,713** |
| films from the 2020s | 77 of 112 (69%) | 195 of 1,215 (16%) |
| directors with 3+ films | 1 | **146** |

Bronze: 1,140/1,140 details and 1,140/1,140 credits written, **0 failures**. Silver:
231,728 credit-bridge rows, 0 parse errors. Silver DQ 20/20, warehouse checks 22/22.
"Top Rated Directors" went from a single row to a real leaderboard — Miyazaki, Kubrick,
Tarantino, Kurosawa.

### A scaling problem the new corpus exposed
`director_trend_over_time.sql` and `genre_growth_over_time.sql` had **no `LIMIT`**. At 112
films nobody noticed; at 1,215 they returned 1,304 and 791 rows into fixed-height dashboard
panels. Both were capped — and `director_trend` also now requires `>= 3` films, so the panel
shows careers rather than one-off credits. The lesson: *a query with no bound isn't "simple",
it's a query whose result size is controlled by your data volume instead of by you.*

### What to Study Next
The run took 30 minutes, almost entirely in two sequential loops issuing 2,280 HTTP requests
one at a time. Look into (a) TMDB's `append_to_response`, which can return details *and*
credits in a single request — halving the call count with no logic change — and (b) why
issuing independent I/O-bound requests concurrently (a thread pool, or `asyncio` + `aiohttp`)
is the standard fix, and what rate limiting you must add so concurrency doesn't just get you
throttled. Then the deeper question: at what data volume does the pandas-in-one-process
Silver step (which currently downloads all 1,140 objects serially and holds every row in
memory) stop being viable, and what changes first — the download, the memory, or the CPU?

## Task 44 — Verifying the phase, and the docs that were quietly wrong

### What Was Built
No new features — the closing task of Phase 8: prove the numbers that motivated the phase
actually moved, then correct documentation that had drifted away from the code.

**Verification at the new scale.** Every route (11 URLs plus static assets) returns 200
against the 1,215-film warehouse; `/movies/238/` (The Godfather) renders rating 8.7/10,
23,226 votes, a synopsis, "Directed by Francis Ford Coppola", and a cast including Brando
and Pacino — a page that before Phase 8 would have shown no rating, no synopsis, and quite
possibly no director. Silver DQ 20/20, warehouse checks 22/22, 182/182 tests, and a bad id
still 404s cleanly.

**Documentation fixes.** Three real defects, all found by reading rather than by any tool:
1. `README.md`'s schema-setup step listed only DDL files `01`–`03`. Files `04`–`06` exist and
   add the image columns, split `fact_casting`, and add `overview` — so *anyone following the
   README on a fresh machine got an incomplete warehouse.* This is the worst kind of doc bug:
   it doesn't fail loudly, it produces a subtly wrong system.
2. The test count was stale in three places (README, `architecture.md` §7, `CLAUDE.md`).
3. `architecture.md` had no account of the corpus-source decision or the dedup-grain bug —
   both of which are the most interesting things in this phase.

### Concepts Used
- **Verification vs. testing.** The 182 unit tests all passed *before* Phase 8 began, while
  half the films were missing their director. Tests check that code does what it says; only
  verification against live data checks that what it says is what you wanted. Both Phase 8
  data bugs were invisible to the suite by construction.
- **Documentation as part of the system.** A README that produces a broken install is a
  broken build script written in English. Worth treating with the same seriousness.
- **Reconciliation controls** (written up in `architecture.md` §3): asserting a *conserved
  quantity* across a layer boundary — "the number of films with a director in Bronze must
  equal the number in `fact_crew`" — rather than only checking internal consistency. This is
  the check family that would have caught the Task 40 bug on the day it was introduced.

### Phase 8 in numbers

| | before | after |
|---|---|---|
| films | 112 | **1,215** |
| actors | 3,548 | **44,554** |
| directors | 120 | **677** |
| cast credits | 3,345 | **62,713** |
| films with a director | 47 (42%) | **1,214 (99.9%)** |
| directors with 3+ films | 0 | **146** |
| films from the 2020s | 69% | 16% |
| decades covered meaningfully | 1 | **6** |
| empty dashboard panels | 1 | **0** |
| tests | 174 | **182** |

### What to Study Next
Phase 8 found two bugs of the same species: data that existed upstream but silently failed
to arrive downstream (`overview` never loaded; director credits deduplicated away). Neither
was caught by tests, DQ checks, or FK constraints, because all of those verify *internal
consistency* and both bugs were internally consistent.

The concrete follow-up is to add a **reconciliation check** to `warehouse_checks.py`: pick a
quantity that must be conserved across a boundary (films-with-a-director, films-with-a-
synopsis, distinct people credited) and assert Bronze == warehouse, failing loudly on drift.
Then read about **data contracts** and dbt's `relationships` / `not_null` / custom singular
tests — the broader idea being that the schema of what a layer *promises to deliver* should
itself be a checked artifact, not folklore.

## Task 45 — Person-page stat row and career-period fixes

### What Was Built
Three small, user-driven frontend fixes on the actor/director pages: (1) the Films/Avg
rating/Active stat row no longer looks like a boxed table — the vertical and horizontal
ruled dividers are gone, replaced by a tight cluster of stats each carrying a small lime
"measured" tick; (2) the small "eyebrow" kicker line above every page title (`INDEX Actors`,
`FILM CATALOG · MEASURED`, `dim_actor · 880`) was removed site-wide, after discovering that
on the actor/director list pages it was literally duplicating the title text right below it;
(3) the "Active" stat no longer shows a nonsensical closed range like "2026–2026" for a
director whose only known film releases this year.

### Concepts Used
- **CSS specificity and dead code.** Removing a border isn't just deleting one rule — a
  `border-right` on every cell but the last (`.stat:last-child { border-right: none; }`) is a
  small system that has to be removed as a unit, or the spacing logic it was compensating for
  (a mobile media-query override) becomes orphaned and wrong.
- **Deriving display strings in the view, not the template.** Django templates can do simple
  conditionals, but "is this range still open, and does that change three different labels"
  is exactly the kind of branching logic that belongs in a small, independently testable
  Python function (`_career_period()`), not spread across `{% if %}` tags.
- **Regression via a shared partial.** `_person_header.html` is included by both
  `actor_detail.html` and `director_detail.html`, so one CSS/logic fix instantly applies to
  both pages — the payoff of Task 34's decision to de-duplicate those templates.

### Key Code
`django_app/movies/views.py` — `_career_period(start, end)`:
> Takes the earliest and latest release dates in a person's filmography and returns a
> display string. A single-year career collapses to one bare year instead of repeating it
> ("2015" not "2015–2015"), and a career whose latest film is this year or later renders as
> `"<start>–Active"` — because a range that ends in the current year isn't a closed fact yet,
> it's a career that's still going. This is the same category of bug as Task 40/41: a value
> was technically correct (2026 really is both the min and max release year) but *misleading*
> because the code didn't model what the number meant to a reader.

`django_app/static/css/theoria.css` — `.stat .stat-value::before`:
> A 22px lime bar drawn above each figure via a `::before` pseudo-element, reusing the exact
> visual language already established for the active nav link's underline
> (`.nav-links a[aria-current="page"]::after`) and the meter fills elsewhere in the design
> system. Reusing an existing signature element instead of inventing new decoration is what
> keeps a ten-page site reading as one system rather than one page redesigned in isolation.

### What to Study Next
Look at how many other places in this codebase encode "is this still ongoing" as a raw
date comparison scattered inline vs. centralized in one function — `_career_period()` is a
small example of the general pattern of pulling "what does this data *mean* to a viewer"
logic out of templates and into testable Python.

## Task 46 — URL slugs for movies, actors, and directors

### What Was Built
Every movie, actor, and director page was addressed by its raw warehouse primary key
(`/actors/880/`, `/movies/238/`) — a database implementation detail leaking straight into
the URL bar. Added a `slug` column to `dim_movie`/`dim_actor`/`dim_director`, populated it
for the entire live catalog (1,215 movies, 44,554 actors, 677 directors), and switched all
three detail routes to be addressed by that slug instead (`/actors/tom-holland/`). The old
numeric URLs now 404.

### Concepts Used
- **Surrogate key vs. natural/display key.** `movie_id`/`actor_id`/`director_id` remain the
  real primary keys everywhere in the warehouse (facts still join on them) — `slug` is a
  second, URL-facing key derived from the name, not a replacement for the PK. This is the
  same distinction as a product's SKU vs. its user-facing product-page URL.
- **Deterministic collision resolution.** With 44,554 actors, name collisions ("John Smith")
  are certain. The fix sorts by `actor_id` ascending and numbers repeats in that fixed order
  (`john-smith`, `john-smith-2`, ...) — deterministic means the *same* row gets the *same*
  slug every time the function runs, which matters because a slug that could silently change
  on a rerun would break every bookmark and inbound link pointing at it.
- **Idempotent recomputation over incremental patching.** `assign_slugs()` re-derives every
  row's slug from a fresh `SELECT ... ORDER BY id` over the *whole* table on every call,
  rather than only slugifying the newly-upserted partition. That's the only way to guarantee
  global uniqueness: a collision-check scoped to just the current batch would miss a name
  that collides with someone loaded in an earlier partition, and the unique index would then
  reject the load. At this table size (tens of thousands of rows) recomputing everything
  every run costs about a second — cheap enough that correctness didn't need to be traded
  for incremental cleverness.
- **Unicode normalization.** `unicodedata.normalize("NFKD", name)` decomposes an accented
  character like "ë" into its base letter plus a separate combining accent mark, so encoding
  to ASCII with `errors="ignore"` drops only the accent and keeps the letter — "Zoë Kravitz"
  becomes `zoe-kravitz`, not `zo-kravitz`.

### Key Code
`etl/warehouse_loader/load_dimensions.py` — `assign_slugs(session, table, id_col, name_col)`:
> Reads every `(id, name)` pair from the table ordered by id, walks them in that order
> building a `seen` dict of `base_slug -> count`, and writes back one `slug` per row via a
> single batched `UPDATE ... WHERE id = :id`. The ordering is the whole mechanism: because
> it's always the same or a superset of the ids seen last time (new rows only ever add larger
> ids), the numbering assigned to every existing id never changes across reruns.

`etl/warehouse_loader/load_dimensions.py` — `_slugify(name)`:
> `unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")` before the
> regex hyphenation — written in that order specifically so accented letters survive as their
> plain-ASCII form instead of being silently deleted by the `[^a-z0-9]+` regex that runs next.

### What to Study Next
This is a hand-rolled version of what libraries like `django-autoslug` or a dedicated
`slugify` package (e.g. `python-slugify`) do out of the box, including their own collision
strategies. Worth comparing this project's "recompute the whole table" approach against
their typical "check-then-append-a-random-suffix-on-conflict" approach, and think about why
a read-mostly analytics warehouse (bulk loads, no concurrent writers) can afford the simpler,
fully-deterministic strategy that a live multi-writer web app generally can't.

---

## Task 47 — Silver: every person, every department, every collection

### What Was Built
Three changes to the Silver transforms, all reading data that was already sitting in
Bronze — zero new TMDB calls.

1. **One `silver/people` dataset covering everybody.** Until now a person only existed in
   Silver if they were in the cast (`silver/actors`) or if their crew credit had
   `job == "Director"` (`silver/directors`). Everybody else — editors, composers,
   cinematographers, writers, production designers, stunt performers — was read out of
   Bronze, looked at, and thrown away. That's **79,523 people** and, downstream,
   **169,682 credits**. `transform_people()` now also writes `silver/people/people.parquet`
   with one row per distinct credited person, plus their `known_for_department`.
2. **`department` on the credits bridge.** The bridge already recorded *what* each crew
   member did (`role` holds the job title), but not which craft that job belongs to. TMDB
   supplies `department` on every crew object; it's now carried through, with cast rows
   normalised to `"Acting"`.
3. **Franchises reach Silver.** `belongs_to_collection` — a nested object TMDB returns on
   every movie-detail payload — was being dropped whole. It's now flattened to
   `collection_id` / `collection_name` / `collection_poster_path`. Verified live on the
   rebuilt partition: **591 of 1,140 films (51.8%) belong to one of 344 collections.**

The old `actors`/`directors` outputs are deliberately still written. They're retired in a
later task, once the warehouse and the Django app have stopped reading them.

### Concepts Used
- **Extraction-stage scoping**: a filter applied while *reading* a source is not a cleanup
  step, it's a decision about what will exist. `if member.get("job") == "Director"` was one
  line long and it defined the population of the entire warehouse. No transform,
  aggregate or query downstream could ever recover a person it excluded.
- **Identity vs. role**: the old design let the credit decide the table — you were an
  "actor" because you appeared in the cast array. But a person is one person; what they did
  on a given film is a *fact*, not an attribute of who they are. Splitting those apart is
  what makes one `dim_person` possible in the next task.
- **Functional dependency and dedup keys**: `department` was deliberately *not* added to
  the bridge's dedup key. TMDB assigns each job to exactly one department, so department is
  functionally determined by job — adding it would widen the key without changing the grain.
- **Flattening a nested source field**: Silver is a flat, typed table, so a nested object
  gets flattened at the transform, not stored as a struct and unpacked later.
- **Rebuild-from-immutable-Bronze**: none of this needed the API. Bronze has been holding
  every one of these fields since the first ingestion run; re-running the transforms is safe
  and idempotent by project convention.

### Key Code
`etl/silver/transform_people.py` — `_extract_people()` / `_person_row()`:
> ```python
> return [
>     _person_row(member)
>     for member in (*payload.get("cast", []), *payload.get("crew", []))
> ]
> ```
> The two arrays are chained and mapped through the *same* function. That's the whole fix.
> Both TMDB arrays carry identical person fields (`id`, `name`, `gender`, `popularity`,
> `profile_path`, `known_for_department`), so there was never a reason for two extractors —
> the second one existed only to carry a filter, and the filter was the bug.

`etl/silver/transform_movies.py` — `_flatten_movie()`:
> ```python
> collection = raw.get("belongs_to_collection") or {}
> ```
> The `or {}` matters: TMDB omits the key *and* returns `null` for it, on ~48% of films.
> Defaulting to an empty dict means the three `.get()` calls below produce `None` uniformly
> instead of needing a branch, so a film without a franchise is an ordinary row with null
> columns rather than a special case.

`data_quality/silver_checks.py` — the new `people` entity config:
> Written from the shape of the **TMDB credits payload**, not from what `transform_people`
> happens to emit. This is the Task 40 lesson made into a habit: the `credits_bridge` check
> back then encoded the same wrong key the transform used, so it confirmed the bug instead of
> catching it. A check that mirrors the code it checks can only ever prove the code agrees
> with itself.

### What to Study Next
Both `transform_people()` and `transform_credits_bridge()` read all 1,140 Bronze files
**sequentially**, one blocking S3 `GET` at a time — the rebuild for this task took minutes
per transform, and almost all of it was waiting on the network rather than computing.
Look into `concurrent.futures.ThreadPoolExecutor` for I/O-bound fan-out like this (a
threaded read of the same 1,140 objects finishes in well under a minute), and think about
where the natural limit is: what does S3 rate-limit on, and at what point does the
bottleneck move from network latency to the single-process pandas step that follows?

---

## Task 48 — Warehouse: `dim_person` + `fact_credit`

### What Was Built
The warehouse stopped modelling people as two disjoint kinds and started modelling them as
one kind with many credits.

- **`dim_person`** — one row per person who holds any credit. **122,685 rows**, replacing
  `dim_actor` (44,554) + `dim_director` (677), which between them could only ever describe
  45,231 people because everyone else was filtered out upstream.
- **`fact_credit`** — one row per `(movie, person, department, job)`. **237,454 rows**,
  against the 64,031 that `fact_cast` + `fact_crew` held. 13 departments, **858 distinct
  job titles**.

Both tables were loaded from the Silver output of Task 47 with no new API calls. The legacy
tables are still present and still loading — nothing reads the new ones yet, so no commit in
this phase leaves the site broken.

Concretely: *The Godfather* went from "81 cast rows and one director" to **187 credits
across 12 departments**, and the warehouse can now answer "Spielberg has made 19 films with
John Williams, 19 with Michael Kahn, and 11 with Janusz Kamiński" — a query that was
impossible to express yesterday because the data didn't exist in Postgres.

### Concepts Used
- **Choosing the grain before the first load.** `fact_credit`'s PK is
  `(movie_id, person_id, department, job)` because that is the grain TMDB actually
  publishes — a director who also wrote and produced a film is three credits. Task 40 was a
  bug precisely because a key claimed a coarser grain than the data had; here the key was
  derived from the source, not from convenience.
- **Additive migration.** New tables are created alongside the old ones and both are loaded.
  The cutover is a separate, later step. This is what makes "one task, one commit" possible
  on a schema change this large without a broken intermediate state.
- **Normalising two shapes into one.** Cast credits have no department or job in TMDB, so
  they're stored as `department='Acting', job='Actor'` with the part in `character_name`.
  Once `job` exists as a column, "what they did" is expressed identically for everyone, and
  a query no longer needs to know which table a person came from.
- **Unique indexes are checked per row, not per statement.** See below — this is the real
  lesson of the task.

### Key Code
`etl/warehouse_loader/load_facts.py` — `_build_credit_rows()`:
> Deliberately has **no job filter**. The function it replaces, `_build_crew_rows()`, ends
> with `& (bridge_df["role"] == "Director")`, and that single predicate is what discarded
> 169,682 of 170,915 crew credits at the loader. The new builder rejects a row only when its
> `movie_id` or `person_id` can't be resolved — i.e. for referential reasons, never for
> editorial ones.

`etl/warehouse_loader/load_dimensions.py` — `assign_slugs()`, the `SET slug = NULL` line:
> This was a **live bug found by running the backfill**, not by the test suite. Recomputing
> slugs over the whole table can *permute* them: a newly loaded crew member with a lower
> TMDB id takes `dee-wallace`, so the actor who held it moves to `dee-wallace-2`. The rewrite
> is a batched `executemany`, and Postgres validates the unique index **after every
> individual row** — so the row that gains the slug can be written before the row that gives
> it up, and the transient duplicate is rejected even though the final state is unique.
> Clearing the column first removes the intermediate collision (a unique index permits many
> NULLs), and both statements sit inside the caller's transaction, so no reader ever sees a
> table without slugs.
>
> Note that this defect shipped in Task 46 and had been latent ever since. It never fired
> because those tables had only ever been loaded in ways where the recomputed slugs were
> *identical* to the stored ones — the design was right about the final state and silent
> about the write order, and nothing had exercised the difference until now.

### What to Study Next
The fix above works because Postgres checks a unique **index** immediately. Postgres also
supports `DEFERRABLE INITIALLY DEFERRED` unique *constraints*, which postpone validation to
commit time and would let the permutation succeed with no clear-first step at all. Read up
on the difference between a unique index and a deferrable unique constraint, why only the
latter can be deferred, and what that costs — then decide which one a bulk-reload warehouse
should actually prefer. Related: look at how `UPDATE ... FROM (VALUES ...)` as a single
statement would also sidestep the per-row check entirely.

---

## Task 49 — Gold earns a reader: deriving `fact_collaboration`

### What Was Built
A `fact_collaboration` table: one row per pair of people who have worked together, with how
many films they share and the years they span. **193,064 edges over 12,301 people** —
11,828 pairs with 2+ films, 3,232 with 3+.

It's built in Gold from Silver, then loaded into Postgres by a new
`etl/warehouse_loader/load_gold.py`. That's the first time anything has read the Gold layer.
Gold has been written on every pipeline run since Task 14 and consumed by **nothing** —
`warehouse_checks` confirmed the files existed and were non-empty, and that was all.

What it can now answer, live from the warehouse:

| Pair | Films | Span |
|---|---|---|
| Steven Spielberg + Michael Kahn (editor) | 20 | 1977–2018 |
| Steven Spielberg + John Williams | 19 | 1975–2026 |
| Martin Scorsese + Thelma Schoonmaker (editor) | 11 | 1980–2023 |
| Tim Burton + Danny Elfman | 11 | 1988–2010 |

### Concepts Used
- **Bounding a derived dataset by meaning, not by `LIMIT`.** Pairing every credit on every
  film produces **33.1 million** edges on this corpus — and, worse, asserts that a caterer
  and a stunt double "collaborated". Restricting to *key credits* (top-10 billing + nine
  principal craft jobs) gives **181,538** for the same partition. That 180x reduction is a
  definition, not an optimisation: it decides what the table *means*. Compare Task 42, where
  two dashboard queries had no bound at all and returned whatever the corpus happened to
  contain — same lesson from the other direction.
- **Why a Gold layer exists.** The honest test for "does this belong in Gold" is: expensive
  to compute, cheap to serve, and shaped for a read the star schema can't answer directly.
  A quadratic expansion over every film is all three. The other four Gold datasets fail that
  test, which is why nothing reads them — they're cheap enough to recompute in SQL, and the
  Django views do exactly that.
- **Canonical ordering for unordered pairs.** Storing both `(a,b)` and `(b,a)` doubles the
  table and makes every query say `OR`. Storing one, with `person_a_id < person_b_id`, means
  a lookup for a person must check *both* columns — the cost moves from write to read, which
  is the right trade for a table written once per load and read on every person page.
- **Constraints belong to the table, not the writer.** That ordering is enforced by a SQL
  `CHECK`, so it holds no matter what writes to it.
- **Different layers, different failure semantics.** The Silver-sourced fact loaders
  quarantine unresolvable rows. `load_gold` doesn't: a Gold edge references people the
  pipeline just derived from the same partition, so an FK miss means the Gold build and the
  dimension load disagree with each other. That's a bug to log at ERROR, not a bad record to
  set aside.

### Key Code
`etl/gold/build_gold_datasets.py` — `_build_collaboration_edges()`:
> ```python
> people = sorted({int(p) for p in group["person_id"]})
> for pair in itertools.combinations(people, 2):
> ```
> `sorted()` is what makes the pair canonical — `combinations()` over an ascending list can
> only ever emit `(smaller, larger)`, so the `CHECK` constraint is satisfied by construction
> rather than by a comparison afterwards. `itertools.combinations` also matters: the obvious
> alternative is a self-merge of the credits frame on `movie_id`, which materialises n² rows
> per film *including* both mirror images and the self-pairs, before you filter any of it
> away. On a 51-cast film that's 2,601 rows to produce 1,275.

`warehouse/ddl/09_collaboration.sql` — the two ranked indexes:
> ```sql
> CREATE INDEX ... ON fact_collaboration (person_a_id, films_together DESC);
> CREATE INDEX ... ON fact_collaboration (person_b_id, films_together DESC);
> ```
> Two indexes, one per side, because canonical ordering means a person can appear in either
> column. Each is a composite ending in the sort column, so "this person's top collaborators"
> is an index range scan with the ordering already satisfied — no sort step at all.

### What to Study Next
`fact_collaboration` is a **derived** table: nothing outside the pipeline can tell it's stale,
and re-running Gold for an old partition happily overwrites counts computed from a newer one.
Read up on **materialized views** (`CREATE MATERIALIZED VIEW ... WITH DATA`, `REFRESH
MATERIALIZED VIEW CONCURRENTLY`) and compare: Postgres would then own the derivation and the
refresh, at the cost of the aggregation no longer being expressible in pandas or reusable
outside the database. Which is the better home for this table, and does the answer change if
the corpus grows 50x?

---

## Task 50 — Franchises: `dim_collection` and the series pages

### What Was Built
Film series became a first-class thing. TMDB returns `belongs_to_collection` on every
movie-detail payload and it had never been read at any layer — Task 47 carried it into
Silver, and this task gave it a dimension, a foreign key, and two pages.

- **`dim_collection`** — 358 franchises, all slugged.
- **`dim_movie.collection_id`** — a *nullable* FK. **613 of 1,215 films** belong to a
  series; 127 franchises have 2 or more entries in the catalog.
- **`/franchises/`** — every series ranked by entries held, reusing the same
  `table-2col` + `data-meter` share-bar pattern as the genre sheet.
- **`/franchises/<slug>/`** — the series in release order, with entries, span, average
  rating and total revenue. James Bond: **17 films, 1971–2021, $6,082,635,670.**
- **"Part of"** on the movie page, linking a film to its series.

### Concepts Used
- **When an attribute should become a dimension.** The lazy version of this is three
  columns on `dim_movie` (`collection_id`, `collection_name`, `collection_poster_path`) —
  which is exactly what Silver stores. The test for promoting it: does the thing have its
  own identity, its own attributes, and is it shared by many rows? A franchise has a name,
  artwork, a slug and a URL, and 17 Bond films would otherwise repeat that name 17 times
  with no row to hang a page off. Silver keeps it inline because Silver is a flat table
  per entity; the warehouse normalises it because the warehouse models relationships.
- **A nullable foreign key as a real statement.** Half the catalog belongs to no series.
  That is a fact about films, not missing data, and the schema says so — `NULL` here means
  "stands alone", not "not yet loaded".
- **Load order follows referential order.** `load_dim_collection()` must run before
  `load_dim_movie()`, because the FK points that way. Made explicit with a comment rather
  than left as an accident of dict ordering.
- **Deriving a dimension from a denormalised source.** Silver has one collection value per
  *movie*; the dimension needs one row per *collection*. That's a `drop_duplicates` on the
  key — the same shape as `transform_people`'s dedup, one layer further along.
- **Reusing a design component rather than inventing one.** The franchise sheet is the
  genre sheet with a different noun. `data-meter` and `initMeters()` already scale a lime
  bar to the column max client-side, so the new page needed no new CSS and no new JS.

### Key Code
`etl/warehouse_loader/load_dimensions.py` — `load_dim_collection()`:
> ```python
> named = df[df["collection_id"].notna() & df["collection_name"].notna()]
> collections = named[[...]].drop_duplicates(subset=["collection_id"], keep="last")
> ```
> Filter before dedupe, and filter on *both* id and name. A row with an id but no name
> would produce a `dim_collection` row violating `name NOT NULL`; a row with neither is the
> ordinary standalone-film case and simply contributes nothing. The dimension is the set of
> franchises that actually exist, derived from the films that reference them.

`django_app/movies/views.py` — `collection_detail()`:
> `total_revenue` is a plain `Sum` over `dim_movie` — one row per film, no fan-out. But
> `avg_rating` reads `fact_movie_metrics`, which is at `(movie, date, genre)` grain, so it
> collapses with `.values("movie_id", "rating").distinct()` **before** averaging. Two
> aggregates on the same page, only one of which needs the guard — which is the whole
> reason that grain is worth understanding rather than memorising a rule.

`django_app/movies/views.py` — `collection_list()`:
> `.annotate(movie_count=Count("movies")).filter(movie_count__gt=0)` — the `filter` after
> `annotate` becomes a `HAVING` clause, not a `WHERE`. It excludes franchises whose films
> aren't in this corpus, so the page never lists an empty series.

### What to Study Next
`.annotate()` then `.filter()` compiles to `HAVING`; `.filter()` then `.annotate()` compiles
to `WHERE` and changes *what gets counted*. Write both orderings of `collection_list`'s
query, print `str(queryset.query)` for each, and work out which one answers "franchises with
at least one film in the catalog" and which answers something subtly different. This
ordering-sensitivity is one of the most common sources of wrong numbers in Django reporting
code.

---

## Task 51 — One person, every credit, and who they keep working with

### What Was Built
The data from Tasks 47–49 finally became a page.

- **`/people/<slug>/`** — a single person page replacing the actor and director pages.
  Credits grouped by department (Acting first, then the crafts), a **"Works with"** readout
  from `fact_collaboration`, and stats that now distinguish **films** from **credits**.
- **`/people/`**, with `/actors/` and `/directors/` kept as *scopes* of it — they're now
  "people holding an Acting credit" and "people holding a Directing credit", which is a
  question about `fact_credit`, not about which table someone landed in.
- **Legacy detail URLs 301** to the person page.
- **Crew on the movie page** — *The Godfather* went from "81 cast + 1 director" to the full
  187 credits across 11 departments: Directing 8, Writing 4, Production 18, Camera 13,
  Editing 9, Sound 19, Art 7, Costume & Make-Up 8, Visual Effects 1, Lighting 4.

Thelma Schoonmaker — an editor, so invisible to this warehouse before Phase 10 — now has a
page: 11 films, ★7.82, 1980–2023, 12 credits, top collaborator Martin Scorsese.

### Concepts Used
- **Redirect by id, never by slug.** Unifying the two slug namespaces re-numbered 381 slugs
  — `/actors/tom-holland/` now 301s to `/people/tom-holland-2/`, because a crew member with
  a lower TMDB id claimed the base name. The redirect looks the legacy row up by slug, takes
  its **id**, and finds the person by that. The id is the only stable link between the two
  namespaces; a slug-to-slug redirect would have silently sent 381 URLs to the wrong person
  or to a 404.
- **301 vs 302.** Permanent, because the move is permanent — it tells caches and search
  engines to update rather than to keep asking.
- **Group in Python, not in SQL.** A `GROUP BY` returns aggregates, not the rows themselves,
  and one query per department would be an N+1 in the number of crafts a person works in.
  So: one `select_related` query, grouped into a dict on the way out.
- **Ordering a categorical axis by meaning.** Departments sort by an explicit
  `DEPARTMENT_ORDER` list, with anything unrecognised appended alphabetically. Alphabetical
  order would put Art before Directing; frequency order would reshuffle between people.
- **Films ≠ credits.** A director who also wrote and produced a film is one film and three
  credits. The header prints the second stat only when the two differ, so the distinction
  appears exactly where it's informative.
- **Paying a storage saving back on read.** `fact_collaboration` stores each pair once, so
  finding one person's collaborators means querying **both** id columns and merging. That
  cost is real and it's paid here, once per page — the deliberate other half of Task 49's
  canonical-ordering decision.

### Key Code
`django_app/movies/views.py` — `_redirect_to_person()`:
> ```python
> legacy = get_object_or_404(legacy_model.objects.using("warehouse"), slug=slug)
> person = get_object_or_404(Person.objects.using("warehouse"), pk=getattr(legacy, legacy_pk))
> return redirect("movies:person_detail", person_slug=person.slug, permanent=True)
> ```
> Two lookups where one looks sufficient. The second is the whole point: it re-derives the
> *current* slug from the id rather than assuming the old one still means the same person.

`django_app/movies/views.py` — `_top_collaborators()`:
> Queries `person_a_id=` and `person_b_id=` separately, then reads the person off the
> **opposite** side of each row before merging and sorting. Written as two queries rather
> than a `Q(...) | Q(...)`, because each row also needs a different `select_related` target
> — the interesting person is whichever one isn't you.

`django_app/movies/views.py` — `_person_queryset()`:
> ```python
> people.filter(credits__department=department).distinct()
> ```
> `.distinct()` is load-bearing: joining `dim_person` to `fact_credit` yields one row per
> matching credit, so an actor with 40 acting credits would otherwise appear 40 times in the
> index. This is the same join-fan-out family as the `fact_movie_metrics` genre problem, met
> in a new place.

### What to Study Next
`_person_queryset("Acting")` does a join + `DISTINCT` over ~63,000 credit rows to list
people. The alternative is `Person.objects.filter(person_id__in=Credit.objects.filter(...).values("person_id"))`,
which compiles to a subquery instead. Run both with `EXPLAIN ANALYZE` against the live
warehouse and compare: `DISTINCT` on a large join usually means a hash aggregate over every
matched row, while `IN (subquery)` can become a semi-join that stops at the first match per
person. Then look at `EXISTS` as a third form, and work out which one Postgres actually
prefers here and why.

---

## Task 52 — The path finder: measuring distance through the graph

### What Was Built
`/connect/` — type two names, get the shortest chain of films linking them. The signature
page of the phase, and the one thing here no other movie site does.

Measured live:

- **Tom Hanks → Thelma Schoonmaker: 2 degrees** — *Catch Me If You Can* → Leonardo DiCaprio
  → *The Wolf of Wall Street*. **30 ms.**
- **Marlon Brando → Zendaya: 3 degrees** — *Last Tango in Paris* → **Franco Arcalli, an
  editor** → *Once Upon a Time in America* → Jennifer Connelly → *Spider-Man: Homecoming*.
  That middle hop was impossible before this phase; editors didn't exist in the warehouse.
- The graph itself: **49,276 people, 23 separate pieces, 99.1% in the largest.**

Four outcomes, each a real state of the data with a designed answer: a path, the same
person twice, an unconnected pair, and a name nobody matches.

### Concepts Used
- **Two graphs, two questions.** `fact_collaboration` (Task 49) is scoped to key credits and
  answers *"who works together repeatedly"*. This one is wider — all cast plus principal
  crew — and answers *"is there any path"*. A 40th-billed extra is a real connection but not
  a working relationship, so the same edge belongs in one graph and not the other. They
  deliberately share no code.
- **Why not SQL.** A recursive CTE over this graph times out (measured, >60s): it re-expands
  the same nodes at every depth because Postgres can't memoise the visited set across
  iterations. BFS in Python is fast precisely *because* the visited set is the algorithm.
  Knowing when to pull data out of the database is as much a data-engineering skill as
  knowing how to push work into it.
- **Bidirectional search.** From a hub, a one-sided BFS reaches ~31,000 people at depth 2
  and ~41,000 at depth 3 — the frontier explodes. Searching from both ends and expanding
  whichever side is smaller turns one depth-*d* search into two of depth *d/2*, which on a
  branching factor this large is the difference between milliseconds and seconds.
- **Caching against a data version, not a clock.** The adjacency is keyed on
  `count(*) + max(ingestion_date)` from `fact_credit`. A TTL would either serve stale edges
  after a load or rebuild for nothing during a quiet afternoon; a version key rebuilds
  exactly when the data changes and never otherwise.
- **Connected components.** The graph isn't one piece, and saying so is more honest than
  pretending every query has an answer.

### Key Code
`django_app/movies/graph.py` — `find_path()`:
> ```python
> if len(forward_frontier) <= len(backward_frontier):
>     frontier, seen, other = forward_frontier, forward, backward
> else:
>     frontier, seen, other = backward_frontier, backward, forward
> ```
> The whole bidirectional trick in four lines: each round, expand the cheaper side. The
> `seen` dicts double as the parent pointers, so no separate bookkeeping is needed — and the
> moment an expansion lands on a node the *other* side already reached, the two halves are
> stitched into one chain.

`django_app/movies/graph.py` — `_build_adjacency()`:
> ```python
> edges.setdefault(other, movie_id)
> ```
> `setdefault`, not assignment: two people often share several films, and keeping the *first*
> rather than the last makes the rendered path reproducible across rebuilds. A path that
> silently cited a different film each time would look like a bug.

`django_app/movies/views.py` — `_describe_path()`:
> Collects every person id and film id from the whole chain first, then issues **two**
> queries. The obvious version walks the chain looking things up per hop — an N+1 whose N is
> the answer's length, i.e. worse precisely when the result is most interesting.

### What to Study Next
BFS finds the shortest path when every edge costs the same. But a film with 4 credits is a
much stronger connection than a blockbuster with 200, and treating them as equal is why hub
films dominate every route. Read up on **Dijkstra's algorithm** and edge weighting, then
work out what weight would express "this connection is meaningful" — 1/cast_size? shared
films? — and whether the resulting "strongest" path is more interesting to a viewer than the
shortest one. Also worth knowing: A* and why it needs a distance heuristic that a social
graph doesn't naturally have.
