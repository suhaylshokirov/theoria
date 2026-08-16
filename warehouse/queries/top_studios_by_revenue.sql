-- Top studios by total revenue, with a minimum film count floor so a studio
-- with exactly one blockbuster can't outrank a studio with a long, steady
-- catalog — the same shape as top_rated_directors.sql's >=3-film floor.
--
-- Revenue sums straight off dim_movie (one row per film, no fan-out to guard
-- against — a plain SUM is correct here). Rating is the opposite: it lives in
-- fact_movie_metrics, which repeats a movie's rating once per genre, so it is
-- de-duplicated to one row per movie in movie_ratings before being averaged.
-- Getting these two backwards — averaging the un-deduped rating, or guarding
-- the already-one-row-per-film revenue sum — is the exact mistake this
-- project has hit before (Task 59's studio_detail view hit the same fork).

WITH movie_company AS (
    SELECT DISTINCT movie_id, company_id
    FROM bridge_movie_company
),
movie_ratings AS (
    SELECT DISTINCT movie_id, rating
    FROM fact_movie_metrics
)
SELECT
    c.slug                          AS studio_slug,
    c.name                          AS studio_name,
    COUNT(DISTINCT mc.movie_id)     AS movie_count,
    SUM(dm.revenue)                 AS total_revenue,
    ROUND(AVG(dm.revenue), 2)       AS avg_revenue_per_movie,
    ROUND(AVG(mr.rating), 2)        AS avg_rating
FROM movie_company mc
JOIN dim_company c        ON c.company_id = mc.company_id
JOIN dim_movie dm         ON dm.movie_id = mc.movie_id
LEFT JOIN movie_ratings mr ON mr.movie_id = mc.movie_id
GROUP BY c.company_id, c.name
HAVING COUNT(DISTINCT mc.movie_id) >= 3
ORDER BY total_revenue DESC
LIMIT 20;
