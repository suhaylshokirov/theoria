-- Task 65: studio provenance — description, headquarters, homepage, parent.
--
-- dim_company's five original columns (company_id, name, logo_path,
-- origin_country, slug) all come from the thin `production_companies` stub
-- embedded in a *movie's* detail payload. TMDB keeps that stub deliberately
-- minimal. The richer fields live on `GET /company/{id}`, called for the
-- first time in this project by etl/bronze/ingest_companies.py.
--
-- No FK on parent_company_id, and that is deliberate. A parent company (e.g.
-- Warner Bros. Entertainment #17, or Viacom International) is frequently a
-- holding company that is never itself directly credited on a film — so it
-- has no bridge_movie_company row and therefore no dim_company row (that
-- dimension is, by Task 58's design, the distinct set of companies actually
-- linked to a movie). Enforcing referential integrity would force either
-- rejecting a legitimate parent link or fabricating a dim_company row for a
-- company that was never linked to a film. Instead parent_company_id is a
-- soft, unenforced reference resolved at read time: the studio page links to
-- the parent only when a dim_company row for it happens to exist, and
-- otherwise renders parent_company_name as plain text (the same "render only
-- when it resolves" judgement Task 56 applied to IMDb/homepage links).
--
-- Measured coverage (live probe, 2026-08-30): among the top 50 studios by
-- film count — the ones people actually open — headquarters 96%, homepage
-- 64%, parent_company 10%, description 4%. Catalog-wide description is ~1%.
-- The provenance block is really "where they are / their site / their
-- parent"; description is carried anyway, for the ~1% that have one.
--
-- These columns are also in 01_dimensions.sql for fresh bootstraps.
-- Safe to re-run (ADD COLUMN IF NOT EXISTS throughout).

ALTER TABLE dim_company ADD COLUMN IF NOT EXISTS description         TEXT;
ALTER TABLE dim_company ADD COLUMN IF NOT EXISTS headquarters        TEXT;
ALTER TABLE dim_company ADD COLUMN IF NOT EXISTS homepage            TEXT;
ALTER TABLE dim_company ADD COLUMN IF NOT EXISTS parent_company_id   INTEGER;
ALTER TABLE dim_company ADD COLUMN IF NOT EXISTS parent_company_name TEXT;
