-- How IMDb ratings are spread across the catalogue: the number of titles in
-- each half-point band (a rating of 7.3 lands in the 7.0 band), for movies and
-- for shows side by side.
--
-- Both sources are one row per title (fact_movie_rating / fact_series_rating),
-- so a plain COUNT needs no de-duplication.

SELECT 'movie' AS kind, FLOOR(rating * 2) / 2 AS band_start, COUNT(*) AS title_count
FROM fact_movie_rating
WHERE source = 'imdb'
GROUP BY 2
UNION ALL
SELECT 'series', FLOOR(rating * 2) / 2, COUNT(*)
FROM fact_series_rating
WHERE source = 'imdb'
GROUP BY 2
ORDER BY 1, 2;
