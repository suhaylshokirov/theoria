-- Task 53 (Phase 10): retire the actor/director split.
--
-- dim_person + fact_credit (08) supersede these four tables completely:
--
--   dim_actor    ->  dim_person, filtered to people with an Acting credit
--   dim_director ->  dim_person, filtered to people with a Directing credit
--   fact_cast    ->  fact_credit WHERE department = 'Acting'
--   fact_crew    ->  fact_credit WHERE job = 'Director'
--
-- Every reader — the Django views, all ten analytics queries, the DQ checks —
-- moved across first, in Tasks 51 and 53. Dropping them last is what made each
-- commit in this phase deployable on its own.
--
-- Facts before dimensions: fact_cast/fact_crew hold the FKs into
-- dim_actor/dim_director, so the dimension drops would fail otherwise.
--
-- Note this loses the mapping that let /actors/<slug>/ redirect for the 376
-- slugs that changed when the two slug namespaces merged. Those URLs now 404.
-- Keeping two whole dimension tables alive purely as a redirect map would cost
-- more than it's worth on a catalog that isn't public.

DROP TABLE IF EXISTS fact_cast;
DROP TABLE IF EXISTS fact_crew;
DROP TABLE IF EXISTS dim_actor;
DROP TABLE IF EXISTS dim_director;
