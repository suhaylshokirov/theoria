-- Task 61 (Phase 14): dim_country + dim_language and their bridges.
--
-- Phase 13's bridge pattern (13_companies.sql), applied twice to the two
-- remaining link tables Task 57 already wrote to Silver. The one structural
-- difference from dim_company/dim_collection: country and language each
-- already have a stable, short, URL-safe natural key straight from TMDB
-- (ISO 3166-1 alpha-2 / ISO 639-1), so neither dimension needs a surrogate
-- id or a slug — the code *is* the identifier.
--
-- bridge_movie_country carries `relation` in its PK, not just its payload:
-- Task 57 found that `origin_country` and `production_countries` disagree on
-- ~23% of films, so a (movie_id, country_code) grain would silently pick one
-- relationship over the other on every disagreement. Both bridges are
-- factless — no measure, just recording that a relationship exists — hence
-- `bridge_` rather than `fact_`, per 13_companies.sql's naming rationale.
--
-- These definitions are also in 01_dimensions.sql for fresh bootstraps.
-- Safe to re-run (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS dim_country (
    country_code VARCHAR(10)  NOT NULL,
    name         TEXT         NOT NULL,
    CONSTRAINT pk_dim_country PRIMARY KEY (country_code)
);

CREATE TABLE IF NOT EXISTS dim_language (
    language_code VARCHAR(10) NOT NULL,
    name          TEXT        NOT NULL,
    english_name  TEXT,
    CONSTRAINT pk_dim_language PRIMARY KEY (language_code)
);

-- relation is part of the PK: origin and production are different claims
-- about the same film and must both be able to exist for one country_code.
CREATE TABLE IF NOT EXISTS bridge_movie_country (
    movie_id       INTEGER      NOT NULL,
    country_code   VARCHAR(10)  NOT NULL,
    relation       VARCHAR(20)  NOT NULL,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_bridge_movie_country PRIMARY KEY (movie_id, country_code, relation),
    CONSTRAINT fk_bridge_movie_country_movie
        FOREIGN KEY (movie_id) REFERENCES dim_movie (movie_id),
    CONSTRAINT fk_bridge_movie_country_country
        FOREIGN KEY (country_code) REFERENCES dim_country (country_code)
);
CREATE INDEX IF NOT EXISTS idx_bridge_movie_country_movie_id ON bridge_movie_country (movie_id);
CREATE INDEX IF NOT EXISTS idx_bridge_movie_country_country_code ON bridge_movie_country (country_code);

CREATE TABLE IF NOT EXISTS bridge_movie_language (
    movie_id       INTEGER      NOT NULL,
    language_code  VARCHAR(10)  NOT NULL,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_bridge_movie_language PRIMARY KEY (movie_id, language_code),
    CONSTRAINT fk_bridge_movie_language_movie
        FOREIGN KEY (movie_id) REFERENCES dim_movie (movie_id),
    CONSTRAINT fk_bridge_movie_language_language
        FOREIGN KEY (language_code) REFERENCES dim_language (language_code)
);
CREATE INDEX IF NOT EXISTS idx_bridge_movie_language_movie_id ON bridge_movie_language (movie_id);
CREATE INDEX IF NOT EXISTS idx_bridge_movie_language_language_code ON bridge_movie_language (language_code);
