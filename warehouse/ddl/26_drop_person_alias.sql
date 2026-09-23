-- Ad hoc (2026-09-23), alongside 25_drop_unused_indexes.sql: drop
-- person_alias (Task 72) to reclaim Neon free-tier storage.
--
-- Confirmed write-only before dropping: the nightly loader rewrote all ~81k
-- rows every run, but nothing read them — the Django model was never queried
-- by a view or template, and no analytics query touched the table. ~19 MB
-- once its heap and both indexes (pk + the redundant idx_person_alias_person_id)
-- are counted.
--
-- Only the warehouse copy goes. Bronze still holds `also_known_as` in every
-- person payload, and Silver still writes silver/person_aliases/ — so if an
-- alias search is ever built, the loader can be restored from git history and
-- backfilled from the lake without calling TMDB again.
--
-- Apply only after the loader change that stops writing this table is on
-- main — otherwise the next nightly run fails inserting into it.

DROP TABLE IF EXISTS person_alias;
