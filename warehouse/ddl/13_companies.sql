-- Task 58 (Phase 13): dim_company + bridge_movie_company.
--
-- A production company is a genuinely new *entity* with its own identity,
-- artwork and page — unlike dim_collection (one collection per film, so it
-- flattened to a column on dim_movie), a film has 2.81 companies on average.
-- That's a true many-to-many, modelled here for the first time in this
-- warehouse: genres are still handled by fanning fact_movie_metrics out to
-- one row per genre, the wart every analytics query has to SELECT DISTINCT
-- around. A bridge table avoids repeating that wart for companies.
--
-- Named bridge_ rather than fact_ on purpose: this table carries no measure,
-- only the existence of a relationship (a "factless fact table" in the
-- dimensional-modelling literature). Reserving fact_ for tables that actually
-- have something to sum keeps the schema self-describing at a glance.
--
-- These definitions are also in 01_dimensions.sql for fresh bootstraps.
-- Safe to re-run (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS dim_company (
    company_id     INTEGER      NOT NULL,
    name           TEXT         NOT NULL,
    logo_path      TEXT,
    origin_country VARCHAR(10),
    slug           VARCHAR(300),
    CONSTRAINT pk_dim_company PRIMARY KEY (company_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_company_slug ON dim_company (slug);

-- Both FKs indexed: the join runs in both directions (a film's studios, and
-- a studio's films).
CREATE TABLE IF NOT EXISTS bridge_movie_company (
    movie_id       INTEGER      NOT NULL,
    company_id     INTEGER      NOT NULL,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_bridge_movie_company PRIMARY KEY (movie_id, company_id),
    CONSTRAINT fk_bridge_movie_company_movie
        FOREIGN KEY (movie_id) REFERENCES dim_movie (movie_id),
    CONSTRAINT fk_bridge_movie_company_company
        FOREIGN KEY (company_id) REFERENCES dim_company (company_id)
);
CREATE INDEX IF NOT EXISTS idx_bridge_movie_company_movie_id ON bridge_movie_company (movie_id);
CREATE INDEX IF NOT EXISTS idx_bridge_movie_company_company_id ON bridge_movie_company (company_id);
