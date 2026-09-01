-- Task 72: people bios — biography, vitals, IMDb link, aliases.
--
-- dim_person's five original columns (person_id, name, gender, popularity,
-- profile_path, known_for_department) all come from the cast/crew member
-- objects embedded in a *movie's* credits payload. TMDB keeps those minimal.
-- The biographical fields live on `GET /person/{id}`, called for the first
-- time in this project by etl/bronze/ingest_people.py.
--
-- The six scalar fields go straight onto dim_person. `also_known_as` is a
-- list, not a scalar — folding it into a delimited string would break first
-- normal form — so it gets its own table, person_alias. That table is named
-- plainly: not fact_ (it carries no measure) and not bridge_ (it doesn't join
-- two dimensions, it attaches repeating text to one).
--
-- imdb_id gets a NON-UNIQUE index — it's an external lookup key, but nothing
-- guarantees TMDB never repeats one (same reasoning as dim_movie.imdb_id,
-- Task 55).
--
-- Measured coverage among the ~35,782 people with a photo (n=200 probe,
-- 2026-09-01): imdb_id 94%, birthday 66%, biography 64%, place_of_birth 64%,
-- homepage 16%, deathday 14%. All nullable. `adult` (always false) is
-- deliberately not carried, matching the Phase 12 precedent.
--
-- These columns / this table are also in 01_dimensions.sql for fresh
-- bootstraps. Safe to re-run (IF NOT EXISTS throughout).

ALTER TABLE dim_person ADD COLUMN IF NOT EXISTS biography      TEXT;
ALTER TABLE dim_person ADD COLUMN IF NOT EXISTS birthday       DATE;
ALTER TABLE dim_person ADD COLUMN IF NOT EXISTS deathday       DATE;
ALTER TABLE dim_person ADD COLUMN IF NOT EXISTS place_of_birth TEXT;
ALTER TABLE dim_person ADD COLUMN IF NOT EXISTS homepage       TEXT;
ALTER TABLE dim_person ADD COLUMN IF NOT EXISTS imdb_id        VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_dim_person_imdb_id ON dim_person (imdb_id);

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
