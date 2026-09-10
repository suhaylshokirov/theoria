-- Task 84 (TV Shows): dim_season, dim_episode, fact_episode_rating — the
-- episode grain in the warehouse, ready for the show page (Tasks 87–88).
--
-- Also folded into 01_dimensions.sql (dim_season, dim_episode) and
-- 02_facts.sql (fact_episode_rating) for fresh bootstraps. Safe to re-run
-- (IF NOT EXISTS throughout).
--
-- ---------------------------------------------------------------------------
-- An episode is a DIMENSION plus a rating FACT, not one fact_episode carrying
-- descriptive text and measures together. This is architectural call 1 of the
-- TV feature (see tasks.md), the same split dim_movie + fact_movie_rating and
-- dim_movie_video (Task 74) already use:
--   * the episode's own attributes (name, overview, still, air date, runtime)
--     live in a table the loader replaces once per series load, separate from
--   * the numbers that move every night — rating / vote_count per source —
--     which sit in fact_episode_rating, byte-for-byte the fact_movie_rating /
--     fact_series_rating shape with episode_id in place of the parent id.
--
-- dim_episode.episode_id is TMDB's own GLOBAL episode id, not the composite
-- (series_id, season_number, episode_number). It is a real stable identifier
-- of the same character as movie_id / series_id, and a single-column PK keeps
-- fact_episode_rating narrow (one integer FK, not three columns). The natural
-- key is still protected — a UNIQUE index on
-- (series_id, season_number, episode_number) — and re-checked in the warehouse
-- DQ (grain:dim_episode) because _replace_by_parent re-inserts a whole
-- series' episodes every load.
--
-- LOAD STRATEGY for dim_episode is REPLACE, not upsert — _replace_by_parent()
-- (etl/warehouse_loader/common.py) keyed on series_id. This is the SECOND use
-- of the Task 74 pattern and the first where the parent is not a movie. TMDB
-- renumbers and withdraws episodes (a season re-cut, a special reclassified,
-- a mini-series restructured), so a series' episode set must be able to
-- SHRINK. A pure upsert would leave a phantom episode on the show page
-- forever, with no failing check — exactly the dead-embed problem
-- dim_movie_video was given _replace_by_parent to avoid. The delete is scoped
-- to the series_ids in the current Silver partition, never a blanket TRUNCATE.
--
-- dim_season stays on plain upsert: a show's SEASON list is stable in a way
-- its episode list is not (renumbering happens within a season far more often
-- than a whole season is withdrawn), and dim_season carries no measure and no
-- ingestion_date — it is a thin descriptive stub off the seasons[] array that
-- already rides bronze/series_details. A withdrawn season simply stops
-- receiving episodes; the show page (Task 88) renders an empty season as a
-- quiet "not available" state, not a crash.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dim_season (
    season_id     INTEGER NOT NULL,   -- TMDB's global season id
    series_id     INTEGER NOT NULL,
    season_number SMALLINT,
    name          TEXT,
    air_date      DATE,
    episode_count INTEGER,
    overview      TEXT,
    poster_path   TEXT,
    CONSTRAINT pk_dim_season PRIMARY KEY (season_id),
    CONSTRAINT fk_dim_season_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_season_series_number
    ON dim_season (series_id, season_number);
CREATE INDEX IF NOT EXISTS idx_dim_season_series_id ON dim_season (series_id);

CREATE TABLE IF NOT EXISTS dim_episode (
    episode_id      INTEGER NOT NULL,   -- TMDB's global episode id
    series_id       INTEGER NOT NULL,
    season_number   SMALLINT,
    episode_number  SMALLINT,
    name            TEXT,
    air_date        DATE,
    runtime         INTEGER,
    overview        TEXT,
    still_path      TEXT,
    episode_type    TEXT,
    production_code TEXT,
    imdb_id         VARCHAR(16),        -- backfilled by transform_imdb_ratings (Task 83)
    ingestion_date  DATE    NOT NULL,
    CONSTRAINT pk_dim_episode PRIMARY KEY (episode_id),
    CONSTRAINT fk_dim_episode_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id)
);
-- Natural-key uniqueness (episode_id is the surrogate PK). Postgres allows
-- multiple NULLs here, which is the right behaviour for an unaired episode
-- with no number yet.
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_episode_natural_key
    ON dim_episode (series_id, season_number, episode_number);
-- The show page reads a whole series' episodes in (season, episode) order.
CREATE INDEX IF NOT EXISTS idx_dim_episode_series_order
    ON dim_episode (series_id, season_number, episode_number);
-- Non-unique: the Phase 15 IMDb join key, same posture as dim_series.imdb_id
-- (19_series.sql) and dim_person.imdb_id (17_person_details.sql).
CREATE INDEX IF NOT EXISTS idx_dim_episode_imdb_id ON dim_episode (imdb_id);


-- fact_episode_rating (Task 84) is the third copy of the fact_movie_rating
-- shape (15_movie_ratings.sql), after fact_series_rating (21_series_ratings.sql):
-- one row per (episode, source), IMDb as the rating of record and TMDB kept
-- beside it for comparison. So AVG(rating) needs no de-dup guard, and
-- CHECK (source IN ('imdb','tmdb')) enforces the vocabulary (Django owns the
-- display metadata — no dim_rating_source, the same call as for films and
-- shows). fact_ not bridge_, because it carries measures.
--
-- silver/episode_ratings (Task 83) already emits BOTH sources with a `source`
-- column — 'imdb' from the title.episode -> title.ratings join, 'tmdb' from
-- the episodes Parquet's own vote_average / vote_count — so
-- load_fact_episode_rating() is a plain read-and-upsert, unlike the movie and
-- series rating loaders which synthesise the 'tmdb' rows themselves.
CREATE TABLE IF NOT EXISTS fact_episode_rating (
    episode_id     INTEGER     NOT NULL,
    source         VARCHAR(16) NOT NULL,
    rating         NUMERIC(4,2),
    vote_count     INTEGER,
    ingestion_date DATE        NOT NULL,
    CONSTRAINT pk_fact_episode_rating PRIMARY KEY (episode_id, source),
    CONSTRAINT fk_fact_episode_rating_episode
        FOREIGN KEY (episode_id) REFERENCES dim_episode (episode_id),
    CONSTRAINT ck_fact_episode_rating_source CHECK (source IN ('imdb', 'tmdb'))
);
CREATE INDEX IF NOT EXISTS idx_fact_episode_rating_episode_id
    ON fact_episode_rating (episode_id);
CREATE INDEX IF NOT EXISTS idx_fact_episode_rating_source_rating
    ON fact_episode_rating (source, rating DESC);
