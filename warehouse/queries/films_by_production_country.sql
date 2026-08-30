-- Films by country of production — how many catalogue films were (co-)produced
-- in each country, most first. A film with several production countries counts
-- once for each, so the counts sum to more than the catalogue size. Only the
-- 'production' relation is used here; 'origin' is a separate relationship that
-- disagrees on ~23% of films (see bridge_movie_country's PK).
--
-- Average rating is IMDb's, from fact_movie_rating at one row per film — a plain
-- AVG is correct, no per-genre de-duplication needed. LEFT JOIN so an as-yet
-- unrated film still counts toward film_count.

SELECT
    c.name                          AS country_name,
    COUNT(DISTINCT b.movie_id)      AS film_count,
    ROUND(AVG(r.rating), 2)         AS avg_rating
FROM bridge_movie_country b
JOIN dim_country c ON c.country_code = b.country_code
LEFT JOIN fact_movie_rating r ON r.movie_id = b.movie_id AND r.source = 'imdb'
WHERE b.relation = 'production'
GROUP BY c.name
ORDER BY film_count DESC
LIMIT 20;
