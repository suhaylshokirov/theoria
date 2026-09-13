-- Show count and average IMDb rating per decade of first air date. The TV
-- counterpart of movies_by_decade.sql — no revenue column, since TV carries
-- no money measure (see 21_series_ratings.sql's deliberate omission of
-- fact_series_metrics).
--
-- The rating is IMDb's, read from fact_series_rating at its true grain (one
-- row per show) — no per-show de-duplication needed.

SELECT
    dd.decade,
    COUNT(DISTINCT ds.series_id)    AS series_count,
    ROUND(AVG(r.rating), 2)         AS avg_rating
FROM dim_series ds
JOIN dim_date dd ON dd.full_date = ds.first_air_date
LEFT JOIN fact_series_rating r ON r.series_id = ds.series_id AND r.source = 'imdb'
GROUP BY dd.decade
ORDER BY dd.decade;
