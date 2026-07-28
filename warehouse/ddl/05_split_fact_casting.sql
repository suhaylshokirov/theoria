-- Task 35 (Workstream A): split fact_casting into fact_cast + fact_crew.
-- These tables are added to 02_facts.sql for fresh bootstraps; this file
-- migrates the already-live warehouse. Safe to re-run (IF NOT EXISTS / IF EXISTS).

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

DROP TABLE IF EXISTS fact_casting;
