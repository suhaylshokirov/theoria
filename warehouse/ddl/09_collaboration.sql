-- Task 49 (Phase 10): fact_collaboration.
--
-- The first table in the warehouse whose source is the Gold layer rather than
-- Silver. Until now Gold was written on every pipeline run and read by nothing;
-- this is the dataset that justifies the layer — a quadratic expansion over
-- every film, too expensive to compute per request and shaped for a read the
-- star schema can't serve without a self-join each time.
--
-- Grain: one row per unordered pair of people, with person_a_id < person_b_id.
-- Enforced by a CHECK rather than left to the loader, because "a pair appears
-- once, not twice in mirror image" is a property of the table, not of whoever
-- happens to write to it.
--
-- Scope: pairs among *key* credits only (top-billed cast + principal craft
-- roles). Pairing every credit on every film yields 33.1M rows on this corpus
-- and claims a caterer collaborated with a stunt double. See
-- etl/gold/build_gold_datasets.py::_build_collaboration_edges.
--
-- Safe to re-run (IF NOT EXISTS throughout).

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

-- Every read of this table is "who did this person work with most", so the
-- ranking column is indexed descending alongside each side of the pair.
CREATE INDEX IF NOT EXISTS idx_fcollab_a_rank ON fact_collaboration (person_a_id, films_together DESC);
CREATE INDEX IF NOT EXISTS idx_fcollab_b_rank ON fact_collaboration (person_b_id, films_together DESC);
