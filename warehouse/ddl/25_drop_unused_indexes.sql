-- Ad hoc (2026-09-23): reclaim Neon free-tier storage again — the project was
-- at 467 MB of its 512 MB cap. Two moves, both zero functional loss.
--
-- 1. Drop 18 secondary indexes nothing needs.
--
--    a. Prefix-duplicates (same reasoning as 23_reclaim_warehouse_storage.sql):
--       each is a single-column index on the leading column of the table's
--       primary key or another composite index, which already serves any
--       lookup — and any FK check — on that column. idx_dim_episode_series_order
--       is an exact copy of the unique idx_dim_episode_natural_key.
--       (idx_person_alias_person_id, the biggest of these, goes with its table
--       in 26_drop_person_alias.sql.)
--
--    b. Effectively unused: idx_fcredit_department / idx_fsc_department
--       (11 and 5 scans ever — a filter on department alone is too
--       unselective to use them, and department filters in the app go through
--       idx_*_person_dept), and idx_fcredit_ingestion_date /
--       idx_fsc_ingestion_date, whose only reader is the nightly
--       data-quality row count (a seq scan of ~400k rows is milliseconds).
--
-- 2. VACUUM FULL, run *after* the drops so there is headroom: it writes a
--    compact copy of each table (and its indexes) before freeing the old one.
--    Neon's heaps were ~2x their live size — measured against the local
--    replica, which is truncate-and-reloaded and so has zero dead space.
--    Smallest-first, so each rewrite frees space for the next, larger one.
--    VACUUM cannot run inside a transaction: apply with plain `psql -f`, never
--    `psql -1`. Each table is ACCESS EXCLUSIVE-locked while it is rewritten —
--    run outside the nightly-refresh window.

DROP INDEX IF EXISTS idx_bridge_movie_company_movie_id;
DROP INDEX IF EXISTS idx_bridge_movie_country_movie_id;
DROP INDEX IF EXISTS idx_bridge_movie_language_movie_id;
DROP INDEX IF EXISTS idx_bridge_series_genre_series_id;
DROP INDEX IF EXISTS idx_bridge_series_company_series_id;
DROP INDEX IF EXISTS idx_bridge_series_country_series_id;
DROP INDEX IF EXISTS idx_bridge_series_language_series_id;
DROP INDEX IF EXISTS idx_bridge_series_network_series_id;
DROP INDEX IF EXISTS idx_fmm_movie_id;
DROP INDEX IF EXISTS idx_fact_movie_rating_movie_id;
DROP INDEX IF EXISTS idx_fact_series_rating_series_id;
DROP INDEX IF EXISTS idx_fact_episode_rating_episode_id;
DROP INDEX IF EXISTS idx_dim_season_series_id;
DROP INDEX IF EXISTS idx_dim_episode_series_order;

DROP INDEX IF EXISTS idx_fcredit_department;
DROP INDEX IF EXISTS idx_fcredit_ingestion_date;
DROP INDEX IF EXISTS idx_fsc_department;
DROP INDEX IF EXISTS idx_fsc_ingestion_date;

VACUUM (FULL, ANALYZE) dim_company;
VACUUM (FULL, ANALYZE) dim_series;
VACUUM (FULL, ANALYZE) dim_season;
VACUUM (FULL, ANALYZE) dim_movie;
VACUUM (FULL, ANALYZE) dim_movie_video;
VACUUM (FULL, ANALYZE) dim_date;
VACUUM (FULL, ANALYZE) fact_episode_rating;
VACUUM (FULL, ANALYZE) dim_episode;
VACUUM (FULL, ANALYZE) fact_credit;
VACUUM (FULL, ANALYZE) dim_person;
VACUUM (FULL, ANALYZE) fact_series_credit;
