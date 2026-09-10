-- Fact tables for the Theoria star schema.
-- Run after 01_dimensions.sql; all tables use IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS fact_movie_metrics (
    movie_id     INTEGER        NOT NULL,
    date_id      INTEGER        NOT NULL,
    genre_id     INTEGER        NOT NULL,
    rating       NUMERIC(4, 2),
    vote_count   INTEGER,
    revenue      BIGINT,
    budget       BIGINT,
    popularity   NUMERIC(10, 4),
    ingestion_date DATE NOT NULL,
    CONSTRAINT pk_fact_movie_metrics PRIMARY KEY (movie_id, date_id, genre_id),
    CONSTRAINT fk_fmm_movie   FOREIGN KEY (movie_id)  REFERENCES dim_movie  (movie_id),
    CONSTRAINT fk_fmm_date    FOREIGN KEY (date_id)   REFERENCES dim_date   (date_id),
    CONSTRAINT fk_fmm_genre   FOREIGN KEY (genre_id)  REFERENCES dim_genre  (genre_id)
);

CREATE INDEX IF NOT EXISTS idx_fmm_movie_id  ON fact_movie_metrics (movie_id);
CREATE INDEX IF NOT EXISTS idx_fmm_date_id   ON fact_movie_metrics (date_id);
CREATE INDEX IF NOT EXISTS idx_fmm_genre_id  ON fact_movie_metrics (genre_id);
CREATE INDEX IF NOT EXISTS idx_fmm_ingestion_date ON fact_movie_metrics (ingestion_date);

-- fact_credit records every credit on every film, at the grain TMDB actually
-- publishes: one row per (movie, person, department, job). See
-- 08_person_credits.sql for the full rationale.
CREATE TABLE IF NOT EXISTS fact_credit (
    movie_id       INTEGER NOT NULL,
    person_id      INTEGER NOT NULL,
    department     TEXT    NOT NULL,
    job            TEXT    NOT NULL,
    character_name TEXT,
    ordering       SMALLINT,
    ingestion_date DATE    NOT NULL,
    CONSTRAINT pk_fact_credit PRIMARY KEY (movie_id, person_id, department, job),
    CONSTRAINT fk_fcredit_movie  FOREIGN KEY (movie_id)  REFERENCES dim_movie  (movie_id),
    CONSTRAINT fk_fcredit_person FOREIGN KEY (person_id) REFERENCES dim_person (person_id)
);

CREATE INDEX IF NOT EXISTS idx_fcredit_movie_id       ON fact_credit (movie_id);
CREATE INDEX IF NOT EXISTS idx_fcredit_person_id      ON fact_credit (person_id);
CREATE INDEX IF NOT EXISTS idx_fcredit_department     ON fact_credit (department);
CREATE INDEX IF NOT EXISTS idx_fcredit_ingestion_date ON fact_credit (ingestion_date);
CREATE INDEX IF NOT EXISTS idx_fcredit_person_dept    ON fact_credit (person_id, department);


-- fact_collaboration is derived in Gold rather than loaded from Silver — see
-- 09_collaboration.sql and etl/warehouse_loader/load_gold.py for why it lives
-- there and what "collaboration" is scoped to mean.
CREATE TABLE IF NOT EXISTS fact_collaboration (
    person_a_id    INTEGER  NOT NULL,
    person_b_id    INTEGER  NOT NULL,
    films_together INTEGER  NOT NULL,
    first_year     SMALLINT,
    last_year      SMALLINT,
    CONSTRAINT pk_fact_collaboration PRIMARY KEY (person_a_id, person_b_id),
    CONSTRAINT ck_fcollab_ordered CHECK (person_a_id < person_b_id),
    CONSTRAINT fk_fcollab_person_a FOREIGN KEY (person_a_id) REFERENCES dim_person (person_id),
    CONSTRAINT fk_fcollab_person_b FOREIGN KEY (person_b_id) REFERENCES dim_person (person_id)
);

CREATE INDEX IF NOT EXISTS idx_fcollab_person_a ON fact_collaboration (person_a_id);
CREATE INDEX IF NOT EXISTS idx_fcollab_person_b ON fact_collaboration (person_b_id);
CREATE INDEX IF NOT EXISTS idx_fcollab_a_rank ON fact_collaboration (person_a_id, films_together DESC);
CREATE INDEX IF NOT EXISTS idx_fcollab_b_rank ON fact_collaboration (person_b_id, films_together DESC);


-- fact_movie_rating (Phase 15) is the rating of record, at each film's true
-- grain — one row per (movie, source), unlike fact_movie_metrics.rating
-- which repeats once per genre. See 15_movie_ratings.sql for the full
-- rationale (why fact_ not bridge_, why no dim_rating_source).
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


-- fact_series_credit (Task 80) is the TV counterpart of fact_credit, with two
-- differences forced by TMDB's aggregate_credits shape: character_name is IN
-- the primary key (6.7% of cast hold >1 character per series, which a
-- fact_credit-shaped key would collapse on upsert), and it carries a measure,
-- episode_count — the first credit table here with a number to aggregate. See
-- 20_series_credits.sql for the full rationale and the measured figures.
CREATE TABLE IF NOT EXISTS fact_series_credit (
    series_id      INTEGER  NOT NULL,
    person_id      INTEGER  NOT NULL,
    department     TEXT     NOT NULL,
    job            TEXT     NOT NULL,
    character_name TEXT     NOT NULL DEFAULT '',
    episode_count  INTEGER,
    ordering       SMALLINT,
    ingestion_date DATE     NOT NULL,
    CONSTRAINT pk_fact_series_credit
        PRIMARY KEY (series_id, person_id, department, job, character_name),
    CONSTRAINT fk_fsc_series FOREIGN KEY (series_id) REFERENCES dim_series (series_id),
    CONSTRAINT fk_fsc_person FOREIGN KEY (person_id) REFERENCES dim_person (person_id)
);

CREATE INDEX IF NOT EXISTS idx_fsc_series_id      ON fact_series_credit (series_id);
CREATE INDEX IF NOT EXISTS idx_fsc_person_id      ON fact_series_credit (person_id);
CREATE INDEX IF NOT EXISTS idx_fsc_department     ON fact_series_credit (department);
CREATE INDEX IF NOT EXISTS idx_fsc_ingestion_date ON fact_series_credit (ingestion_date);
CREATE INDEX IF NOT EXISTS idx_fsc_person_dept    ON fact_series_credit (person_id, department);


-- fact_series_rating (Task 81) is the TV counterpart of fact_movie_rating:
-- one row per (series, source), IMDb as the rating of record and TMDB kept
-- for comparison. No fact_series_metrics — TV has no money measures, so that
-- table would be write-only. See 21_series_ratings.sql for the full rationale.
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
