-- Task 36 (Workstream B): carry image/rich fields from Silver into the warehouse.
-- These columns are added to 01_dimensions.sql for fresh bootstraps; this file
-- ALTERs the already-live dimension tables in place. All idempotent via IF NOT EXISTS.

ALTER TABLE dim_movie    ADD COLUMN IF NOT EXISTS tagline       TEXT;
ALTER TABLE dim_movie    ADD COLUMN IF NOT EXISTS poster_path   TEXT;
ALTER TABLE dim_movie    ADD COLUMN IF NOT EXISTS backdrop_path TEXT;

ALTER TABLE dim_actor    ADD COLUMN IF NOT EXISTS profile_path  TEXT;
ALTER TABLE dim_director ADD COLUMN IF NOT EXISTS profile_path  TEXT;
