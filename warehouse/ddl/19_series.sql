-- Task 79 (TV Shows): dim_series, dim_network, and the five series bridges.
-- The series half of the star schema — the structural mirror of dim_movie plus
-- its company/country/language bridges (13_companies.sql, 14_countries_languages.sql),
-- with two differences noted below.
--
-- Also folded into 01_dimensions.sql for fresh bootstraps (dims and their
-- bridges both live in 01, the same as the movie bridges). Safe to re-run
-- (IF NOT EXISTS throughout).
--
-- ---------------------------------------------------------------------------
-- dim_network is its OWN dimension, not a `kind` column on dim_company.
--   Rejected alternative: add `kind IN ('company','network')` to dim_company
--   and reuse it. TMDB keys networks in a SEPARATE id namespace from
--   companies — network 49 is HBO, company 49 is Universal Studios — so a
--   shared table would collide on the primary key. A network is also a
--   genuinely different entity to a reader (who aired it, not who made it),
--   with its own page in Task 87. Two namespaces, two tables.
--
-- dim_series carries a `slug` like dim_movie, assigned by assign_slugs() in
--   load_dimensions.py over the whole table (a URL slug's collision suffix
--   needs the full-table view). dim_network gets one too — it has a page.
--   Countries and languages still need no slug: the ISO code is the identifier.
--
-- The bridges are factless (no measure, only that a relationship exists),
-- hence bridge_ not fact_ — the 13_companies.sql naming rule. All use plain
-- upsert: a show's genre / studio / network set does not shrink the way a
-- film's video set does (18_movie_videos.sql), so no _replace_by_parent here.
--
-- bridge_series_country carries `relation` in its PK for the same reason
-- bridge_movie_country does: origin and production are different claims about
-- the same show and must both be storable for one country_code.

CREATE TABLE IF NOT EXISTS dim_series (
    series_id          INTEGER      NOT NULL,
    name               TEXT         NOT NULL,
    original_name      TEXT,
    first_air_date     DATE,
    last_air_date      DATE,
    number_of_seasons  INTEGER,
    number_of_episodes INTEGER,
    status             TEXT,
    type               TEXT,
    in_production      BOOLEAN,
    original_language  VARCHAR(10),
    overview           TEXT,
    tagline            TEXT,
    poster_path        TEXT,
    backdrop_path      TEXT,
    homepage           TEXT,
    imdb_id            VARCHAR(16),
    slug               VARCHAR(300),
    CONSTRAINT pk_dim_series PRIMARY KEY (series_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_series_slug ON dim_series (slug);
-- Non-unique: the Phase 15 IMDb join key, same posture as dim_person.imdb_id
-- (17_person_details.sql) — indexed for the lookup, not asserted unique.
CREATE INDEX IF NOT EXISTS idx_dim_series_imdb_id ON dim_series (imdb_id);

CREATE TABLE IF NOT EXISTS dim_network (
    network_id     INTEGER      NOT NULL,
    name           TEXT         NOT NULL,
    logo_path      TEXT,
    origin_country VARCHAR(10),
    slug           VARCHAR(300),
    CONSTRAINT pk_dim_network PRIMARY KEY (network_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_network_slug ON dim_network (slug);

CREATE TABLE IF NOT EXISTS bridge_series_genre (
    series_id      INTEGER NOT NULL,
    genre_id       INTEGER NOT NULL,
    ingestion_date DATE    NOT NULL,
    CONSTRAINT pk_bridge_series_genre PRIMARY KEY (series_id, genre_id),
    CONSTRAINT fk_bridge_series_genre_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id),
    CONSTRAINT fk_bridge_series_genre_genre
        FOREIGN KEY (genre_id) REFERENCES dim_genre (genre_id)
);
CREATE INDEX IF NOT EXISTS idx_bridge_series_genre_series_id ON bridge_series_genre (series_id);
CREATE INDEX IF NOT EXISTS idx_bridge_series_genre_genre_id ON bridge_series_genre (genre_id);

CREATE TABLE IF NOT EXISTS bridge_series_company (
    series_id      INTEGER NOT NULL,
    company_id     INTEGER NOT NULL,
    ingestion_date DATE    NOT NULL,
    CONSTRAINT pk_bridge_series_company PRIMARY KEY (series_id, company_id),
    CONSTRAINT fk_bridge_series_company_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id),
    CONSTRAINT fk_bridge_series_company_company
        FOREIGN KEY (company_id) REFERENCES dim_company (company_id)
);
CREATE INDEX IF NOT EXISTS idx_bridge_series_company_series_id ON bridge_series_company (series_id);
CREATE INDEX IF NOT EXISTS idx_bridge_series_company_company_id ON bridge_series_company (company_id);

CREATE TABLE IF NOT EXISTS bridge_series_country (
    series_id      INTEGER     NOT NULL,
    country_code   VARCHAR(10) NOT NULL,
    relation       VARCHAR(20) NOT NULL,
    ingestion_date DATE        NOT NULL,
    CONSTRAINT pk_bridge_series_country PRIMARY KEY (series_id, country_code, relation),
    CONSTRAINT fk_bridge_series_country_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id),
    CONSTRAINT fk_bridge_series_country_country
        FOREIGN KEY (country_code) REFERENCES dim_country (country_code)
);
CREATE INDEX IF NOT EXISTS idx_bridge_series_country_series_id ON bridge_series_country (series_id);
CREATE INDEX IF NOT EXISTS idx_bridge_series_country_country_code ON bridge_series_country (country_code);

CREATE TABLE IF NOT EXISTS bridge_series_language (
    series_id      INTEGER     NOT NULL,
    language_code  VARCHAR(10) NOT NULL,
    ingestion_date DATE        NOT NULL,
    CONSTRAINT pk_bridge_series_language PRIMARY KEY (series_id, language_code),
    CONSTRAINT fk_bridge_series_language_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id),
    CONSTRAINT fk_bridge_series_language_language
        FOREIGN KEY (language_code) REFERENCES dim_language (language_code)
);
CREATE INDEX IF NOT EXISTS idx_bridge_series_language_series_id ON bridge_series_language (series_id);
CREATE INDEX IF NOT EXISTS idx_bridge_series_language_language_code ON bridge_series_language (language_code);

CREATE TABLE IF NOT EXISTS bridge_series_network (
    series_id      INTEGER NOT NULL,
    network_id     INTEGER NOT NULL,
    ingestion_date DATE    NOT NULL,
    CONSTRAINT pk_bridge_series_network PRIMARY KEY (series_id, network_id),
    CONSTRAINT fk_bridge_series_network_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id),
    CONSTRAINT fk_bridge_series_network_network
        FOREIGN KEY (network_id) REFERENCES dim_network (network_id)
);
CREATE INDEX IF NOT EXISTS idx_bridge_series_network_series_id ON bridge_series_network (series_id);
CREATE INDEX IF NOT EXISTS idx_bridge_series_network_network_id ON bridge_series_network (network_id);
