-- Task 93 (Translations): four tables holding text TMDB publishes per language.
--
--   movie_translation   (movie_id,     lang) -> title, overview, tagline
--   person_translation  (person_id,    lang) -> biography
--   genre_translation   (genre_id,     lang) -> genre_name
--   country_translation (country_code, lang) -> name
--
-- Naming — none of the three warehouse prefixes fits, the same call
-- 17_person_details.sql made for person_alias:
--   - Not fact_. No measure: every column is descriptive text.
--   - Not bridge_. A bridge ties two dimensions together; here the "second
--     side" is a language code, which is not a dimension and never will be.
--   - Not dim_. These do not describe a business entity of their own — each
--     one hangs repeating text off exactly one existing dimension.
--   So: <entity>_translation, keyed (entity id, lang), read only by joining
--   down from the parent dimension.
--
-- One ROW per language, not title_ru / title_uz columns. A column per language
-- is the same first-normal-form break that also_known_as was split out to
-- avoid, and it would make a fourth language a schema change. Here a fourth
-- language is a new value in `lang` and no DDL at all — which is also why
-- `lang` carries no CHECK constraint; the domain is enforced by the loader's
-- Silver filter and asserted in the warehouse data-quality checks.
--
-- `lang` is the bare ISO-639-1 code ('ru', 'uz'), not 'ru-RU': the site serves
-- one variant per language. Silver keeps only the languages the site ships.
--
-- Indexes: every FK column is the *leading* column of its table's primary key,
-- so the PK index already serves the join from the parent — a separate FK index
-- would be the prefix-duplicate 25_drop_unused_indexes.sql just removed.
--
-- Load strategy differs by table (see etl/warehouse_loader/load_dimensions.py):
--   movie_translation / person_translation  REPLACE by parent id, not upsert —
--     a film's translation set can shrink when TMDB withdraws one, and an
--     upsert would strand a stale Russian overview forever with no failing check.
--   genre_translation / country_translation  plain upsert — small fixed
--     vocabularies that only ever gain or rename entries.
--
-- Also folded into 01_dimensions.sql for fresh bootstraps. Safe to re-run.
-- Apply to Neon and the local replica BEFORE deploying code that reads these.

CREATE TABLE IF NOT EXISTS movie_translation (
    movie_id       INTEGER      NOT NULL,
    lang           VARCHAR(5)   NOT NULL,
    title          TEXT,
    overview       TEXT,
    tagline        TEXT,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_movie_translation PRIMARY KEY (movie_id, lang),
    CONSTRAINT fk_movie_translation_movie
        FOREIGN KEY (movie_id) REFERENCES dim_movie (movie_id)
);

CREATE TABLE IF NOT EXISTS person_translation (
    person_id      INTEGER      NOT NULL,
    lang           VARCHAR(5)   NOT NULL,
    biography      TEXT,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_person_translation PRIMARY KEY (person_id, lang),
    CONSTRAINT fk_person_translation_person
        FOREIGN KEY (person_id) REFERENCES dim_person (person_id)
);

CREATE TABLE IF NOT EXISTS genre_translation (
    genre_id       INTEGER      NOT NULL,
    lang           VARCHAR(5)   NOT NULL,
    genre_name     TEXT         NOT NULL,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_genre_translation PRIMARY KEY (genre_id, lang),
    CONSTRAINT fk_genre_translation_genre
        FOREIGN KEY (genre_id) REFERENCES dim_genre (genre_id)
);

CREATE TABLE IF NOT EXISTS country_translation (
    country_code   VARCHAR(10)  NOT NULL,
    lang           VARCHAR(5)   NOT NULL,
    name           TEXT         NOT NULL,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_country_translation PRIMARY KEY (country_code, lang),
    CONSTRAINT fk_country_translation_country
        FOREIGN KEY (country_code) REFERENCES dim_country (country_code)
);
