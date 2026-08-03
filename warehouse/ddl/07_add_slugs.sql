-- Task: URL slugs for movies, actors, and directors.
-- Idempotent migration for the already-live warehouse; 01_dimensions.sql carries
-- the same columns/indexes for a fresh bootstrap.
--
-- slug is nullable at the DDL level: it is populated by a separate pass
-- (etl.warehouse_loader.load_dimensions.assign_slugs) that recomputes every
-- row's slug deterministically, not by the row-upsert itself. A unique index
-- still allows multiple NULLs in Postgres, so this is safe before that pass
-- has ever run.

ALTER TABLE dim_movie ADD COLUMN IF NOT EXISTS slug VARCHAR(300);
ALTER TABLE dim_actor ADD COLUMN IF NOT EXISTS slug VARCHAR(300);
ALTER TABLE dim_director ADD COLUMN IF NOT EXISTS slug VARCHAR(300);

CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_movie_slug ON dim_movie (slug);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_actor_slug ON dim_actor (slug);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_director_slug ON dim_director (slug);
