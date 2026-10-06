-- Task 110: Uzbek keeps the original titles.
--
-- TMDB's Uzbek film titles cover ~15% of films and its show names ~5%, so a Uzbek
-- page showed a patchwork of translated and untranslated titles. The pipeline no
-- longer keeps a title/name for any language but Russian (etl/translations.py,
-- NAME_LANGUAGES); this clears what was loaded before that change.
--
-- Uzbek overviews and taglines are untouched. A row left with no text at all is
-- deleted, since the loader never writes a blank row. Russian rows are not touched.
--
-- Safe to re-run. Data-only: no schema change, nothing to fold into 01.

UPDATE movie_translation  SET title = NULL WHERE lang <> 'ru' AND title IS NOT NULL;
UPDATE series_translation SET name  = NULL WHERE lang <> 'ru' AND name  IS NOT NULL;

DELETE FROM movie_translation
 WHERE lang <> 'ru' AND title IS NULL AND overview IS NULL AND tagline IS NULL;
DELETE FROM series_translation
 WHERE lang <> 'ru' AND name IS NULL AND overview IS NULL AND tagline IS NULL;
