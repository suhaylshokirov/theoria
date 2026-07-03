-- Movie count, average rating, and total revenue per decade of release.
-- Ratings are de-duplicated per movie before aggregation for the same reason
-- as top_rated_directors.sql (fact_movie_metrics has one row per genre).

WITH movie_ratings AS (
    SELECT DISTINCT movie_id, rating
    FROM fact_movie_metrics
)
SELECT
    dd.decade,
    COUNT(DISTINCT dm.movie_id)     AS movie_count,
    ROUND(AVG(mr.rating), 2)        AS avg_rating,
    SUM(dm.revenue)                 AS total_revenue
FROM dim_movie dm
JOIN dim_date dd ON dd.full_date = dm.release_date
LEFT JOIN movie_ratings mr ON mr.movie_id = dm.movie_id
GROUP BY dd.decade
ORDER BY dd.decade;
