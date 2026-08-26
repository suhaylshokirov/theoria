-- Task 66-67 (Phase 15): fact_movie_rating — the rating of record, at each
-- film's true grain (one row per film per source), replacing
-- fact_movie_metrics.rating/vote_count as what the app reads.
--
-- fact_movie_metrics is at (movie_id, date_id, genre_id) grain, so a
-- multi-genre film's rating is stored once per genre — the reason every
-- reader of it has to SELECT DISTINCT movie_id first. A rating has nothing
-- to do with a film's genres; fact_movie_rating puts it at its true grain
-- (one row per movie per source), so that dedupe guard is unnecessary here
-- by construction rather than something every caller must remember to add.
--
-- Two things considered and rejected, both worth recording:
--   - No dim_rating_source table. A two-row dimension whose only attributes
--     (icon, label, outbound URL template) are pure presentation would be
--     over-modelling; a CHECK constraint enforces the vocabulary and Django
--     owns the display metadata.
--   - fact_ and not bridge_. This table carries a measure (rating,
--     vote_count), so it earns the fact_ prefix under the naming rule
--     13_companies.sql established for factless relationship tables.
--
-- Both sources are loaded here, not just IMDb: TMDB's vote_average/
-- vote_count (already in silver/movies/movies.parquet) get a source='tmdb'
-- row alongside IMDb's source='imdb' row, so this table becomes the single
-- answer to "what is this film rated" rather than a second partial answer
-- sitting next to fact_movie_metrics.
--
-- Also folded into 02_facts.sql for fresh bootstraps (it's a fact, so 02,
-- not 01 — unlike 13_companies.sql, which carried a dimension).
-- Safe to re-run (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS fact_movie_rating (
    movie_id       INTEGER     NOT NULL,
    source         VARCHAR(16) NOT NULL,
    rating         NUMERIC(4,2),
    vote_count     INTEGER,
    ingestion_date DATE        NOT NULL,
    CONSTRAINT pk_fact_movie_rating PRIMARY KEY (movie_id, source),
    CONSTRAINT fk_fact_movie_rating_movie
        FOREIGN KEY (movie_id) REFERENCES dim_movie (movie_id),
    CONSTRAINT ck_fact_movie_rating_source CHECK (source IN ('imdb', 'tmdb'))
);
CREATE INDEX IF NOT EXISTS idx_fact_movie_rating_movie_id ON fact_movie_rating (movie_id);
CREATE INDEX IF NOT EXISTS idx_fact_movie_rating_source_rating
    ON fact_movie_rating (source, rating DESC);
