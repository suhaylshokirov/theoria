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
Last completed task   : Task 61 — Warehouse: dim_country, dim_language and their bridges
Currently on          : Task 62 — not started (Django: provenance on the movie page, and browse by country/language — Phase 14).
Current phase         : Phase 13 (Tasks 57–60) — complete. Phase 14 (Tasks 61–63): Task 61 complete, 62–63 not started.
Blockers / open issues: **No blockers.** **The Studios pages were redesigned by user
request on 2026-08-17, ad hoc and outside the numbered task flow** (same posture as the Franchise
removal and Analytics-panel cuts logged further below) — `/studios/` was a ranked `table-2col` of
studio name + film count, the same shape Task 59 deliberately reused from the old `/genres/` page.
The user found it boring and wanted studios browsed the way people are: a logo grid with
search/sort, each studio's own page filterable the same way `/movies/` is. Rebuilt `studio_list`
and `studio_detail` on the `_person_list()`/`movie_list()` pattern (live-filter AJAX via
`data-live-filter`, `_is_ajax()` branch returning a results-only fragment) rather than inventing a
third filtering mechanism. Logo cards are a genuinely new card type — TMDB logos are landscape,
often-transparent wordmarks, so `.poster-card .logo` uses `object-fit: contain` + padding on a
3:2 frame instead of the person/movie cards' cropped 2:3 `cover`; the ~46% of studios with no logo
(Task 59's measured figure) fall back to an initial-letter monogram (`.placeholder-studio` +
`.studio-initial`) rather than a hand-drawn building/camera icon, on the reasoning that a wrong
freehand SVG is a worse failure mode than a plain letter. `studio_detail`'s header stats (Films /
Avg rating / Active / Revenue) are now computed once over the studio's *whole* filmography and
never move as the grid below is filtered or paged — the same separation `person_detail`'s stat row
already has from its own credit list. Live-verified: `/studios/` renders a 30-per-page logo grid,
search-by-name and Most-films/A–Z sort both work over AJAX and plain GET;
`/studios/warner-bros-pictures/?q=batman&sort=rating` returns exactly its 7 Batman films ranked by
rating with the header stats (128 films total) unchanged; a studio with no logo shows its initial
in the monogram tile; bad slug still 404s. Tests 238 → 243 (5 new: search/sort/invalid-sort/
AJAX-fragment coverage for `studio_list`, plus filtered-filmography and AJAX-fragment coverage for
`studio_detail`, mirroring `movie_list`'s and `person_list`'s existing test shape). No warehouse or
ETL change — this is markup/view/CSS only, reading the same `dim_company`/`bridge_movie_company`
Task 58 already populated. **Task 61 (2026-08-17) applies Phase 13's bridge pattern
a second and third time** — `dim_country`/`dim_language` (natural ISO-code primary keys, no
surrogate id, no slug) and `bridge_movie_country`/`bridge_movie_language`, loaded from the Silver
link tables Task 57 already wrote. `bridge_movie_country` carries `relation` (`origin`/
`production`) inside its own PK, carrying forward Task 57's grain decision that the two
relationships must coexist rather than overwrite each other. New `_existing_str_ids()` helper in
`etl/warehouse_loader/common.py` resolves FKs against string (non-integer) primary keys — the
first time this warehouse has needed that, since every prior dimension used a surrogate int id.
Row-count-sanity checks reuse Task 58's `nunique()`-not-`len()` pattern for both new link tables.
**Backfilled and live-verified across all three partitions**: `dim_country` 46 rows / `dim_language`
73 rows cumulative (40/70 on the `2026-07-29` partition alone, matching the Phase 12 audit table
exactly); `bridge_movie_country` 2,907 rows / `bridge_movie_language` 2,113 rows, **0 rejects on
any partition**; *The Godfather* shows agreeing origin/production country (USA) and three
languages (English/Italiano/Latin); 278 of 1,215 films have an origin/production disagreement,
matching Task 57's ~23% figure. Warehouse checks 25/25 → **35/35**. No Django/UI change — Task 62
is next and reads these tables. Tests 229 → 238.
Prior task's notes: **Task 60 (2026-08-16) closes Phase 13** — two new panels
spend `dim_company`/`bridge_movie_company` on the dashboard: `studio_output_by_decade.sql` (the
leading studio, by film count, for each decade) and `top_studios_by_revenue.sql` (revenue +
avg rating, ≥3-film floor mirroring `top_rated_directors.sql`'s). **`studio_output_by_decade`
required a real interpretation call**: the plan's literal name suggested a full (studio × decade)
crosstab, but that's either 1,383 columns or thousands of rows — neither fits the flat
ranked-table shape every other panel uses, and neither is skimmable. Interpreted instead as
"who led each decade", which stays one row per decade (~10 rows) while still exercising the
studio/decade join; the query's own comment records this as a deliberate scope narrowing, not
an oversight. `top_studios_by_revenue` got the genre-fanout guard right on both sides at once,
the way `studio_detail`'s view already had to in Task 59: `SUM(dm.revenue)` is a plain sum off
`dim_movie` (one row per film, no de-dup needed), while `rating` is de-duplicated to one row per
movie via a `movie_ratings` CTE before `AVG()` — the query's comment names the Task 59 fork
explicitly so a future reader doesn't have to rediscover why the two aggregates are asymmetric.
**A real linking bug caught before it shipped**: the first draft of the dashboard template built
each studio's link from `row.company_id`, which is syntactically slug-shaped enough to match the
`<slug:...>` URL converter but resolves to nothing (`get_object_or_404(..., slug=company_id)`
against a numeric string never matches a real text slug) — silently 404ing every link. Fixed by
adding `c.slug AS studio_slug` to the query and linking on that instead; caught by actually
clicking through a live-rendered row rather than trusting the template compiled. Both queries
timed live against the full warehouse: **8.7ms and 11.4ms** respectively, the full `/analytics/`
response **116ms** — both carry an explicit `LIMIT`/bounded-row-count by construction (Task 42's
lesson), so neither is a candidate for the kind of unbounded-query bug found there. Dashboard is
now **4 panels** (2 tables added to the existing decade/genre pair); the sheet-header sub-copy
was updated to mention studios, and the header's unused `eyebrow`/`accession` params — dead since
Task 45 removed that kicker line but never dropped from this one call site — were finally deleted
rather than left silently ignored. Live-verified: all 4 panels render with real data,
`studio_output_by_decade` shows United Artists→Paramount→Universal leading successive decades,
`top_studios_by_revenue` ranks Warner Bros. Pictures first (128 films, $45.3B, ★7.24) and every
studio link resolves 200. Tests unchanged in count (229) — the existing dashboard context test
was extended with the two new panels' fixtures and assertions rather than duplicated.
Prior task's notes: **Task 59 (2026-08-16) gave `dim_company` its pages** —
`/studios/` (ranked table, most films first) and `/studios/<slug>/` (filmography + stats), plus a
"Studios" record row on the movie page. The plan's own page-shape reference (`genre_list.html`,
reused per the Task 50 note) had gone stale by the time this task ran: the entire genre browsing
UI — `genre_list.html`, `genre_detail.html`, `/genres/`, and the Genres nav link — was removed on
2026-08-14 (see the Phase 11 status entry below) when the Analytics dashboard was cut to 2 panels.
Only the underlying CSS/JS (`.table-2col`, `[data-meter]`, `initMeters()`) survived that removal,
so **the "no new CSS or JS" instruction was honoured by reusing the surviving primitives directly**
rather than the now-deleted template file — `studio_list.html` is a fresh `<table class="table-2col">`
built from those same classes, not a copy of a file that no longer exists. `studio_detail.html`
reuses `.stats`/`.stat` (already standalone, not scoped under `.person-head`) for its Films/Avg
rating/Active/Revenue row and `_movie_card.html` for its filmography grid — no image plate for the
studio itself, since only ~45% of companies have a logo and no existing CSS component covers a
non-person image plate; adding one would have violated the same "no new CSS" instruction. New
`Company`/`MovieCompany` models (`managed = False`, `MovieCompany` explicit rather than a Django
`ManyToManyField(through=...)` — that field expects Django to own and generate the join table,
whereas `bridge_movie_company` already exists and is fully managed by `13_companies.sql`). Nav
gained a "Studios" link between People and Analytics (Films · People · Studios · Analytics — still
4 items, no crowding at the level Task 51's Actors+Directors collapse was worried about). One real
grain bug caught while writing `studio_detail`'s two aggregates: `.values("movie_id","rating").distinct()`
before averaging rating (the `fact_movie_metrics` genre-fanout guard used everywhere else in this
project) but a **plain** `Sum("revenue")` off `dim_movie` — getting that backwards (averaging off
the fanned-out fact table, or guarding the already-one-row-per-film revenue sum) was the exact
mistake the plan warned about. Live-verified: all 10 routes 200 (incl. `/studios/?page=2`), a bad
studio slug 404s; `/studios/` ranks Warner Bros. Pictures first (**128 films**, matching Task 58's
verified count exactly); `/studios/warner-bros-pictures/` shows **128 / ★7.24 / 1971–2025 /
$45,341,167,063**; `/movies/the-godfather/` links all three of its studios (Albert S. Ruddy
Productions, Alfran Productions, Paramount Pictures). Tests 225 → 229 (4 new: studios render as
links on the movie page, `studio_list` ranking/filter, `studio_detail`'s two-aggregate stats
including the grain-guard regression, and a 404 case; the 10 existing `movie_detail` tests were
also updated to mock the new `MovieCompany` query the view now makes).
Prior task's notes: **Task 58 (2026-08-16) added `dim_company` and
`bridge_movie_company`**, the warehouse's first genuine bridge table — new `warehouse/ddl/13_companies.sql`
(also folded into `01_dimensions.sql` for fresh bootstraps), applied live. Named `bridge_` rather
than `fact_` on purpose: it carries no measure, only the existence of a movie/company relationship
(a "factless fact table"), and reserving `fact_` for tables that actually sum something keeps the
schema self-describing. `load_dim_company()` in `load_dimensions.py` mirrors `load_dim_collection()`
exactly — the dimension is the *distinct* set of companies in Silver's `movie_companies` link
table, derived via `drop_duplicates`, filtered on id **and** name — then gets the same
`assign_slugs()` whole-table recompute as every other slugged dimension (the Task 48
`UniqueViolation` fix already covers it, no new code needed). `load_bridge_movie_company()` in
`load_facts.py` resolves both FKs against the live dimensions and quarantines unresolvable rows to
`data_quality/rejected/`, run after `load_dim_movie()`/`load_dim_company()` and using the same
reject-don't-drop convention as every other loader here. Two new `_FK_CHECKS` entries plus a
dedicated row-count sanity check in `warehouse_checks.py` — the bridge's Silver source is a
*link* table (one row per movie/company pair, not per company), so its silver-to-warehouse
comparison had to use `nunique(company_id)` rather than a plain row count, or a studio backing 128
films would look like the warehouse had silently dropped 127 rows. **Live-verified across all
three backfilled partitions**: `dim_company` **1,383** rows, **0** null slugs; `bridge_movie_company`
**3,409** rows, 0 rejects; Warner Bros. Pictures → **128 films**, matching the task's estimate
exactly. Warehouse checks 20/20 → **25/25** (2 new FK checks + 2 new row-count checks + 1 new
fact-load-sanity check, net +5). No Django/UI change yet — nothing renders `dim_company` or
`bridge_movie_company` until Task 59. Tests 217 → 225 (8 new: `load_dim_company`'s dedup/null-filter
behavior, `_build_bridge_company_rows`/`load_bridge_movie_company`'s FK resolution and rejects,
`load_dimensions()`/`load_facts()` integration updated for the new tables, and the companies
row-count-sanity distinct-vs-row-count regression in `test_warehouse_checks.py`).
Prior task's notes: **Task 57 (2026-08-16) added a new Silver module,
`etl/silver/transform_movie_links.py`**, extracting the three nested arrays `_flatten_movie()`
has always dropped: `production_companies`, `production_countries`/`origin_country`, and
`spoken_languages`. It does its own Bronze pass over `bronze/movie_details` (mirroring
`transform_credits_bridge.py`'s relationship to `transform_movies.py`) and writes three
denormalised long tables — `silver/movie_companies/`, `silver/movie_countries/`,
`silver/movie_languages/` — each ready to derive a dimension from via `drop_duplicates`, the
`load_dim_collection()` pattern. **The grain decision of this task is `relation` on the country
table**: `origin_country` and `production_countries` are different relationships that disagree on
~23% of films, so they're kept as separate rows tagged `relation ∈ {"origin", "production"}`
rather than merged into one row meaning two things at once — the inverse of the Task 40 mistake
(a key claiming a finer grain than the data), guarded against here by folding `relation` into the
dedup key itself. An origin row's `country_name` is backfilled from a `production_countries` row
for the same code in the same payload when one exists, else left null rather than guessed. Three
new `ENTITY_CONFIGS` entries added to `silver_checks.py`, written from the TMDB payload shape
(the Task 40 lesson: a check that mirrors the transform only confirms the transform agrees with
itself). Wired into `run_pipeline.py` after `transform_credits_bridge`. **Live-verified on
`2026-07-29`**: 3,200 company links across 1,243 distinct companies, 1,506 production + 1,211
origin country links (17 origin rows have no name-match, left null), 2,013 language links across
70 distinct codes — all matching the Phase 12 audit's figures. Silver DQ 16/16 → **28/28** (3 new
entities × 4 checks). No warehouse/Django change yet — that's Task 58. Tests 207 → 217 (9 new in
`test_etl.py`, 1 new in `test_data_quality.py`; `_all_entity_dfs()` extended with the three new
fixtures so the existing all-clean/missing-file suite tests still cover every configured entity).
Prior task's notes: **Task 56 (2026-08-16) surfaced `imdb_id`/`original_title`/
`homepage` on the movie page** — three `TextField(null=True)` added to the unmanaged `Movie` model
(`django_app/movies/models.py`), read-side only, no DDL/ETL/pipeline changes. `original_title`
renders only when it differs from `title` (guarded in the template, not the view), since it's
identical on ~93% of films and printing it twice is noise. IMDb and the official homepage render
as an "Elsewhere" record row with outbound links (`target="_blank" rel="noopener noreferrer"`, a
visible `↗` marker via a new `.ext-link` CSS rule) — the raw `imdb_id` string never appears as a
label, only inside the href, per the UI rule against surfacing internal-looking identifiers.
Live-verified: `/movies/the-godfather/` shows an "Elsewhere" row linking to `imdb.com/title/tt0068646/`
and `thegodfather.com`; `/movies/warriors-of-the-wind/` shows `originally "風の谷のナウシカ"` above the
tagline; films where original_title equals title (e.g. Inception) show neither the original-title
line nor an empty Elsewhere row. Tests 205 → 207 (2 new: renders-when-differs, hidden-when-same).
Prior phase's notes: **Task 55 (2026-08-15) carried `imdb_id`/`original_title`/
`homepage` from Bronze into `dim_movie`** — three scalar fields with 100%/100%/58.2% Bronze coverage
that had been ingested since Task 42 and dropped at `_flatten_movie()`, never existing past Bronze.
New `warehouse/ddl/12_add_movie_identifiers.sql` (idempotent `ALTER TABLE` + non-unique index on
`imdb_id`) applied live; `01_dimensions.sql` updated for fresh bootstraps; `load_dim_movie()`'s
column list and `silver_checks`' `expected_cols`/`required_cols` extended. Backfilled by rebuilding
Silver from immutable Bronze for all three partitions and re-running `load_dimensions()` — never an
ad-hoc `UPDATE`. Live-verified: `imdb_id` non-null 1,213/1,215, `original_title` differs from
`title` on 101/1,215, `homepage` populated on 699/1,215 (57.5%); warehouse checks 20/20 on the
`2026-07-29` partition. Tests 204 → 205. Task 56 (surface these on the movie page — `original_title`
shown only when it differs from `title`, IMDb/homepage as outbound links, no raw `imdb_id` label per
the UI rule) has not started. **The Analytics dashboard was cut from 9 panels to 2
on 2026-08-14** by user decision — Top Rated Directors, Most Productive Actors, Director Trend,
Actor Collaborations, Genre Growth, Signature Partnerships, and Department Reach were all removed
(view calls, context keys, and template sections deleted from `analytics/views.py` and
`dashboard.html`); only Rating by Decade and Revenue by Genre remain. The separate `/genres/` index
and `/genres/<id>/` detail page were removed at the same time — Genre browsing is now just the
ranked table inside the Revenue by Genre panel, which already showed film count per genre. The nav
lost its Genres link; the genre chips on a movie page are now plain (unlinked) text since there's
nowhere left for them to point. This is a read-side-only change: none of the seven dropped `.sql`
files in `warehouse/queries/` were touched or deleted, same posture as the Franchises removal below
— the query layer is untouched, only the app layer stopped reading most of it. Tests 214 → 204 (10
removed: the genre_list/genre_detail view tests, and the analytics dashboard test collapsed to
match its two surviving context keys). Live-verified: `/analytics/` renders exactly 2 panels;
`/genres/` and `/genres/1/` both 404; nav is Films · People · Analytics. **The Franchises feature was removed from the
application layer on 2026-08-10** by user decision — the `/franchises/` and `/franchises/<slug>/`
routes, both views and templates, the nav link, the "Part of" row on the movie page, the
franchise-revenue dashboard panel and its query, the `Collection` model and `Movie.collection`
FK, and their view tests are all gone. **The data layer was deliberately kept:** `dim_collection`
(358 rows) and `dim_movie.collection_id` are still in the warehouse, `10_collections.sql` is
untouched, and `transform_movies` / `load_dim_collection()` / the Silver DQ config still populate
them every run. Nothing renders any of it. That is a knowingly-accepted write-only path — the
same shape as the four unread Gold datasets below — chosen so the removal stays reversible in one
commit and so Task 58 keeps `load_dim_collection()` as the worked example `load_dim_company()`
copies. Task 59's page-shape reference was repointed from `/franchises/` to `genre_list.html`
accordingly. Live-verified after removal: `/`, `/movies/`, `/people/`, `/genres/`, `/analytics/`
and `/movies/the-godfather/` all 200; `/franchises/` and `/franchises/<slug>/` both **404**; nav
is Films · People · Genres · Analytics; the dashboard is **9 panels** with no franchise text; the
movie page has no "Part of" row. **Tests 210 → 207** (the 3 deleted collection view tests).
**Note the drift in the recorded test counts:** Task 54 below records 214, but the suite was
already at 210 before this removal — the Connect-feature removal (`0d99520`) and the
movie-page vote-count change landed as direct commits without going through the task flow, so
their test deltas were never written down. 207 is the verified current figure. **Phases 12–14 come from an unused-data audit run on
2026-08-10** — every field in the Bronze payloads checked against every reader in the app. Result:
seven fields with 58–100% coverage have been ingested since Task 42 and dropped at
`_flatten_movie()`, never existing at any layer past Bronze — `production_companies` (1,243
studios, 3,200 film links), `production_countries` (40), `spoken_languages` (70),
`origin_country`, `imdb_id`, `original_title` (differs from `title` on 78 films),
`homepage`. `adult`/`video`/`softcore` are always null and are explicitly out of scope. The
audit also found, and Phases 12–14 deliberately do **not** address: 4 of the 5 Gold datasets are
still written every run and read by nothing but an existence check; `dim_movie.status`,
`dim_date.month` and `dim_date.day` have zero readers; `dim_date` holds 49,673 rows of which
1,147 are referenced; `fact_movie_metrics.budget`/`revenue` duplicate `dim_movie` (0 and 3
mismatches respectively, the 3 from an older partition); `silver/actors/` and `silver/directors/`
still sit in S3 though Task 53 stopped writing them; 2 films have no `fact_movie_metrics` row.
All cleanup rather than capability — recorded so they aren't rediscovered as if new. The **stale
Warehouse Schema section in this file was also fixed** (it still listed `dim_actor`,
`dim_director` and `fact_casting`, all dropped in Tasks 35/53). Prior-phase notes follow. Task 54 is a read-side-only fix (no DDL, no ETL, no pipeline re-run, no new TMDB calls) for a movie page that `fact_credit` (Task 48) had made unreadable at scale: a person holding several jobs on one film rendered once per job across several department sections (Christopher Nolan on *The Dark Knight* was 4 rows in 3 sections), and every credit — 47–139 cast, up to ~980 crew on the worst film — rendered as a headshot card with no limit. Two complaints, two fixes, and the measurement that separates them: merging collapses 143.8 crew rows/film to 138.1 distinct people (~4%), so **merging fixes duplication, not volume**. `_merge_crew()` in `movies/views.py` groups a person's non-Acting credits by `person_id`, files them under their single most senior department via `_department_rank()` (extracted from the sort key that was duplicated in `movie_detail` and `person_detail`), and joins the jobs in department order — Nolan now reads "Director / Screenplay / Story / Producer", once. Volume is fixed by **paging cast and crew in the browser**, ten at a time: the view sends every credit and `initPagedSection()` in `static/js/theoria.js` shows a window of them, so Next is a repaint rather than a round-trip. This replaced a first, server-side implementation (`?cast_page=`/`?crew_page=`/`?crew=all` + `#cast`/`#crew` anchors) that the user judged not smooth enough; `BILLED_CREW_JOBS` went with it, since paging ten at a time makes the first page short whatever it holds. `[data-page-group]` wrappers (one per crew department) hide themselves when none of their people are on the current page; the pager is `<button>`s that ship `hidden` and are revealed only when there's more than one page, so with JS off the reader gets the whole list and no dead controls. **The two pagers are a deliberate split, documented in both partials:** `_pager.html` stays server-side for `/movies/` and `/people/` (1,215 and 122,685 rows — not a payload to hand a browser), `_pager_client.html` serves one film's ~1,200-credit maximum, which is. Crew renders as a list rather than a poster grid because crew photo coverage measured 23.8% against cast's 70.1%. **Crew rows carry faces too, after user review:** a headshot where one exists, and the same `.placeholder-person` silhouette the cast cards use where it doesn't (55 photos / 87 silhouettes on The Dark Knight) — an initials monogram was tried first and rejected, since two placeholder vocabularies on one page is one more than a reader should have to learn. That review also surfaced a **real CSS bug**: `.credit-list` never zeroed the `<ul>`'s UA-default 40px `padding-inline-start`, and because these lists paint their own background to draw the 1px gaps as hairlines, that padding rendered as an unexplained grey column down the left of every crew list — invisible on white, obvious on the dark surface. `.collab-list` (person pages) had the identical defect since Task 51; both fixed. Live-verified: `/movies/the-dark-knight/` ships **139 cast cards and 142 crew rows across 11 department groups** in one response, no server-paging params in the markup, Nolan merged and once; the paging algorithm was **executed under Node against a stub DOM** (page 1: 10 items/3 departments; page 2: 2 items collapsing to 1 heading; buttons disable at both ends). Tests 210 → 214. Prior phase's notes: Full test suite is 210/210 passing (down from 225 because Task 53 deleted the legacy actor/director tests, not because anything regressed); Silver DQ 16/16 on all three partitions, warehouse checks 20/20 — both counts dropped for the same reason (the `actors`/`directors` Silver entities and the `fact_cast`/`fact_crew` FK + load-sanity checks no longer exist). **Phase 10 is done: Tasks 47–53 all complete.** The warehouse is now exactly **9 tables** — `dim_movie`, `dim_person`, `dim_genre`, `dim_collection`, `dim_date`, `fact_movie_metrics`, `fact_credit`, `fact_collaboration`, `etl_watermarks` — and `dim_actor`/`dim_director`/`fact_cast`/`fact_crew` are **dropped** (`warehouse/ddl/11_drop_legacy_person_tables.sql`, facts before dimensions). `/people/<slug>/` is the single person page; `/actors/<slug>/` and `/directors/<slug>/` now 301 to it via a single `dim_person` slug lookup with no legacy table involved (the 381 slugs that moved during the Task 48 namespace unification are consequently **404 rather than redirected** — accepted, the site isn't public). 16 routes, 10 analytics panels, **zero empty panels**. **Gold is no longer a write-only dead end** (Task 49) — `load_gold.py` reads `gold/collaboration_edges` into `fact_collaboration`; the other four Gold datasets are still unread, and deliberately so (they're cheap enough to recompute in SQL, which the Django views already do). Caveat that remains open: `fact_collaboration` is a *derived* table, so nothing outside the pipeline can tell it's stale, and re-running Gold for an older partition overwrites counts computed from a newer one — worth revisiting as a materialized view. **Two bugs only the live runs could find, both fixed:** `assign_slugs()`'s batched `executemany` hit a `UniqueViolation` on a slug *permutation* because Postgres checks a unique index per row, not per statement (latent since Task 46; fixed by clearing the column first, in the same transaction); and `/connect/` returned a different — equally short, equally valid — path after every reload, because the adjacency query had no `ORDER BY` while Python dicts preserve insertion order (fixed; verified stable, so **Task 52's outcome line below cites a path that is no longer the one returned** — the current answer for Tom Hanks → Thelma Schoonmaker is *Philadelphia* → Tony Devon → *The King of Comedy*). **Every one of the 122,685 `dim_person` rows has at least one credit**, since the dimension is built from the credits themselves — which retires the long-standing "orphan dimension members" gap outright, now that the legacy tables it applied to are gone. **Fresh-install verification is now empirical, not assumed:** a throwaway database built from DDL `01`–`03` produces exactly the 9 live tables. Note that once `11` *drops* things, "run every DDL file in order" no longer equals "build the current schema" — README documents `01`–`03` (bootstrap) and `04`–`11` (migrations for an existing DB) as two separate paths. Task 43 (person enrichment — bios/birthdays) remains deferred by user decision on cost grounds, and unifying to `dim_person` made it *more* expensive (122,685 people, not 45k). Remaining known gaps, still open: `dim_date` rebuilds ~49,700 rows every load; `*_incremental()` is tested but never called by `run_pipeline.py`; the Silver transforms read Bronze one S3 object at a time (~16 min per full rebuild pass). Older notes: Task 45 restyled the actor/director stat row (removed ruled dividers, added lime measure ticks), removed the eyebrow/accession kicker line from every page, and fixed the Active-period stat so an ongoing career reads "<start>–Active" instead of a closed range ending this year. Task 46 added a `slug` column (+ unique index) to `dim_movie`/`dim_actor`/`dim_director`, populated by `assign_slugs()` in `load_dimensions.py` (recomputes the whole table's slugs every run, collision-numbered in ascending id order, so reruns never reassign an existing row's slug); `/movies/`, `/actors/`, `/directors/` detail routes are now slug-addressed (`/actors/tom-holland/`) and the old numeric-id URLs 404. Genres are still id-addressed (only ~19 rows), as are collections' underlying ids behind their slugs.
Last updated          : 2026-08-17
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
`s3://your-datalake-name/<layer>/<entity>/ingestion_date=YYYY-MM-DD/<file>.{json|parquet}`

---

## Warehouse Schema (star schema)

> The live shape as of Task 54 — 9 tables. `dim_actor`, `dim_director`, `fact_cast` and
> `fact_crew` were **dropped** in Task 53 and are gone; `fact_casting` was replaced in Task 35.
> `warehouse/ddl/01`–`03` bootstrap this schema; `04`–`11` are migrations for an existing DB.

**Dimensions:**
- `dim_movie(movie_id PK, title, release_date, runtime, budget, revenue, original_language, status, overview, tagline, poster_path, backdrop_path, slug, collection_id FK)`
- `dim_person(person_id PK, name, gender, popularity, profile_path, known_for_department, slug)`
- `dim_genre(genre_id PK, genre_name)`
- `dim_collection(collection_id PK, name, poster_path, slug)`
- `dim_date(date_id PK, full_date, year, month, day, decade)`

**Facts:**
- `fact_movie_metrics(movie_id FK, date_id FK, genre_id FK, rating, vote_count, revenue, budget, popularity, ingestion_date)` — PK `(movie_id, date_id, genre_id)`, so a multi-genre film repeats its movie-level measures once per genre. Every query aggregating one must collapse it with `SELECT DISTINCT movie_id, …` first.
- `fact_credit(movie_id FK, person_id FK, department, job, character_name, ordering, ingestion_date)` — PK `(movie_id, person_id, department, job)`, the grain TMDB publishes.
- `fact_collaboration(person_a_id FK, person_b_id FK, films_together, first_year, last_year)` — derived in Gold, `CHECK (person_a_id < person_b_id)`.

**Operational:** `etl_watermarks(loader_name PK, last_ingestion_date, updated_at)`

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
| 14    | Where and in What Language | 61–63 | Not started |

---

## Task List

> Work top to bottom. Don't skip ahead — each phase depends on data the previous one produced.

### Phase 7 — Product Upgrade

> Full plan: `~/.claude/plans/that-s-it-the-project-recursive-ocean.md`. Workstreams were
> deliberately started with C (frontend) per user instruction; A/B/D follow.

#### [x] Task 34 — Frontend rebuild (Workstream C: browsable + styled + visual)
- **Goal:** Turn the ID-only, unstyled Django app into a browsable, styled, cross-linked movie site.
- **Files:** `django_app/static/css/theoria.css` (new), `templates/base.html`, `movies/{views,urls}.py`, all `movies/templates/movies/*.html` (incl. new `movie_list`, `person_list`, `genre_list`, `_movie_card`, `_person_card`), new `movies/templatetags/tmdb_images.py`, `analytics/templates/analytics/dashboard.html`, `settings.py` (`STATICFILES_DIRS`), `config.py`/`.env.example` (`TMDB_IMAGE_BASE_URL`), `tests/test_django_views.py`
- **Outcome:** Four new list routes (`/movies/` with `?q=` title search + `?sort=` rating/revenue/release/title + pagination; `/actors/` and `/directors/` with name search + pagination via a shared `_person_list()` helper; `/genres/` chips) fix the broken nav link and make every entity reachable by browsing. All detail pages rebuilt as styled, cross-linked templates (hero backdrop + poster + cast grid on movie pages, stat tiles + poster-grid filmography on people pages) with one hand-written stylesheet (light/dark via `prefers-color-scheme`). Image markup is guarded with `{% if %}` + the new `tmdb_image` filter (base URL from `config.TMDB_IMAGE_BASE_URL`), so pages degrade gracefully until Workstream B adds poster/backdrop/profile columns. Analytics dashboard restyled as cards; Chart.js pinned to 4.4.1. Home gained top-rated/newest poster strips. Verified live: all 11 routes + static CSS return 200 against the real warehouse; search/sort/pagination work. Tests: 7 new/updated view tests + a filter test; full suite 166/166.

#### [x] Task 35 — Workstream A: split `fact_casting` into `fact_cast` + `fact_crew`
- **Goal:** Eliminate the ~46% reject rate caused by the actor×director cross-join.
- **Outcome:** Replaced the single `fact_casting(movie_id, actor_id, director_id, ...)` table (both FKs NOT NULL, populated by cross-joining every credited actor with every credited director per movie) with two independent facts: `fact_cast(movie_id, actor_id, role, ordering, ingestion_date)` and `fact_crew(movie_id, director_id, ingestion_date)`. Neither loader looks at the other dimension, so a movie's cast no longer disappears when it has no resolvable director — the root cause of ~52% of movies (58/112) rendering an empty Cast section. `fact_crew` models director credits only, mirroring `dim_director`'s existing scope (people credited as director); other crew roles are out of scope. Changed: `warehouse/ddl/02_facts.sql` (fresh-bootstrap schema) + new `warehouse/ddl/05_split_fact_casting.sql` (live migration: create both tables, drop `fact_casting`); `etl/warehouse_loader/load_facts.py` (`_build_cast_rows`/`_build_crew_rows`, `load_fact_cast`/`load_fact_crew` replace the cross-join builder/loader); `data_quality/warehouse_checks.py` (`_FK_CHECKS` and `check_fact_load_sanity` now check `fact_cast`/`fact_crew` independently); all 4 `warehouse/queries/*.sql` files that referenced `fact_casting` (none actually needed the actor×director pairing); `movies/models.py` (`Cast`/`Crew` replace `Casting`); `movies/views.py` (`movie_detail`/`actor_detail`/`director_detail` — the Python dedup workaround in `movie_detail` is gone, since `fact_cast` has one row per actor by construction); `movie_detail.html` provenance label. Live migration applied: `fact_casting` dropped, `fact_cast`/`fact_crew` created, `load_facts()` re-run for the existing `2026-07-09` Silver partition — `fact_cast` upserted 3,345 rows with **0 rejects** (previously 1,816 rows / 1,714 rejected, all reason `"no director for movie"`); `fact_crew` upserted 54 rows. Verified live: warehouse checks 22/22 pass; distinct movies with cast rose from 54/112 to 99/112 (the remaining 13 are movies from an earlier, non-reloaded partition, not a fact_cast/fact_crew issue); `/movies/120/` (LOTR: Fellowship of the Ring — previously zero cast) now renders its full cast. Tests: replaced ~10 Task-19 cross-join tests with independent-builder tests plus an explicit regression test proving cast renders with no director present; full suite 174/174. Surfaced but out of scope: a separate bug in `transform_credits_bridge.py`'s crew dedup key drops the "Director" role for crew members credited with multiple jobs (see Blockers above) — affects the "Directed by" line, not Cast.

#### [x] Task 36 — Workstream B: carry poster/backdrop/tagline/headshot fields Silver → warehouse
- **Goal:** Surface image/rich fields already present in Bronze JSON; zero new API calls.
- **Outcome:** The four image/rich fields were being dropped at the Silver step — now carried through end to end (no new TMDB calls; all present in Bronze detail/credits JSON). `transform_movies._flatten_movie()` keeps `tagline`/`poster_path`/`backdrop_path` (TMDB `""` normalised to `None`); `transform_people` keeps `profile_path` for both cast and crew. Added the columns to `dim_movie` (tagline/poster_path/backdrop_path) and `dim_actor`/`dim_director` (profile_path) in `01_dimensions.sql`, plus a new `warehouse/ddl/04_add_image_columns.sql` (idempotent `ADD COLUMN IF NOT EXISTS`) to ALTER the already-live tables. `load_dimensions.py` upsert column lists extended; `silver_checks.py` expected-schema lists extended; `movies/models.py` gained the matching `TextField(null=True)` fields (the templates already referenced them behind `{% if %}`). Tests: fixtures updated + 2 new assertions that the fields survive the transforms; full suite 168/168.

#### [x] Task 37 — Workstream D: re-apply DDL, re-run pipeline live, verify end-to-end
- **Goal:** Fresh live run at a bigger sample size (MAX_PAGES to be confirmed by user) + full verification.
- **Outcome:** Applied `04_add_image_columns.sql` to the live warehouse, then ran `scripts.run_pipeline --date 2026-07-09 --max-pages 5` (user-chosen size; 100 movies). All stages green: Silver 99 movies / 3133 actors / 104 directors, Silver DQ 20/20, Gold 4 datasets, dims upserted, `fact_movie_metrics` 250 (0 rejected), `fact_casting` 1816 (1714 rejected — same expected cross-join limitation, Task 35 will fix), warehouse checks 20/20. Verified populated: dim_movie 99 posters / 95 backdrops / 80 taglines; dim_actor 2342 headshots; dim_director 84 headshots. A rendered `/movies/<id>/` page emits real `https://image.tmdb.org/t/p/w1280` backdrop + `w342` poster URLs (200). Note: user did **not** raise MAX_PAGES beyond 5 this run.

#### [x] Task 38 — Frontend redesign (AP-521): one design system, white + lime
- **Goal:** Replace the two disjoint themes (dark "projection booth" on Home/Analytics, unstyled system-ui everywhere else) with a single, fully-designed system across all ten pages.
- **Files:** `static/css/{theoria,home,analytics}.css` (all rewritten), `static/js/theoria.js` (new, shared) + `analytics.js` (charts only) + `home.js` (deleted), `templates/base.html`, all `movies/templates/movies/*.html` (incl. new `_sheet_header.html`, `_person_header.html`), `analytics/templates/analytics/dashboard.html`, `movies/views.py`, `tests/test_django_views.py`
- **Outcome:** One token system (colour/type/space/motion) in `theoria.css`, with a hard contract that page stylesheets may only *add* components, never restyle a shared one — every `body.page-*` override deleted. Palette is white paper + one lime, with lime reserved as the "measurement mark" (meter bars, keyed posters, active nav); all ink/lime pairs contrast-verified, and the pale in-table bar is legal only because its value is printed on top. Type is Archivo used at two widths (expanded display / condensed labels) + Instrument Sans body + Spline Sans Mono for keys and figures. Every page opens with a shared "sheet header" (shelf label · accession · display title), which is what makes the eight previously-plain pages read as designed. Home's hero is the signature: the full catalog as a contact sheet with top-rated films keyed in lime, developing on load in the one orchestrated motion moment; the grain/lamp-flicker/infinite-marquee were removed. `movie_detail` replaced its darkened-backdrop hero with a specimen layout (framed plate + record list + backdrop strip); `genre_list` went from a chip row to a ranked sheet with share bars; the dashboard became white panels keeping all 7 panels' data bindings. `initMeters()`/number formatting moved into shared `theoria.js` so any page can use `data-meter`, and `analytics.js` now reads its palette from CSS custom properties instead of duplicating five hexes. Views: `genre_list` gained a distinct `Count` annotation, `home` a `mosaic` + `keyed_ids` context, and `movie_detail` now collapses the duplicate cast rows the `fact_casting` cross-join produces for multi-director films. Verified live: all 10 routes + 5 static assets 200, zero template syntax leaking into output, no horizontal overflow at 390px, skip-link is the first tab stop with visible focus rings, and `prefers-reduced-motion` leaves the mosaic fully visible with meters at final width. Tests: 169/169 (added a cast-deduplication test; updated the `genre_list`, `home` and `movie_detail` mock chains).

---

#### [x] Task 39 — Backfill missing poster/backdrop/headshot images (stale Silver partition)
- **Goal:** Fix movies/actors/directors rendering with no picture, reported alongside the missing-Cast bug (Task 35).
- **Outcome:** Not a TMDB data gap and not a code bug — `warehouse/ddl` and `etl/silver/transform_{movies,people}.py` were already correct as of Task 36. The cause: the `2026-07-06` Silver partition (99 movies, 3291 actors, 108 directors) was produced *before* Task 36 added `tagline`/`poster_path`/`backdrop_path`/`profile_path` to the transform code, so its Silver Parquet never had those columns at all. `dim_movie`/`dim_actor`/`dim_director` upsert only the movies/people present in whichever partition is (re)loaded — the later `2026-07-09` run (Task 37) covered a different, only-partially-overlapping set of 100 movies from TMDB's live "popular" list, so the ~13 movies (and their casts) unique to `2026-07-06` — including *The Lord of the Rings: The Return of the King*, *Superman*, *KPop Demon Hunters* — never got their image columns backfilled, despite Bronze (immutable) having always had the raw `poster_path`/`backdrop_path` values. Confirmed via direct Bronze reads before touching anything: TMDB's raw JSON for movie 122 (LOTR: ROTK) already had both paths; the Silver Parquet for that date simply didn't carry the column. Fix required no code changes — re-ran `transform_movies()`/`transform_people()` for `ingestion_date=2026-07-06` (Silver is rebuilt from immutable Bronze, so this is safe/idempotent per project convention), confirmed via `silver_checks.py` (20/20 pass), then re-ran `load_dimensions()` for the same date to upsert the now-complete rows. No `load_facts`/pipeline re-run needed — this only touched dimension attributes. Verified live: movies missing a poster went from 13→0, missing backdrop 17→5 (the remaining 5 — obscure/foreign titles — confirmed via direct Bronze read to genuinely have `backdrop_path: null` from TMDB itself, i.e. real sparsity, not a bug); actors missing a headshot went from 1206→898, directors 36→22 (spot-checked several high-profile names — Nathan Fillion, Halle Berry, James Gunn, Florence Pugh, Clint Eastwood — all now populated). `/movies/122/` (LOTR: ROTK) now renders real `image.tmdb.org` poster and backdrop URLs (previously blank). No test changes needed (pure data reprocessing, no code touched); full suite still 174/174.

---

### Phase 8 — Correctness & Catalog Depth

> Full plan: `~/.claude/plans/what-else-can-we-lazy-unicorn.md`. Theme: the pipeline runs
> clean and every check passes, which was hiding the fact that the warehouse was silently
> discarding data it had already ingested. Fix the silent losses first, then grow the catalog.

#### [x] Task 40 — Fix the crew dedup key so directors survive Silver
- **Goal:** Stop `transform_credits_bridge` from destroying "Director" credits.
- **Files:** `etl/silver/transform_credits_bridge.py`, `data_quality/silver_checks.py`, `etl/gold/build_gold_datasets.py`, `tests/test_etl.py`
- **Outcome:** The bridge deduplicated crew rows on `(movie_id, person_id, credit_type)`, but `credit_type` is only ever the literal `"crew"` — it doesn't distinguish jobs. Any crew member with more than one job on a film collapsed to a single row via `keep="last"`, and since directors very often also produce or write (measured: **65 of 99 movies**), the "Director" row was usually the one thrown away. Bronze had a director for **99/99** movies the entire time; the transform was deleting them on every run. Fixed by adding `role` (which holds the job title for crew) to the dedup subset, making the key match the true grain of a credit. Two companion fixes in the same commit: `silver_checks.ENTITY_CONFIGS["credits_bridge"]["pk_cols"]` encoded the *same* wrong key — so it confirmed the bug rather than catching it, and would have started failing on every legitimate multi-job row after the fix; and `_build_director_ratings()` in Gold filtered `credit_type == "crew"` without `role == "Director"` (unlike `load_facts._build_crew_rows()`, which filters both), so Gold counted every producer/writer/editor as a director. Rebuilt Silver from immutable Bronze and reloaded facts for both partitions (`2026-07-06`, `2026-07-09`): `fact_crew` 54 → **128 rows** with 0 rejects; movies with a director 47/112 → **111/112** (the one holdout, *A Lustful Night*, genuinely has no director in Bronze — real TMDB sparsity); directors with 3+ films 0 → 1, so the **"Top Rated Directors" dashboard panel renders for the first time** (Christopher Nolan, 4 films, avg 8.22). Verified live: `/movies/155/` (The Dark Knight, previously zero crew rows) now shows "Directed by Christopher Nolan". Silver DQ 20/20, warehouse checks 22/22, tests **176/176** (2 new: a regression test that a Director+Producer double credit keeps both rows, and one that Gold excludes non-director crew).

#### [x] Task 41 — Put the score and the synopsis on the movie page
- **Goal:** Surface `rating`/`vote_count` (already in `fact_movie_metrics`) and `overview` (already 99/99 non-null in Silver, but with no warehouse column).
- **Files:** `warehouse/ddl/01_dimensions.sql` + new `06_add_overview.sql`, `etl/warehouse_loader/load_dimensions.py`, `django_app/movies/{models,views}.py`, `movie_detail.html`, `static/css/theoria.css`, `tests/{test_etl,test_django_views}.py`
- **Outcome:** Neither value needed a new API call — both were already in the pipeline and simply never reached the page. `overview` was extracted by `transform_movies._flatten_movie()`, written to Silver, and even listed in `silver_checks`' expected columns, but `dim_movie` had no such column so `load_dim_movie()`'s explicit column list silently couldn't carry it (a case of the "never `SELECT *`" rule cutting both ways: not selecting a column is indistinguishable from not having one). Added `overview TEXT` to `01_dimensions.sql` for fresh bootstraps plus an idempotent `06_add_overview.sql` for the live DB, and added the column to the loader list — backfilled by *re-running* `load_dimensions()` for both partitions rather than writing an `UPDATE`. For the rating, `movie_detail` now reads `fact_movie_metrics` via `.values("rating","vote_count").distinct().first()`: the fact is at `(movie_id, date_id, genre_id)` grain, so a multi-genre film repeats the same rating once per genre — `Avg()` would return the right number only by accident, since rating is a movie-level measure fanned out by the genre dimension, not a set of measurements to average. Live: `overview` populated for **109/112** movies (3 genuinely blank in TMDB, verified against Bronze); `/movies/155/` now renders synopsis, `Rating 8.5 / 10`, `36,040 votes` and `Directed by Christopher Nolan`. Tests **177/177** — one existing loader test asserted the exact upserted column set and its fixture already contained `overview`, so the test had been pinning the bug in place.

#### [x] Task 42 — `discover/movie`: break out of the popular-list ceiling
- **Goal:** Replace "popular right now" as the only corpus source; build a deliberate multi-decade catalog.
- **Files:** `etl/tmdb_client.py`, new `etl/bronze/ingest_discover.py`, `config.py`, `.env.example`, `scripts/run_pipeline.py`, `warehouse/queries/{director_trend_over_time,genre_growth_over_time}.sql`, `README.md`, `tests/test_etl.py`
- **Outcome:** `movie/popular` returns whatever is trending *at call time*, which is why the catalog was 112 films with 77 of them from the 2020s — no transform or aggregate can recover a time axis the extraction step never collected. Added `TMDBClient.discover_movies()` (a thin wrapper on the existing `get()`, so it inherits retry/backoff) and `ingest_discover.py`, which iterates **one release year at a time** and pages within each year: windowing the request predicate is what makes an unbounded catalog reachable through an API that caps pagination depth. Ids are deduplicated across year windows while preserving discovery order. The four new `DISCOVER_*` config values are a *definition of the dataset*, not tuning knobs — lowering `DISCOVER_MIN_VOTES` doesn't slow the pipeline, it makes the warehouse describe a different population. `ingest_movies()` is untouched and still the default; `--source discover` selects between them and everything downstream is identical, since both return a plain `list[int]`. **Live run, 30.6 min end to end:** Bronze 1,140/1,140 details + 1,140/1,140 credits, **0 failures**; Silver 1,140 movies / 43,138 actors / 604 directors / 231,728 bridge rows, 0 parse errors; Silver DQ 20/20; warehouse checks 22/22. Warehouse went **112 → 1,215 films**, 3,548 → **44,554 actors**, 120 → **677 directors**, 3,345 → **62,713 cast credits**, evenly spread ~200 films/decade (2020s share 69% → 16%), and directors with 3+ films **1 → 146** — "Top Rated Directors" is now a real leaderboard (Miyazaki, Kubrick, Tarantino, Kurosawa). The bigger corpus exposed a latent bug: `director_trend_over_time.sql` and `genre_growth_over_time.sql` had **no `LIMIT`** and returned 1,304 / 791 rows into fixed-height panels — both capped at 300, and director trend now also requires ≥3 films so the panel shows careers rather than one-off credits. Tests **182/182** (5 new).

#### [ ] Task 43 — Person enrichment (bios, birthdays, birthplaces) — DEFERRED
- **Goal:** `person/{id}` enrichment, restricted to people who actually have credits.
- **Status:** Deferred by user decision on 2026-07-29, on cost grounds. Task 42 grew `dim_actor` from 3,548 to 44,554, so this went from ~3.5k API calls to ~45k (~6h at the observed ~2 req/s). Scoped options if resumed: top-5-billed actors + all directors ≈ 4k calls; actors in 2+ films + directors ≈ 9.6k calls; everyone ≈ 45k calls.

#### [x] Task 44 — Live re-run, verification, and doc truth-up
- **Goal:** Full run at the new corpus size; fix README's missing DDL steps and stale test counts.
- **Files:** `README.md`, `docs/architecture.md`, `CLAUDE.md`, `for_learning.md`
- **Outcome:** Verified live at the new scale: all 11 routes + static assets return 200; `/movies/238/` (The Godfather) renders rating 8.7/10, 23,226 votes, synopsis, "Directed by Francis Ford Coppola" and a cast including Brando and Pacino — a page that before Phase 8 would have had no rating, no synopsis and possibly no director. A bad id still 404s. Silver DQ 20/20, warehouse checks 22/22 (incl. `bronze_to_silver` 1140=1140), tests 182/182. Three real doc defects fixed: (1) **README's schema-setup step listed only DDL `01`–`03`**, so anyone following it on a fresh machine built an incomplete warehouse — silent, not loud, which is the worst kind; (2) the test count was stale in three places; (3) `architecture.md` had no account of the corpus-source decision or the dedup-grain bug, both now written up (§2 "Choosing the corpus", §3 "Resolved: the credits-bridge dedup grain"), including why the DQ check *shared the bug's premise* and why total-row-count checks can't catch "right number of rows, wrong ones" — that needs reconciliation controls asserting a conserved quantity across a layer boundary.

---

### Phase 9 — Frontend Polish & URL Design

> Ad hoc user-driven polish after Phase 8, working directly against the live 1,215-film
> catalog rather than through the numbered-task planning process used for Phases 1–8.

#### [x] Task 45 — Person-page stat row and career-period fixes
- **Goal:** The actor/director Films/Avg rating/Active stat row read as a boxed table (ruled dividers on every side), the small "eyebrow" kicker labels above every page title were redundant or actively duplicated the title, and the Active-period stat showed a closed year range even for an ongoing career (e.g. "2026–2026" for a director whose only dated film releases this year).
- **Files:** `django_app/static/css/theoria.css`, `movies/templates/movies/{_person_header,_sheet_header,movie_detail,actor_detail,director_detail,genre_detail,genre_list,home,movie_list,person_list}.html`, `movies/views.py`, `tests/test_django_views.py`
- **Outcome:** Removed `.stat`'s `border-right` and `.stats`'/`.person-head`'s top/bottom rules entirely, then reworked `.stats` into a tight flex cluster where each figure carries a small lime tick — the same "this one is measured" mark already used on the active nav link and meter fills, rather than a divider. Separately removed the eyebrow/accession row (`INDEX Actors`, `FILM CATALOG · MEASURED`, `dim_actor · 880`, etc.) from every page per user decision, after finding `person_list.html` was passing the accession the *same* text as the title (a real duplication bug, not by design). For the Active stat, added `_career_period(start, end)` in `views.py`: collapses a single-year career to one bare year instead of repeating it (`"2015"` not `"2015–2015"`), and renders an open-ended career as `"<start>–Active"` (or bare `"Active"`) whenever the latest known film is this year or later, instead of implying a career that's still going already "ended". Tests 187/187 (5 new, covering each `_career_period` branch).

#### [x] Task 46 — URL slugs for movies, actors, and directors
- **Goal:** Hide the warehouse surrogate keys from movie/actor/director URLs (`/actors/880/` → `/actors/tom-holland/`) — a bare integer id in the URL exposes internal identifiers for no reason a browsing user needs.
- **Files:** `warehouse/ddl/01_dimensions.sql` + new `07_add_slugs.sql`, `etl/warehouse_loader/load_dimensions.py`, `django_app/movies/{models,views,urls}.py`, `movies/templates/movies/{_movie_card,_person_card,home,movie_detail}.html`, `tests/{test_etl,test_django_views}.py`, `README.md`, `docs/architecture.md`
- **Outcome:** Added a nullable `slug VARCHAR(300)` column + unique index to `dim_movie`/`dim_actor`/`dim_director` (`07_add_slugs.sql` for the live DB, `01_dimensions.sql` for a fresh bootstrap). New `assign_slugs(session, table, id_col, name_col)` in `load_dimensions.py` runs after each table's normal Silver upsert and **recomputes every row's slug from the whole table**, not just the partition just loaded — a slug scheme where a collision is only checked against the current batch would silently produce two rows with the same slug the moment a later partition introduced a same-named person, and Postgres's unique index would then reject the load outright. Collisions are broken by walking the table in ascending id order and numbering repeats (`john-smith`, `john-smith-2`, ...), which is what makes the scheme idempotent across reruns: the same row always gets the same slug, since only a new (larger) id can ever be appended after the existing numbering, never inserted ahead of it. `_slugify()` NFKD-folds accented characters to their ASCII base (e.g. "Zoë Kravitz" → `zoe-kravitz`) rather than dropping them. Django's `urls.py` swapped `<int:*_id>` for `<slug:*_slug>` on all three detail routes (genres stay id-based — only ~19 rows, collisions aren't a concern); `movie_detail`/`actor_detail`/`director_detail` now fetch by `slug=` and read the numeric id off the fetched object for every downstream query, so the rest of each view is unchanged. **Live backfill:** applied `07_add_slugs.sql`, then one `load_dimensions()` run against the existing `2026-07-29` Silver partition assigned slugs to all **1,215 movies, 44,554 actors, 677 directors** in ~25s, zero collisions left unresolved, zero nulls. Verified live: `/actors/tom-holland/`, `/movies/the-godfather/`, `/directors/christopher-nolan/` all 200; the old `/actors/880/`, `/movies/238/` now 404 (ids are no longer reachable, as intended); cast/director links on a movie page render as `/actors/marlon-brando/` etc. Tests 192/192 (5 new: slug collision numbering, accent folding, rerun stability).

---

### Phase 10 — People, Partnerships & Franchises

> Full plan: `~/.claude/plans/explore-the-project-look-snuggly-whistle.md`. Theme: the
> warehouse models a catalog of titles when the data it already holds describes a network of
> people. Crew is ingested and then discarded at the loader (169,682 of 170,915 crew credits,
> 79,523 people); `belongs_to_collection` has never been read at any layer. Zero new TMDB
> calls — every task below rebuilds from immutable Bronze.

#### [x] Task 47 — Silver: every person, every department, every collection
- **Goal:** Stop the Silver transforms from discarding crew identity, craft, and franchises.
- **Files:** `etl/silver/{transform_people,transform_credits_bridge,transform_movies}.py`, `data_quality/silver_checks.py`, `tests/{test_etl,test_data_quality}.py`
- **Outcome:** `transform_people()` gained `_extract_people()`, which chains the TMDB `cast` and `crew` arrays through one `_person_row()` mapper and writes a new `silver/people/people.parquet` — one row per distinct credited person, with `known_for_department`. The old `_extract_directors()` filter (`job == "Director"`) was the single line deciding who existed in the warehouse at all; it excluded 79,523 people whose credits were already in Bronze. The legacy `actors`/`directors` outputs are still written (retired in Task 53) so nothing downstream breaks mid-phase. `transform_credits_bridge()` now carries `department` (`"Acting"` for cast, TMDB's crew department otherwise) — deliberately **not** added to the dedup key, since TMDB assigns each job to exactly one department so department is functionally determined by `role`, and widening a key past the true grain is what caused the Task 40 bug. `transform_movies._flatten_movie()` flattens `belongs_to_collection` (nested, and `null` on ~48% of films — hence `or {}`) into `collection_id`/`collection_name`/`collection_poster_path`. `silver_checks.py` gained a `people` entity config written from the *credits payload shape* rather than mirroring the transform, plus the three new movie columns and `department` in the bridge's expected schema. Silver rebuilt from immutable Bronze for all three partitions; verified live on `2026-07-29`: 1,140 movies with **591 (51.8%) in one of 344 collections**. Tests 192 → 195.

#### [x] Task 48 — Warehouse: `dim_person` + `fact_credit`
- **Goal:** Give every credited person a warehouse row and every credit a fact row.
- **Files:** new `warehouse/ddl/08_person_credits.sql`, `warehouse/ddl/{01_dimensions,02_facts}.sql`, `etl/warehouse_loader/{load_dimensions,load_facts}.py`, `data_quality/warehouse_checks.py`, `tests/{test_etl,test_warehouse_checks}.py`
- **Outcome:** `dim_person(person_id PK, name, gender, popularity, profile_path, known_for_department, slug)` and `fact_credit(movie_id, person_id, department, job, character_name, ordering, ingestion_date)` added, with the fact's **PK `(movie_id, person_id, department, job)` chosen to match the grain TMDB actually publishes** (a director who also wrote and produced is three credits) rather than discovered after a bug, per the Task 40 lesson. `_build_credit_rows()` replaces `_build_crew_rows()`'s `role == "Director"` predicate — the one line that discarded 169,682 of 170,915 crew credits — and rejects rows only for unresolvable FKs, never editorially; cast credits normalise to `department="Acting"`, `job="Actor"` with the part in `character_name`. Legacy `dim_actor`/`dim_director`/`fact_cast`/`fact_crew` are untouched and still loading (retired in Task 53), so no commit leaves the site broken. **Live backfill across all three partitions: `dim_person` 122,685 rows / 122,685 slugs (0 null), `fact_credit` 237,454 rows across 13 departments and 858 distinct job titles** — vs 45,231 people and 64,031 credits before. Warehouse checks **25/25** incl. both new FK checks. Verified: *The Godfather* went from 81 cast rows + 1 director to **187 credits across 12 departments**, and the warehouse can now answer "Spielberg: 19 films with John Williams, 19 with Michael Kahn, 11 with Janusz Kamiński". **Slug namespace unified as flagged in the plan — 376 of 44,554 actor slugs and 5 of 677 director slugs changed once** (a crew member with a lower TMDB id claims the base slug); measured before and confirmed after, not assumed. **Bug found by the live run, not the tests:** `assign_slugs()` rewrites slugs as a batched `executemany`, and Postgres checks a unique index after every row — so a *permutation* (B takes `dee-wallace` while A still holds it) is rejected despite the final state being unique. Latent since Task 46; it never fired because those tables were only ever loaded in ways where recomputed slugs were identical. Fixed by clearing the column before rewriting, both inside the caller's transaction, with a regression test naming the live failure. Tests 195 → 202.
#### [x] Task 49 — Gold earns a reader: derive `fact_collaboration`
- **Goal:** Turn the credits into a collaboration graph, and give the Gold layer its first consumer.
- **Files:** `etl/gold/build_gold_datasets.py`, new `etl/warehouse_loader/load_gold.py`, new `warehouse/ddl/09_collaboration.sql`, `scripts/run_pipeline.py`, `data_quality/warehouse_checks.py`, `tests/{test_etl,test_warehouse_checks}.py`
- **Outcome:** `_build_collaboration_edges()` emits one row per unordered pair of key collaborators (`films_together`, `first_year`, `last_year`), written to `gold/collaboration_edges/` and then loaded into `fact_collaboration` by the new `load_gold.py` — **the first thing in the project that reads Gold**, which had been written on every run since Task 14 and consumed by nothing. **The scoping decision is the point:** pairing every credit on every film gives **33.1M** edges and asserts a caterer collaborated with a stunt double; restricting to key credits (`TOP_BILLED_CUTOFF = 10` + nine `KEY_CREW_JOBS`) gives **181,538** for the same partition — a 180× reduction that comes from defining what a collaboration *is*, not from a `LIMIT` (contrast Task 42's unbounded queries). Both constants are declared as a definition, like `config.DISCOVER_*`. Pairs are canonical (`person_a_id < person_b_id`) **by construction** — `sorted()` + `itertools.combinations` can only emit `(smaller, larger)` — and the property is enforced by a SQL `CHECK`, not left to the loader; `itertools.combinations` also avoids the self-merge alternative that materialises n² rows per film including mirror images. Two composite indexes ending in `films_together DESC`, one per side, so "this person's top collaborators" is an index range scan with no sort. `load_gold` deliberately does **not** quarantine unresolvable rows the way the Silver-sourced loaders do — a Gold edge references people derived from the same partition, so an FK miss means Gold and the dimension load disagree, which is logged at ERROR. **Live: 193,064 edges over 12,301 people, 11,828 pairs with 2+ films, 3,232 with 3+** — Spielberg + Michael Kahn 20 films (1977–2018), Spielberg + John Williams 19, Scorsese + Thelma Schoonmaker 11, Nolan + Emma Thomas 11, Burton + Elfman 11. Warehouse checks **28/28**. Tests 202 → 209.
#### [x] Task 50 — Franchises: `dim_collection` and the series pages
- **Goal:** Make film series a first-class entity; `belongs_to_collection` had never been read at any layer.
- **Files:** new `warehouse/ddl/10_collections.sql`, `warehouse/ddl/01_dimensions.sql`, `etl/warehouse_loader/load_dimensions.py`, `django_app/movies/{models,views,urls}.py`, new `movies/templates/movies/{collection_list,collection_detail}.html`, `movie_detail.html`, `templates/base.html`, `tests/{test_etl,test_django_views}.py`
- **Outcome:** `dim_collection(collection_id PK, name, poster_path, slug)` plus a **nullable** `dim_movie.collection_id` FK. Promoted to a dimension rather than left as three columns on `dim_movie` because a franchise has its own identity, artwork, slug and URL and is shared by many films — 17 Bond films would otherwise repeat the name 17 times with no row to hang a page off. The FK is nullable *by design*: ~half the catalog stands alone, which is a fact about films rather than missing data. `load_dim_collection()` derives the dimension from the denormalised Silver column via `drop_duplicates`, filtering on both id **and** name (an id without a name would violate `name NOT NULL`), and runs **before** `load_dim_movie()` since the FK points that way. New routes `/franchises/` (ranked sheet reusing the genre page's `table-2col` + `data-meter` share bars — no new CSS or JS) and `/franchises/<slug>/` (entries in release order, span, avg rating, series revenue), plus a "Part of" row on the movie page and a Franchises nav entry. `collection_list` uses `.annotate(Count).filter(movie_count__gt=0)` so the `filter` compiles to `HAVING` and empty series never list. `collection_detail` sums revenue straight off `dim_movie` (one row per film) but collapses `fact_movie_metrics` with `.values().distinct()` before averaging rating — two aggregates on one page, only one needing the genre-fanout guard. **Live: 358 collections, 613/1,215 films in a series, 127 with 2+ entries, 0 null slugs.** All routes 200, bad slug 404s; James Bond renders **17 films, 1971–2021, $6,082,635,670**; *The Godfather* links to its collection. Tests 209 → 214.
#### [x] Task 51 — Django: unified person pages + repeat collaborators
- **Goal:** Surface Tasks 47–49's data — one page per person, every credit, and who they keep working with.
- **Files:** `django_app/movies/{models,views,urls}.py`, new `movies/templates/movies/person_detail.html`, `{_person_header,_person_card,_movie_card,person_list,movie_detail,home}.html`, `templates/base.html`, `static/css/theoria.css`, `tests/test_django_views.py`
- **Outcome:** New `Person`/`Credit`/`Collaboration` models (same `managed=False` + fake-composite-PK pattern as the other facts). `/people/<slug>/` replaces the actor and director pages: credits grouped by department via an explicit `DEPARTMENT_ORDER` (Acting first, then the crafts — alphabetical would put Art before Directing), a **"Works with"** readout from `fact_collaboration`, and a Credits stat shown only when it differs from Films (a director who also wrote and produced is one film, three credits). `/actors/` and `/directors/` survive as **scopes** of `/people/` — `Person.objects.filter(credits__department=...).distinct()`, where the `.distinct()` is load-bearing (the join yields one row per credit, so an actor with 40 credits would otherwise appear 40 times). Nav collapsed Actors+Directors into one People entry with an on-page scope switch. **Legacy detail URLs 301 by id, never by slug** — unifying the namespaces re-numbered 381 slugs, so `/actors/tom-holland/` correctly lands on `/people/tom-holland-2/`; a slug-to-slug redirect would have sent 381 URLs to the wrong person. `_top_collaborators()` queries `person_a_id` and `person_b_id` separately and reads the person off the *opposite* side of each row — the read-side cost of Task 49's canonical ordering, paid deliberately. `movie_detail` now reads `fact_credit`, adding Crew sections grouped by department; home counts People + Credits instead of Actors + Directors. New `.collab-list` and `.scope` components added to `theoria.css` (never restyling a shared one, per the CSS contract); `_movie_card.html` gained an optional `card_sub`. **Live: *The Godfather* renders 187 credits across 11 departments** (was 81 cast + 1 director); Spielberg's page shows Michael Kahn 20, John Williams 19, Janusz Kamiński 11; **Thelma Schoonmaker — an editor, invisible to the warehouse before this phase — has a page: 11 films, ★7.82, 1980–2023, top collaborator Scorsese.** All routes 200, bad slug 404s. Tests 214 → 216.
#### [x] Task 52 — The path finder: `/connect/`
- **Goal:** Measure the distance between any two people in the catalog, through the films they share.
- **Files:** new `django_app/movies/graph.py`, new `movies/templates/movies/connect.html`, `movies/{views,urls}.py`, `templates/base.html`, `static/css/theoria.css`, `tests/test_django_views.py`
- **Outcome:** New `movies/graph.py` builds an in-memory adjacency from `fact_credit` (all cast + nine principal crew jobs) and finds shortest paths with **bidirectional BFS**. **Deliberately a different graph from `fact_collaboration`** and sharing no code with it: the persisted table asks "who works together repeatedly" and is scoped to key credits, this one asks "is there any path" and includes a 40th-billed extra, because that is a real connection but not a working relationship. A recursive CTE over this graph still times out (>60s, measured in Phase 8) because Postgres can't memoise the visited set across iterations — BFS is fast precisely *because* the visited set is the algorithm. Bidirectional because a one-sided frontier from a hub hits ~31,000 people at depth 2 and ~41,000 at depth 3; expanding whichever side is smaller turns one depth-*d* search into two of *d/2*. Adjacency is cached against a **data version** (`count(*) + max(ingestion_date)` on `fact_credit`), not a clock, so it rebuilds exactly when a load happens and never otherwise; `_build_adjacency` uses `setdefault` so the film cited for a pair is stable across rebuilds. `_describe_path()` resolves the whole chain in **two** queries rather than walking it per hop — an N+1 whose N is the answer's length. Four designed outcomes (path / same person / unconnected / unknown name), each a real state of the data. New `.chain` component in `theoria.css` draws the path as a lime rule with a tick per person — the lime *is* the measurement, the one computed thing on the page. **Live: Tom Hanks → Thelma Schoonmaker 2 degrees in 30 ms** (*Catch Me If You Can* → DiCaprio → *The Wolf of Wall Street*); **Marlon Brando → Zendaya 3 degrees routed through Franco Arcalli, an editor** — a hop impossible before this phase. Graph shape: **49,276 people, 23 components, 99.1% in the largest**, printed on the page as its own readout. Tests 216 → 225.
#### [x] Task 53 — Analytics, retirement, live re-run, doc truth-up
- **Goal:** Spend the new model on the dashboard, retire the legacy actor/director tables now that nothing reads them, re-run the whole pipeline live, and true up the docs.
- **Files:** `warehouse/queries/` (4 rewritten + 3 new), `django_app/analytics/{views.py,templates/analytics/dashboard.html}`, new `warehouse/ddl/11_drop_legacy_person_tables.sql`, `warehouse/ddl/{01_dimensions,02_facts}.sql`, `etl/silver/transform_people.py`, `etl/warehouse_loader/{load_dimensions,load_facts}.py`, `data_quality/{silver_checks,warehouse_checks}.py`, `django_app/movies/{models,views,graph}.py`, `README.md`, `docs/architecture.md`, `tests/{test_etl,test_django_views,test_warehouse_checks}.py`
- **Outcome:** Three new panels (`signature_partnerships.sql`, `department_reach.sql`, `franchise_revenue.sql`) and four rewritten onto `dim_person`/`fact_credit` — the dashboard is **10 panels with zero "No data available"**, every query under 0.5s, every one carrying an explicit `LIMIT` (Task 42's lesson) and the `SELECT DISTINCT movie_id` CTE where `fact_movie_metrics`' genre fan-out demands it. **Retirement was the point of the ordering:** `fact_cast`/`fact_crew`/`dim_actor`/`dim_director` were dropped (facts before dimensions, so no FK blocks the drop) only after Tasks 48–51 had moved every reader off them; `transform_people()` now returns a single URI instead of a 3-tuple, and `load_dim_actor`/`load_dim_director`/`load_fact_cast`/`load_fact_crew`, the `Actor`/`Director`/`Cast`/`Crew` models, and their checks and tests are gone. The legacy redirects survive as a one-line `dim_person` slug lookup, so the shims no longer depend on the tables they shim. Silver DQ 24→**16/16** and warehouse checks 28→**20/20**: both *fell* because the entities being checked no longer exist, which is worth stating so a future reader doesn't read it as a regression. Full live re-run across all three partitions in **219.0s**, all stages green. **A fresh install was verified empirically rather than by reading the README** — a throwaway database `theoria_bootstrap_test` built from DDL `01`–`03` produced exactly the 9 tables the live warehouse has. That test surfaced the doc defect worth having: once migration `11` *drops* tables, "run every DDL file in order" is no longer the same instruction as "build the current schema", so `01`–`03` (bootstrap) and `04`–`11` (migrate an existing DB) are now documented as two distinct paths. **One real bug found by the live walk of all 16 routes:** `/connect/` returned a *different* — equally short, equally valid — path on each reload, because Postgres returns rows in no order without `ORDER BY` while Python dicts preserve insertion order, so the adjacency was rebuilt in a different order every time and BFS picked a different one of the tied shortest paths; one `ORDER BY movie_id, person_id` made it reproducible (verified stable across repeated requests). Tests 225 → **210**, the drop being deleted legacy coverage.

---

### Phase 11 — Movie Page Legibility

> Full plan: `~/.claude/plans/there-are-some-things-vectorized-mist.md`. Theme: Phase 10 gave
> every credited person a warehouse row and every credit a fact row, which was the right data
> model and made the movie page unreadable — a multi-job crew member repeated once per job
> across several sections, and every credit rendered as a headshot card with no ceiling.

#### [x] Task 54 — Fix crew duplication and cast/crew volume on the movie page
- **Goal:** Every credit still renders (no data hidden), but nobody's name appears twice and the page stays readable at 47–139 cast plus up to ~980 crew: merge a person's several crew jobs on one film into a single row filed under their most senior department, and page both cast and crew ten at a time in the browser, so moving through them costs no request.
- **Files:** `django_app/movies/views.py`, new `movies/templates/movies/_pager.html`, `movies/templates/movies/{movie_list,person_list,movie_detail}.html`, `static/css/theoria.css`, `tests/test_django_views.py`
- **Outcome:** New `_merge_crew(credits)` in `views.py` groups a film's non-Acting credits by `person_id` and returns one record per person: jobs joined in department order via a new `_department_rank(name)` helper (extracted from the identical inline sort-key lambda that used to live separately in both `movie_detail` and `person_detail` — a pure refactor, no behaviour change to `person_detail`), filed under the single most senior department (`min` by `_department_rank`). Christopher Nolan on *The Dark Knight* — previously 4 rows across Directing/Writing/Writing/Production — is now one row reading "Director / Screenplay / Story / Producer". Cast needed no merging: `fact_credit.job` is the literal `"Actor"` for all 62,713 Acting rows, verified before writing any code, so cast was already one row per person — its problem is purely volume, fixed with `Paginator(cast, CAST_PER_PAGE).get_page(request.GET.get("cast_page"))` (`CAST_PER_PAGE = 24`), clamping on out-of-range pages exactly like `movie_list`'s existing pager. Crew defaults to a short "billed crew" list — merged people holding at least one of nine principal jobs in a new `BILLED_CREW_JOBS` constant, deliberately its own copy rather than imported from `movies.graph.PATH_CREW_JOBS` or `etl.gold.build_gold_datasets.KEY_CREW_JOBS` (same nine job titles, but Task 52 already decided "who works together repeatedly" vs "is there any path" must not share code, and "what does a viewer want to see first" is a third question; importing the Gold builder into a view would also drag pandas/boto3 into the request path) — with `?crew=all` revealing the full department-grouped list, built only when requested. A person can legitimately be both cast and crew on one film (e.g. an actor who also produced); merging never removes them from the cast grid. New `_pager.html` partial extracts the pagination markup duplicated (and hardcoded to `page=`) in `movie_list.html`/`person_list.html`, parameterized by `param` and a `base_query` string built in the view with `django.utils.http.urlencode` (not assembled in the template, since a shared partial can't know which params a given page carries, and `movie_detail`'s cast pager has to preserve an active `?crew=all`). New `.credit-list`/`.credit-row`/`.credit-name`/`.credit-jobs` CSS component, modelled on the existing `.collab-list`'s 1px-gap-on-`--rule` hairline-divider trick, renders crew as a list rather than a poster grid — deliberate, since crew profile-photo coverage measured at 23.8% against cast's 70.1% would otherwise put a placeholder silhouette on three of every four crew cards. Read-side only: no DDL, no ETL, no pipeline re-run, no new TMDB calls, no DB writes. **Live-verified on `/movies/the-dark-knight/`:** cast shows 24 of 139 with a working pager (`?cast_page=2` returns a different 24; `?cast_page=99` clamps to page 6/6 rather than erroring); billed crew shows 10 of 142; "Christopher Nolan" appears exactly once inside the crew block, reading "Director / Screenplay / Story / Producer" (a second, expected occurrence is the pre-existing, unrelated "Directed by" record line); `?crew=all` renders all 11 department sections (Directing, Writing, Production, Camera, Editing, Sound, Art, Costume & Make-Up, Visual Effects, Lighting, Crew). `/movies/the-godfather/` mirrors this: 9 of 100 billed crew, Coppola once, reading "Director / Screenplay / Producer". A bad slug still 404s. `movie_list`/`person_list` pagination hrefs verified byte-for-byte unchanged against the old hardcoded markup, including with a search query needing URL-encoding. **Superseded within the same task by user decision:** the cast/crew pagers were first built server-side (`?cast_page=`/`?crew_page=`, plus `#cast`/`#crew` anchors to stop the reload landing at the top of the page). The user judged the round-trip per ten people not smooth enough, so **both are now paged in the browser**: the view sends every credit, and `initPagedSection()` in `static/js/theoria.js` shows a window of ten. The `?crew=all` toggle and the `BILLED_CREW_JOBS` subset are **gone** — paging ten at a time makes the first page short whatever it holds, so a second definition of "important crew" stopped earning its keep. A `[data-paged]` container declares `data-page-size` and a `data-page-items` selector; `[data-page-group]` wrappers (one per crew department) hide themselves when none of their people are on the current page, so a heading never sits above an empty ruled list. The pager is `<button>`s, not links — paging changes nothing about the document's address — and ships with `hidden`, revealed only when there is more than one page, so with JS off the reader gets the whole list rather than dead controls. **The split is deliberate and documented in both partials:** `_pager.html` (server-side) still serves `/movies/` and `/people/`, whose result sets are 1,215 and 122,685 rows; `_pager_client.html` serves one film's ~1,200-credit maximum, which is a payload a browser can hold. Cast headshots already carried `loading="lazy"`, and a lazy image inside a hidden container is never fetched, so shipping all 139 cards costs markup but not bandwidth (93 lazy images on The Dark Knight). Live-verified: `/movies/the-dark-knight/` ships **139 cast cards and 142 crew rows across 11 department groups** in one response, both navs `hidden`, and no `cast_page`/`crew_page`/`crew=all` anywhere in the markup. The paging algorithm itself was executed under Node against a stub DOM rather than eyeballed: page 1 shows 10 items across 3 departments, page 2 shows the remaining 2 and collapses to 1 heading, both buttons disable at their ends, and the state readout clamps. Tests 210 → **214** (4 new: multi-job merge, cast/crew overlap for one person, every-credit-is-sent (guarding against a server-side limit creeping back in and silently truncating a film), and department-order grouping; 3 existing tests updated to read `cast_page`/`billed_crew` instead of the now-gone `cast` context key). `person_detail` has a milder version of the same duplication (a writer-director's film listed twice under Writing) and is explicitly left alone per the approved plan — `_merge_crew()` takes a plain credit list rather than a queryset specifically so it can be reused there unchanged later, without being applied now.

---

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

#### [ ] Task 62 — Django: provenance on the movie page, and browse by country/language
- **Goal:** Surface both without inventing two more entity pages nobody asked for.
- **Files:** `django_app/movies/{models,views}.py`, `movies/templates/movies/{movie_detail,movie_list}.html`, `tests/test_django_views.py`
- **Steps:**
  1. Countries and languages on the movie page, in the record list. **Render the origin/production distinction only when they disagree** (22.7% of films) — on the other 77% two identical country lists is noise. This is the same judgment as Task 56's `original_title`.
  2. `/movies/?country=` and `?language=` filters on the existing list page, alongside `?q=` and `?sort=`. **Filters, not detail pages** — a country is a facet of a film, not a thing with a biography, and `/movies/?country=JP` answers the real question ("what Japanese films are here") with no new template.
  3. The filter must survive pagination: `_pager.html` already takes a `base_query`, and `movie_list` already builds one with `urlencode` — extend it, don't rebuild it.
  4. `dim_movie.original_language` already exists and is already shown on the movie page (16 distinct values). **Reconcile it with `spoken_languages` rather than shipping two language facts side by side** with no explanation of how they differ.
- **Outcome:**

#### [ ] Task 63 — Analytics, live re-run, verification, doc truth-up
- **Goal:** The phase-closing task, following Tasks 44 and 53.
- **Files:** new `warehouse/queries/*.sql`, `analytics/{views.py,dashboard.html}`, `README.md`, `docs/architecture.md`, `CLAUDE.md`, `for_learning.md`
- **Steps:**
  1. Panels for films by country of production and non-English cinema over time.
  2. **Full live pipeline re-run across all three partitions** — the first end-to-end run since the new Silver entities existed, so it's the first proof `run_pipeline.py` sequences them correctly.
  3. Silver DQ and warehouse checks: both counts **rise** this phase (new entities, new FK checks). Record the new numbers so a future reader doesn't misread the change.
  4. Walk all routes live, including the new studio pages and both new filters.
  5. **Verify a fresh install empirically, not by reading the README** — a throwaway DB built from DDL `01`–`03` must produce exactly the live table list, per the Task 53 lesson that "run every DDL file in order" stopped being the same instruction as "build the current schema" once migration `11` dropped tables.
  6. Update `docs/architecture.md` with the bridge-table decision (why `bridge_` not `fact_`, and why a bridge is right for companies where a column was right for collections), and this file's Warehouse Schema section with the final table list.
- **Outcome:**

---

## Additional Reference

Full design rationale and original architecture decisions: `docs/architecture.md`
Learning log (updated after every task): `for_learning.md`
