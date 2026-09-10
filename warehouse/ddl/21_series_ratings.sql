-- Task 81 (TV Shows): fact_series_rating — a show's rating of record, from
-- IMDb, with TMDB kept beside it for comparison. The Phase 15 contract
-- (15_movie_ratings.sql), extended to television.
--
-- A direct copy of fact_movie_rating with series_id in place of movie_id:
--   - one row per (series, source), so AVG(rating) needs no de-dup guard;
--   - CHECK (source IN ('imdb','tmdb')) enforces the vocabulary, Django owns
--     the display metadata (no dim_rating_source — same call as for films);
--   - fact_ not bridge_, because it carries measures (rating, vote_count).
--
-- IMDb's title.ratings.tsv.gz — already ingested nightly for films — was
-- measured to contain series rows (tt0903747 -> 9.5 / 2,671,907), so this
-- needs NO new Bronze source: transform_imdb_ratings.py gains a third join
-- (snapshot x silver/series on imdb_id) and writes silver/series_ratings.
--
-- Deliberately NO fact_series_metrics. fact_movie_metrics exists for
-- revenue/budget by genre; its rating/vote_count columns have had no readers
-- since Task 69. TV has no money measures and already has a real genre
-- bridge, so a fact_series_metrics would be born write-only. Omitted on
-- purpose.
--
-- Also folded into 02_facts.sql for fresh bootstraps (it's a fact, so 02).
-- Safe to re-run (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS fact_series_rating (
    series_id      INTEGER     NOT NULL,
    source         VARCHAR(16) NOT NULL,
    rating         NUMERIC(4,2),
    vote_count     INTEGER,
    ingestion_date DATE        NOT NULL,
    CONSTRAINT pk_fact_series_rating PRIMARY KEY (series_id, source),
    CONSTRAINT fk_fact_series_rating_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id),
    CONSTRAINT ck_fact_series_rating_source CHECK (source IN ('imdb', 'tmdb'))
);
CREATE INDEX IF NOT EXISTS idx_fact_series_rating_series_id ON fact_series_rating (series_id);
CREATE INDEX IF NOT EXISTS idx_fact_series_rating_source_rating
    ON fact_series_rating (source, rating DESC);
