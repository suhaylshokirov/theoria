-- Task 109 (Translations for TV): the fifth translation table.
--
--   series_translation (series_id, lang) -> name, overview, tagline
--
-- Same shape and same reasoning as movie_translation (27_translations.sql —
-- read its header for why this is <entity>_translation rather than dim_/fact_/
-- bridge_, why one ROW per language, why `lang` carries no CHECK, and why no
-- separate FK index). A show's text columns are `name` (not `title`) because
-- that is what dim_series calls it.
--
-- Load strategy: REPLACE by series_id, like movie_translation — a show's
-- translation set can shrink when TMDB withdraws one, and an upsert would
-- strand a stale Russian overview forever with no failing check.
--
-- Seasons and episodes are deliberately not translated: their names and
-- overviews stay English (~85k episode rows against Neon's 512 MB free-tier cap,
-- plus a second season call per season every night).
--
-- Also folded into 01_dimensions.sql for fresh bootstraps. Safe to re-run.
-- Apply to Neon and the local replica BEFORE deploying code that reads it.

CREATE TABLE IF NOT EXISTS series_translation (
    series_id      INTEGER      NOT NULL,
    lang           VARCHAR(5)   NOT NULL,
    name           TEXT,
    overview       TEXT,
    tagline        TEXT,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_series_translation PRIMARY KEY (series_id, lang),
    CONSTRAINT fk_series_translation_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id)
);
