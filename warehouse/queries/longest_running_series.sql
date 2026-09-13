-- Longest-running shows by calendar span (first to last air date), not by
-- episode count — Detective Conan (1 season, 1,212 episodes since 1996) and
-- Saturday Night Live (52 seasons, 1,018 episodes since 1975) are both
-- "long-running" in different senses; this answers the calendar one.
--
-- date - date in Postgres already returns an integer day count, so /365 is
-- an approximate whole-year figure for display, not a precise duration.
-- Rating is IMDb's from fact_series_rating, one row per show, so a plain
-- AVG needs no de-duplication guard.

SELECT
    ds.slug                                       AS series_slug,
    ds.name                                        AS series_name,
    ds.first_air_date,
    ds.last_air_date,
    (ds.last_air_date - ds.first_air_date) / 365   AS years_on_air,
    ds.number_of_seasons,
    ds.number_of_episodes,
    ROUND(AVG(r.rating), 2)                        AS avg_rating
FROM dim_series ds
LEFT JOIN fact_series_rating r ON r.series_id = ds.series_id AND r.source = 'imdb'
WHERE ds.first_air_date IS NOT NULL AND ds.last_air_date IS NOT NULL
GROUP BY ds.series_id, ds.slug, ds.name, ds.first_air_date, ds.last_air_date,
         ds.number_of_seasons, ds.number_of_episodes
ORDER BY (ds.last_air_date - ds.first_air_date) DESC
LIMIT 20;
