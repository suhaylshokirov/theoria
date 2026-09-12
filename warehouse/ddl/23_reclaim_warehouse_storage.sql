-- Ad hoc, alongside Task 87: reclaim Neon free-tier storage (512 MB project
-- cap, at 94% after Task 87's live backfill — a bulk UPDATE across all of
-- dim_person to fix a missed slug-assignment step).
--
-- Two independent moves, both zero functional loss:
--
-- 1. Drop fact_collaboration entirely. It is fully derived in Gold
--    (etl/gold/build_gold_datasets.py::_build_collaboration_edges) and, when
--    checked before dropping it, had exactly zero live readers: no Django
--    view imports the Collaboration model, and the one analytics query that
--    reads it (warehouse/queries/actor_collaboration_frequency.sql, deleted
--    alongside this) was never wired into django_app/analytics/views.py. It
--    was flagged as exactly this lever back when the warehouse was 215 MB
--    (see tasks.md) — "fully derived... reclaimable if it ever tightens".
--    It did. etl/warehouse_loader/load_gold.py, which existed only to load
--    this one table, is deleted in the same change; recoverable from git
--    history if a real "people who worked together" feature ever gets built.
--
-- 2. Drop five redundant secondary indexes. Postgres can satisfy a query on
--    a single leading column using any composite index that starts with
--    that column, so a separate single-column index on the same column is a
--    pure duplicate — same query plan, no plan regression from dropping it:
--      fact_credit:        idx_fcredit_person_id (person_id) duplicates
--                           idx_fcredit_person_dept's leading column;
--                           idx_fcredit_movie_id (movie_id) duplicates
--                           pk_fact_credit's leading column.
--      fact_series_credit: idx_fsc_person_id / idx_fsc_series_id, same
--                           reasoning against idx_fsc_person_dept /
--                           pk_fact_series_credit.
--      dim_person:         idx_dim_person_imdb_id — not a prefix-duplicate,
--                           just confirmed unused: imdb_id is only ever
--                           displayed (a link to IMDb), never filtered or
--                           joined on anywhere in the app or the pipeline.
--
-- Together: ~46 MB reclaimed on a database that was otherwise about to fail
-- a nightly load outright rather than degrade.

DROP TABLE IF EXISTS fact_collaboration;

DROP INDEX IF EXISTS idx_fcredit_person_id;
DROP INDEX IF EXISTS idx_fcredit_movie_id;
DROP INDEX IF EXISTS idx_fsc_person_id;
DROP INDEX IF EXISTS idx_fsc_series_id;
DROP INDEX IF EXISTS idx_dim_person_imdb_id;
