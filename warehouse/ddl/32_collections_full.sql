-- Task 153 (Franchises from TMDB collections): everything TMDB knows about a collection.
--
-- Until now dim_collection held the three scalars a movie payload carries in
-- `belongs_to_collection` (id, name, poster_path). `GET /collection/{id}` adds the
-- rest: a backdrop, the original name and language, an overview, the list of every
-- part, and (via append_to_response) translations and an image gallery. This file
-- stores all of it. Measured 2026-11-16 across 392 collections; see tasks.md.
--
--   dim_collection             + backdrop_path, original_name, original_language,
--                                overview, homepage
--   dim_collection_part        one row per film TMDB lists as part of a collection
--   dim_collection_image       one row per poster / backdrop in a collection's gallery
--   collection_translation     per-language name, overview, homepage
--   collection_part_translation per-language title and overview of a part
--
-- `homepage` is the `en` translation entry's data.homepage: the top-level payload has
-- no homepage, and that entry is the only English one TMDB publishes (34 of 368).
--
-- dim_collection_part.movie_id has NO FOREIGN KEY. 692 of 1,313 parts are films the
-- catalogue does not hold; they are stored as listing rows and shown unlinked, not
-- pulled in as full movies (user decision, 2026-11-16: that would grow the catalogue
-- 47% and Neon by ~45-60 MB). movie_id is a soft reference resolved at read time, the
-- same posture as dim_company.parent_company_id.
--
-- dim_collection_part.genre_ids is an array, not a bridge: it is a list of labels on
-- a listing row, with no measure and no catalogue row to join to. TMDB movie genre ids
-- are dim_genre ids. The table is dim_ (not fact_/bridge_) for the reason
-- dim_movie_video is: a multi-valued attribute of one dimension.
--
-- Not stored, on purpose, from /collection parts[]:
--   media_type  always 'movie' (1,313 of 1,313) - it is the endpoint, not data
--   softcore    always false (1,313 of 1,313), undocumented
-- Every other key is a column. `position` is the index in TMDB's `parts` array; TMDB's
-- order is NOT release order (it is in only 277 of 368 collections), so the site sorts
-- by release_date and keeps position only as a stable tiebreak.
--
-- Load strategy: parts, images, translations and part translations are loaded by
-- REPLACE keyed on collection_id (a franchise gains parts when a sequel is announced
-- and can lose images), never by upsert. Children are deleted before parents.
--
-- Also folded into 01_dimensions.sql for fresh bootstraps. Safe to re-run (IF NOT
-- EXISTS throughout). Additive: the code running before this file is applied is
-- unaffected. Apply to Neon and the local replica BEFORE deploying code that reads it.

ALTER TABLE dim_collection ADD COLUMN IF NOT EXISTS backdrop_path      TEXT;
ALTER TABLE dim_collection ADD COLUMN IF NOT EXISTS original_name      TEXT;
ALTER TABLE dim_collection ADD COLUMN IF NOT EXISTS original_language  VARCHAR(10);
ALTER TABLE dim_collection ADD COLUMN IF NOT EXISTS overview           TEXT;
ALTER TABLE dim_collection ADD COLUMN IF NOT EXISTS homepage           TEXT;

CREATE TABLE IF NOT EXISTS dim_collection_part (
    collection_id     INTEGER       NOT NULL,
    movie_id          INTEGER       NOT NULL,
    position          SMALLINT      NOT NULL,
    title             TEXT,
    original_title    TEXT,
    original_language VARCHAR(10),
    release_date      DATE,
    overview          TEXT,
    poster_path       TEXT,
    backdrop_path     TEXT,
    genre_ids         INTEGER[],
    popularity        NUMERIC(12,4),
    vote_average      NUMERIC(5,3),
    vote_count        INTEGER,
    adult             BOOLEAN,
    video             BOOLEAN,
    ingestion_date    DATE,
    CONSTRAINT pk_dim_collection_part PRIMARY KEY (collection_id, movie_id),
    CONSTRAINT fk_dim_collection_part_collection
        FOREIGN KEY (collection_id) REFERENCES dim_collection (collection_id)
);
CREATE INDEX IF NOT EXISTS idx_dim_collection_part_movie
    ON dim_collection_part (movie_id);

CREATE TABLE IF NOT EXISTS dim_collection_image (
    collection_id  INTEGER       NOT NULL,
    file_path      TEXT          NOT NULL,
    kind           TEXT          NOT NULL,
    iso_639_1      VARCHAR(8),
    iso_3166_1     VARCHAR(8),
    width          INTEGER,
    height         INTEGER,
    aspect_ratio   NUMERIC(6,4),
    vote_average   NUMERIC(5,3),
    vote_count     INTEGER,
    ingestion_date DATE,
    CONSTRAINT pk_dim_collection_image PRIMARY KEY (collection_id, file_path),
    CONSTRAINT ck_dim_collection_image_kind CHECK (kind IN ('poster', 'backdrop')),
    CONSTRAINT fk_dim_collection_image_collection
        FOREIGN KEY (collection_id) REFERENCES dim_collection (collection_id)
);
CREATE INDEX IF NOT EXISTS idx_dim_collection_image_kind
    ON dim_collection_image (collection_id, kind);

-- Same shape as series_translation (29_series_translation.sql); `name` because that is
-- what dim_collection calls it. Only `ru` carries a name (etl.translations.NAME_LANGUAGES);
-- Uzbek rows exist only where TMDB has a homepage or overview for them.
CREATE TABLE IF NOT EXISTS collection_translation (
    collection_id  INTEGER       NOT NULL,
    lang           VARCHAR(5)    NOT NULL,
    name           TEXT,
    overview       TEXT,
    homepage       TEXT,
    ingestion_date DATE          NOT NULL,
    CONSTRAINT pk_collection_translation PRIMARY KEY (collection_id, lang),
    CONSTRAINT fk_collection_translation_collection
        FOREIGN KEY (collection_id) REFERENCES dim_collection (collection_id)
);

-- The only source of Russian text for the parts that are not in the catalogue
-- (movie_translation covers catalogue films only): GET /collection/{id}?language=ru-RU
-- localises every part's title and overview. Kept only where it differs from English.
CREATE TABLE IF NOT EXISTS collection_part_translation (
    collection_id  INTEGER       NOT NULL,
    movie_id       INTEGER       NOT NULL,
    lang           VARCHAR(5)    NOT NULL,
    title          TEXT,
    overview       TEXT,
    ingestion_date DATE          NOT NULL,
    CONSTRAINT pk_collection_part_translation PRIMARY KEY (collection_id, movie_id, lang),
    CONSTRAINT fk_collection_part_translation_part
        FOREIGN KEY (collection_id, movie_id)
        REFERENCES dim_collection_part (collection_id, movie_id)
);
