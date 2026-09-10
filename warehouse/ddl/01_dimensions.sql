-- Dimension tables for the Theoria star schema — the current schema.
-- Run once to bootstrap the warehouse; all tables use IF NOT EXISTS so re-runs are safe.
--
-- Files 04-11 are historical migrations that brought an already-live warehouse
-- to this shape. A fresh database needs only 01-03; running the migrations on
-- top is harmless but unnecessary, and 11 would drop tables 01 no longer creates.

-- Declared before dim_movie because dim_movie carries an FK to it. Roughly half
-- the catalog belongs to no collection, so that FK is nullable by design.
CREATE TABLE IF NOT EXISTS dim_collection (
    collection_id INTEGER      NOT NULL,
    name          TEXT         NOT NULL,
    poster_path   TEXT,
    slug          VARCHAR(300),
    CONSTRAINT pk_dim_collection PRIMARY KEY (collection_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_collection_slug ON dim_collection (slug);

CREATE TABLE IF NOT EXISTS dim_movie (
    movie_id         INTEGER      NOT NULL,
    title            TEXT         NOT NULL,
    release_date     DATE,
    runtime          INTEGER,
    budget           BIGINT,
    revenue          BIGINT,
    original_language VARCHAR(10),
    status           VARCHAR(50),
    overview         TEXT,
    tagline          TEXT,
    poster_path      TEXT,
    backdrop_path    TEXT,
    slug             VARCHAR(300),
    collection_id    INTEGER,
    imdb_id          VARCHAR(20),
    original_title   TEXT,
    homepage         TEXT,
    CONSTRAINT pk_dim_movie PRIMARY KEY (movie_id),
    CONSTRAINT fk_dim_movie_collection FOREIGN KEY (collection_id) REFERENCES dim_collection (collection_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_movie_slug ON dim_movie (slug);
CREATE INDEX IF NOT EXISTS idx_dim_movie_collection_id ON dim_movie (collection_id);
CREATE INDEX IF NOT EXISTS idx_dim_movie_imdb_id ON dim_movie (imdb_id);

-- Every person holding any credit, in any department. What they did on a given
-- film lives in fact_credit, not in which table they land in. See
-- 08_person_credits.sql for why this replaced the dim_actor/dim_director split.
CREATE TABLE IF NOT EXISTS dim_person (
    person_id            INTEGER      NOT NULL,
    name                 TEXT         NOT NULL,
    gender               SMALLINT,
    popularity           NUMERIC(10, 4),
    profile_path         TEXT,
    known_for_department TEXT,
    slug                 VARCHAR(300),
    -- Task 72: from GET /person/{id}. All nullable and sparse even among
    -- people with a photo — see 17_person_details.sql.
    biography            TEXT,
    birthday             DATE,
    deathday             DATE,
    place_of_birth       TEXT,
    homepage             TEXT,
    imdb_id              VARCHAR(20),
    CONSTRAINT pk_dim_person PRIMARY KEY (person_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_person_slug ON dim_person (slug);
CREATE INDEX IF NOT EXISTS idx_dim_person_imdb_id ON dim_person (imdb_id);

-- also_known_as is a list, so it can't be a column on dim_person without
-- breaking 1NF. Named plainly — not fact_ (no measure), not bridge_ (attaches
-- repeating text to one dimension, doesn't join two). See 17_person_details.sql.
CREATE TABLE IF NOT EXISTS person_alias (
    person_id      INTEGER      NOT NULL,
    alias          TEXT         NOT NULL,
    ordering       INTEGER,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_person_alias PRIMARY KEY (person_id, alias),
    CONSTRAINT fk_person_alias_person
        FOREIGN KEY (person_id) REFERENCES dim_person (person_id)
);
CREATE INDEX IF NOT EXISTS idx_person_alias_person_id ON person_alias (person_id);

CREATE TABLE IF NOT EXISTS dim_genre (
    genre_id    INTEGER  NOT NULL,
    genre_name  TEXT     NOT NULL,
    CONSTRAINT pk_dim_genre PRIMARY KEY (genre_id)
);

-- A film has 2.81 companies on average — a true many-to-many, unlike
-- dim_collection above (one per film, so it fit as a column). See
-- 13_companies.sql for the bridge_ vs fact_ naming rationale.
CREATE TABLE IF NOT EXISTS dim_company (
    company_id          INTEGER      NOT NULL,
    name                TEXT         NOT NULL,
    logo_path           TEXT,
    origin_country      VARCHAR(10),
    slug                VARCHAR(300),
    -- Task 65: from GET /company/{id}. parent_company_id is a soft, unenforced
    -- reference (a holding-company parent often has no dim_company row) —
    -- see 16_company_details.sql.
    description         TEXT,
    headquarters        TEXT,
    homepage            TEXT,
    parent_company_id   INTEGER,
    parent_company_name TEXT,
    CONSTRAINT pk_dim_company PRIMARY KEY (company_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_company_slug ON dim_company (slug);

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

-- Country and language already have a stable, short, URL-safe natural key
-- from TMDB (ISO 3166-1 alpha-2 / ISO 639-1), so neither dimension needs a
-- surrogate id or a slug. See 14_countries_languages.sql for the bridges'
-- relation-in-the-PK rationale.
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

-- Task 74: a film's trailers and clips. Neither fact_ (no measure — `size` is a
-- resolution) nor bridge_ (no dim_video to join to); it's a multi-valued
-- attribute of dim_movie. PK is TMDB's video_id, not `key` (which is only
-- unique within a site). Loaded by REPLACE, not upsert — see 18_movie_videos.sql.
CREATE TABLE IF NOT EXISTS dim_movie_video (
    movie_id       INTEGER      NOT NULL,
    video_id       VARCHAR(24)  NOT NULL,
    name           TEXT,
    key            TEXT,
    site           TEXT,
    type           TEXT,
    official       BOOLEAN,
    size           INTEGER,
    iso_639_1      VARCHAR(8),
    iso_3166_1     VARCHAR(8),
    published_at   TIMESTAMPTZ,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_dim_movie_video PRIMARY KEY (movie_id, video_id),
    CONSTRAINT fk_dim_movie_video_movie
        FOREIGN KEY (movie_id) REFERENCES dim_movie (movie_id)
);
CREATE INDEX IF NOT EXISTS idx_dim_movie_video_movie_type
    ON dim_movie_video (movie_id, type);

-- Task 79: the TV series half of the star schema — dim_series mirrors dim_movie,
-- and its five bridges mirror the movie company/country/language bridges.
-- dim_network is its own dimension (TMDB keys networks in a separate id
-- namespace from companies). dim_series/dim_network carry a slug (a page each);
-- countries/languages still don't (the ISO code is the identifier). All bridges
-- are factless and plain-upsert. See 19_series.sql for the full rationale.
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

-- Task 84: the episode grain. An episode is a dimension (its descriptive
-- attributes) plus a rating fact (02_facts.sql) — the dim_movie + fact_movie_rating
-- split, not one fact_episode. episode_id / season_id are TMDB's own global ids.
-- dim_episode is loaded by _replace_by_parent keyed on series_id (TMDB renumbers
-- and withdraws episodes, so a series' episode set must be able to shrink — the
-- Task 74 dim_movie_video pattern, second use); dim_season stays on plain upsert.
-- Full rationale in 22_episodes.sql.
CREATE TABLE IF NOT EXISTS dim_season (
    season_id     INTEGER NOT NULL,
    series_id     INTEGER NOT NULL,
    season_number SMALLINT,
    name          TEXT,
    air_date      DATE,
    episode_count INTEGER,
    overview      TEXT,
    poster_path   TEXT,
    CONSTRAINT pk_dim_season PRIMARY KEY (season_id),
    CONSTRAINT fk_dim_season_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_season_series_number
    ON dim_season (series_id, season_number);
CREATE INDEX IF NOT EXISTS idx_dim_season_series_id ON dim_season (series_id);

CREATE TABLE IF NOT EXISTS dim_episode (
    episode_id      INTEGER NOT NULL,
    series_id       INTEGER NOT NULL,
    season_number   SMALLINT,
    episode_number  SMALLINT,
    name            TEXT,
    air_date        DATE,
    runtime         INTEGER,
    overview        TEXT,
    still_path      TEXT,
    episode_type    TEXT,
    production_code TEXT,
    imdb_id         VARCHAR(16),
    ingestion_date  DATE    NOT NULL,
    CONSTRAINT pk_dim_episode PRIMARY KEY (episode_id),
    CONSTRAINT fk_dim_episode_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_episode_natural_key
    ON dim_episode (series_id, season_number, episode_number);
CREATE INDEX IF NOT EXISTS idx_dim_episode_series_order
    ON dim_episode (series_id, season_number, episode_number);
CREATE INDEX IF NOT EXISTS idx_dim_episode_imdb_id ON dim_episode (imdb_id);

-- dim_date is a pre-populated calendar table; rows are generated by the loader.
CREATE TABLE IF NOT EXISTS dim_date (
    date_id     INTEGER  NOT NULL,   -- surrogate key: YYYYMMDD integer
    full_date   DATE     NOT NULL,
    year        SMALLINT NOT NULL,
    month       SMALLINT NOT NULL,
    day         SMALLINT NOT NULL,
    decade      SMALLINT NOT NULL,   -- e.g. 1990, 2000, 2010
    CONSTRAINT pk_dim_date PRIMARY KEY (date_id)
);
