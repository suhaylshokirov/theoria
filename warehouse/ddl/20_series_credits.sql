-- Task 80 (TV Shows): fact_series_credit — every person who worked on a show,
-- what they did, and on how many episodes.
--
-- The TV counterpart of fact_credit (08_person_credits.sql). Also folded into
-- 02_facts.sql for fresh bootstraps. Safe to re-run (IF NOT EXISTS throughout).
--
-- ---------------------------------------------------------------------------
-- Two differences from fact_credit, both forced by aggregate_credits' shape
-- and measured on the real corpus before this table was designed:
--
-- 1. character_name is IN THE PRIMARY KEY (fact_credit's is not).
--      6.7% of cast (746 / 11,213 sampled) hold more than one character in a
--      single series — one actor, several roles, each with its own episode
--      count. A key of (series_id, person_id, department, job) would collapse
--      every one of those to a single row on upsert: Tatiana Maslany in
--      Orphan Black would keep exactly one clone. Widening the key with
--      character_name keeps every distinct role.
--      character_name is NOT NULL DEFAULT '' because Postgres forbids a
--      nullable column in a primary key; the loader coalesces crew's absent
--      character to ''. Crew needs no widening — 0 exact
--      (person, department, job) collisions were measured — but it shares the
--      table and therefore the key.
--
-- 2. It carries a MEASURE: episode_count, straight off aggregate_credits.
--      That makes fact_series_credit the first credit table in this warehouse
--      with a number worth aggregating — fact_credit is factless in all but
--      name.
-- ---------------------------------------------------------------------------

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
