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
