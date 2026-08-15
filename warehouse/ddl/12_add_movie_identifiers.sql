-- Task 55: carry imdb_id, original_title and homepage from Silver into the warehouse.
--
-- All three have been present in silver/movies (100%/100%/58.2% Bronze coverage)
-- since Task 42's discover/movie corpus, but no warehouse column existed for any
-- of them, so load_dim_movie()'s explicit column list silently couldn't carry
-- them — same shape as Task 41's overview gap. The columns are added to
-- 01_dimensions.sql for fresh bootstraps; this file ALTERs the already-live
-- table in place, matching the 04_add_image_columns.sql / 06_add_overview.sql
-- pattern.
--
-- imdb_id gets a non-unique index: it's an external lookup key, but nothing in
-- this warehouse guarantees TMDB never repeats one, so it isn't a candidate key.
--
-- Idempotent via IF NOT EXISTS. Backfill by re-running load_dimensions() for
-- each existing ingestion_date — Silver already carries the values.

ALTER TABLE dim_movie ADD COLUMN IF NOT EXISTS imdb_id VARCHAR(20);
ALTER TABLE dim_movie ADD COLUMN IF NOT EXISTS original_title TEXT;
ALTER TABLE dim_movie ADD COLUMN IF NOT EXISTS homepage TEXT;

CREATE INDEX IF NOT EXISTS idx_dim_movie_imdb_id ON dim_movie (imdb_id);
