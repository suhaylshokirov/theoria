-- Task 41: carry the film synopsis from Silver into the warehouse.
--
-- `overview` has been present in silver/movies (99/99 non-null) since Task 9,
-- but no warehouse column existed for it, so the loader never selected it and
-- nothing on the site could say what a film is about. The column is added to
-- 01_dimensions.sql for fresh bootstraps; this file ALTERs the already-live
-- table in place, matching the 04_add_image_columns.sql pattern.
--
-- Idempotent via IF NOT EXISTS. Backfill by re-running load_dimensions() for
-- each existing ingestion_date — Silver already carries the values.

ALTER TABLE dim_movie ADD COLUMN IF NOT EXISTS overview TEXT;
