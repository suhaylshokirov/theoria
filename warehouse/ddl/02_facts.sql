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

-- fact_cast and fact_crew are independent facts (one row per credited actor /
-- per credited director) rather than a single actor x director cross-join --
-- see docs/architecture.md for why: TMDB's credits endpoint never pairs an
-- actor with "their" director, so a joint fact table loses a movie's entire
-- cast whenever it has no director credit.
--
-- fact_crew currently models director credits only, mirroring dim_director
-- (which itself only contains people credited with job == "Director").
-- Modeling other crew roles (writers, producers, ...) would need a new
-- person-role dimension and is out of scope here.

CREATE TABLE IF NOT EXISTS fact_cast (
    movie_id    INTEGER      NOT NULL,
    actor_id    INTEGER      NOT NULL,
    role        TEXT,
    ordering    SMALLINT,
    ingestion_date DATE NOT NULL,
    CONSTRAINT pk_fact_cast PRIMARY KEY (movie_id, actor_id),
    CONSTRAINT fk_fcast_movie FOREIGN KEY (movie_id) REFERENCES dim_movie (movie_id),
    CONSTRAINT fk_fcast_actor FOREIGN KEY (actor_id) REFERENCES dim_actor (actor_id)
);

CREATE INDEX IF NOT EXISTS idx_fcast_movie_id      ON fact_cast (movie_id);
CREATE INDEX IF NOT EXISTS idx_fcast_actor_id      ON fact_cast (actor_id);
CREATE INDEX IF NOT EXISTS idx_fcast_ingestion_date ON fact_cast (ingestion_date);

CREATE TABLE IF NOT EXISTS fact_crew (
    movie_id    INTEGER      NOT NULL,
    director_id INTEGER      NOT NULL,
    ingestion_date DATE NOT NULL,
    CONSTRAINT pk_fact_crew PRIMARY KEY (movie_id, director_id),
    CONSTRAINT fk_fcrew_movie    FOREIGN KEY (movie_id)    REFERENCES dim_movie    (movie_id),
    CONSTRAINT fk_fcrew_director FOREIGN KEY (director_id) REFERENCES dim_director (director_id)
);

CREATE INDEX IF NOT EXISTS idx_fcrew_movie_id      ON fact_crew (movie_id);
CREATE INDEX IF NOT EXISTS idx_fcrew_director_id   ON fact_crew (director_id);
CREATE INDEX IF NOT EXISTS idx_fcrew_ingestion_date ON fact_crew (ingestion_date);
