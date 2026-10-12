-- The studios behind the most movies, and what those movies earned.
--
-- Restricted to movies with a known budget (>= $1M) and revenue, and to studios
-- with at least 5 of them, so the median and the revenue total describe the same
-- set of movies. Ranked by how many movies that is, not by revenue: a revenue
-- ranking mostly measures how many recent blockbusters a studio made, and
-- unadjusted dollars favour the newest ones.
--
-- movie_company de-duplicates the bridge (its PK already makes it one row per
-- pair; the CTE is the same guard top_studios_by_revenue.sql used).
-- Rating is IMDb's, one row per film in fact_movie_rating, so AVG needs no guard.

WITH movie_company AS (
    SELECT DISTINCT movie_id, company_id
    FROM bridge_movie_company
)
SELECT
    c.slug                                                          AS studio_slug,
    c.name                                                          AS studio_name,
    COUNT(*)                                                        AS movie_count,
    ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP
           (ORDER BY dm.revenue::numeric / dm.budget))::numeric, 2) AS median_multiple,
    ROUND(AVG(r.rating), 2)                                         AS avg_rating,
    SUM(dm.revenue)                                                 AS total_revenue
FROM movie_company mc
JOIN dim_company c ON c.company_id = mc.company_id
JOIN dim_movie dm  ON dm.movie_id = mc.movie_id
LEFT JOIN fact_movie_rating r ON r.movie_id = mc.movie_id AND r.source = 'imdb'
WHERE dm.budget >= 1000000
  AND dm.revenue > 0
GROUP BY c.company_id, c.slug, c.name
HAVING COUNT(*) >= 5
ORDER BY movie_count DESC, studio_name
LIMIT 10;
