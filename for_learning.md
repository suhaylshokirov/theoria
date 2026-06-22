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
