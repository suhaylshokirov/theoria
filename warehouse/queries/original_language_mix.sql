-- The original language of the catalogue's movies, most common first.
--
-- original_language is one value per film, so this reads straight off dim_movie
-- with no bridge fan-out. A movie with no language recorded is left out. The
-- page keeps the first few and folds the rest into "other languages".

SELECT
    dm.original_language                            AS language_code,
    COALESCE(l.english_name, dm.original_language)  AS language_name,
    COUNT(*)                                        AS movie_count
FROM dim_movie dm
LEFT JOIN dim_language l ON l.language_code = dm.original_language
WHERE dm.original_language IS NOT NULL
GROUP BY dm.original_language, l.english_name
ORDER BY movie_count DESC, language_name;
