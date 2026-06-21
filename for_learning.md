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
