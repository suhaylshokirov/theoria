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
    CONSTRAINT pk_fact_movie_metrics PRIMARY KEY (movie_id, date_id, genre_id),
    CONSTRAINT fk_fmm_movie   FOREIGN KEY (movie_id)  REFERENCES dim_movie  (movie_id),
    CONSTRAINT fk_fmm_date    FOREIGN KEY (date_id)   REFERENCES dim_date   (date_id),
    CONSTRAINT fk_fmm_genre   FOREIGN KEY (genre_id)  REFERENCES dim_genre  (genre_id)
);

CREATE INDEX IF NOT EXISTS idx_fmm_movie_id  ON fact_movie_metrics (movie_id);
CREATE INDEX IF NOT EXISTS idx_fmm_date_id   ON fact_movie_metrics (date_id);
CREATE INDEX IF NOT EXISTS idx_fmm_genre_id  ON fact_movie_metrics (genre_id);

CREATE TABLE IF NOT EXISTS fact_casting (
    movie_id    INTEGER      NOT NULL,
    actor_id    INTEGER      NOT NULL,
    director_id INTEGER      NOT NULL,
    role        TEXT,
    ordering    SMALLINT,
    CONSTRAINT pk_fact_casting PRIMARY KEY (movie_id, actor_id, director_id),
    CONSTRAINT fk_fc_movie    FOREIGN KEY (movie_id)    REFERENCES dim_movie    (movie_id),
    CONSTRAINT fk_fc_actor    FOREIGN KEY (actor_id)    REFERENCES dim_actor    (actor_id),
    CONSTRAINT fk_fc_director FOREIGN KEY (director_id) REFERENCES dim_director (director_id)
);

CREATE INDEX IF NOT EXISTS idx_fc_movie_id    ON fact_casting (movie_id);
CREATE INDEX IF NOT EXISTS idx_fc_actor_id    ON fact_casting (actor_id);
CREATE INDEX IF NOT EXISTS idx_fc_director_id ON fact_casting (director_id);
