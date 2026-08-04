-- Task 50 (Phase 10): dim_collection — film franchises.
--
-- TMDB returns `belongs_to_collection` on every movie-detail payload and it had
-- never been read at any layer. It's a dimension rather than three more columns
-- on dim_movie because a collection has its own identity, name, artwork and URL,
-- and is shared by many films — putting it inline would repeat the name once per
-- entry and leave no row to hang a /franchises/<slug>/ page off.
--
-- Roughly half the catalog belongs to no collection, so dim_movie.collection_id
-- is nullable. That is a real property of films, not missing data.
--
-- These definitions are also in 01_dimensions.sql for fresh bootstraps.
-- Safe to re-run (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS dim_collection (
    collection_id INTEGER      NOT NULL,
    name          TEXT         NOT NULL,
    poster_path   TEXT,
    slug          VARCHAR(300),
    CONSTRAINT pk_dim_collection PRIMARY KEY (collection_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_collection_slug ON dim_collection (slug);

ALTER TABLE dim_movie ADD COLUMN IF NOT EXISTS collection_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_dim_movie_collection'
    ) THEN
        ALTER TABLE dim_movie
            ADD CONSTRAINT fk_dim_movie_collection
            FOREIGN KEY (collection_id) REFERENCES dim_collection (collection_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_dim_movie_collection_id ON dim_movie (collection_id);
