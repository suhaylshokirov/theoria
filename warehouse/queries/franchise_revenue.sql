-- Film series by total box office, with how much each entry averages.
--
-- dim_movie holds one row per film, so revenue sums directly here — no genre
-- fan-out to collapse first, unlike anything that reads fact_movie_metrics.

SELECT
    c.name                                  AS franchise,
    COUNT(*)                                AS entries,
    SUM(m.revenue)                          AS total_revenue,
    ROUND(AVG(m.revenue))                   AS avg_revenue,
    MIN(EXTRACT(YEAR FROM m.release_date))  AS first_year,
    MAX(EXTRACT(YEAR FROM m.release_date))  AS last_year
FROM dim_movie m
JOIN dim_collection c ON c.collection_id = m.collection_id
WHERE m.revenue IS NOT NULL AND m.revenue > 0
GROUP BY c.name
HAVING COUNT(*) >= 2
ORDER BY total_revenue DESC
LIMIT 30;
