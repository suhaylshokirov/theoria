-- Task 48 (Phase 10): dim_person + fact_credit.
--
-- Replaces the dim_actor/dim_director split, in which a person's identity was
-- decided by the credit that happened to introduce them: cast members became
-- actors, and crew members existed at all only if job == 'Director'. Everyone
-- else — editors, composers, cinematographers, writers, production designers —
-- was ingested into Bronze and then discarded at the loader.
--
-- These tables are added to 01_dimensions.sql / 02_facts.sql for fresh
-- bootstraps; this file migrates the already-live warehouse.
-- Safe to re-run (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS dim_person (
    person_id            INTEGER      NOT NULL,
    name                 TEXT         NOT NULL,
    gender               SMALLINT,
    popularity           NUMERIC(10, 4),
    profile_path         TEXT,
    known_for_department TEXT,
    slug                 VARCHAR(300),
    CONSTRAINT pk_dim_person PRIMARY KEY (person_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_person_slug ON dim_person (slug);

-- Grain: one row per (movie, person, department, job).
--
-- This mirrors the Silver credits-bridge grain exactly. A person legitimately
-- holds several credits on one film — a director who also wrote and produced it
-- is three rows, not one — and the PK has to say so. Declaring a coarser key
-- than the data has is precisely how Task 40's silent director loss happened;
-- here the grain is chosen before the first load rather than discovered after.
--
-- Cast credits are stored as department='Acting', job='Actor', with the part
-- played in `character_name` and TMDB billing order in `ordering`. Crew credits
-- carry their own department/job and leave both null. (`character` alone is a
-- Postgres keyword and reads ambiguously next to `job`, hence the suffix.)
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

-- Serves the person page's "every credit, newest first" read without touching
-- the movie table, and the department grouping within it.
CREATE INDEX IF NOT EXISTS idx_fcredit_person_dept ON fact_credit (person_id, department);
