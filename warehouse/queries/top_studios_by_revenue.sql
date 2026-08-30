-- Top studios by total revenue, with a minimum film count floor so a studio
-- with exactly one blockbuster can't outrank a studio with a long, steady
-- catalog — the same shape as top_rated_directors.sql's >=3-film floor.
--
-- Revenue sums straight off dim_movie (one row per film, no fan-out to guard
-- against — a plain SUM is correct here). Rating comes from fact_movie_rating,
-- which is one row per film per source, so AVG(rating) needs no de-duplication:
-- the movie_ratings CTE that used to sit here (SELECT DISTINCT ... FROM
-- fact_movie_metrics) existed only to undo that table's per-genre fan-out.

WITH movie_company AS (
    SELECT DISTINCT movie_id, company_id
    FROM bridge_movie_company
)
SELECT
    c.slug                          AS studio_slug,
    c.name                          AS studio_name,
    COUNT(DISTINCT mc.movie_id)     AS movie_count,
    SUM(dm.revenue)                 AS total_revenue,
    ROUND(AVG(dm.revenue), 2)       AS avg_revenue_per_movie,
    ROUND(AVG(r.rating), 2)         AS avg_rating
FROM movie_company mc
JOIN dim_company c         ON c.company_id = mc.company_id
JOIN dim_movie dm          ON dm.movie_id = mc.movie_id
LEFT JOIN fact_movie_rating r ON r.movie_id = mc.movie_id AND r.source = 'imdb'
GROUP BY c.company_id, c.name
HAVING COUNT(DISTINCT mc.movie_id) >= 3
ORDER BY total_revenue DESC
LIMIT 20;
