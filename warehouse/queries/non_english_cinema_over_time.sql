-- Non-English cinema over time — the share of each release decade's films whose
-- original language is something other than English.
--
-- original_language is the film's primary language (one value per film), so this
-- reads straight off dim_movie with no bridge fan-out. A NULL original_language
-- is treated as unknown, not non-English: it is excluded from the numerator but
-- still counted in film_count.

SELECT
    dd.decade,
    COUNT(DISTINCT dm.movie_id)                                            AS film_count,
    COUNT(DISTINCT dm.movie_id) FILTER (WHERE dm.original_language <> 'en') AS non_english_count,
    ROUND(
        100.0 * COUNT(DISTINCT dm.movie_id) FILTER (WHERE dm.original_language <> 'en')
        / NULLIF(COUNT(DISTINCT dm.movie_id), 0), 1
    )                                                                      AS non_english_pct
FROM dim_movie dm
JOIN dim_date dd ON dd.full_date = dm.release_date
GROUP BY dd.decade
ORDER BY dd.decade
LIMIT 20;
