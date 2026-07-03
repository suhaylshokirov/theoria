-- Total and average revenue per genre.
-- Genre-movie pairs are de-duplicated first since fact_movie_metrics carries one
-- row per (movie, genre) and dim_movie.revenue must not be summed once per genre.

WITH movie_genre AS (
    SELECT DISTINCT movie_id, genre_id
    FROM fact_movie_metrics
)
SELECT
    g.genre_id,
    g.genre_name,
    COUNT(DISTINCT mg.movie_id)     AS movie_count,
    SUM(dm.revenue)                 AS total_revenue,
    ROUND(AVG(dm.revenue), 2)       AS avg_revenue_per_movie
FROM movie_genre mg
JOIN dim_genre g ON g.genre_id = mg.genre_id
JOIN dim_movie dm ON dm.movie_id = mg.movie_id
GROUP BY g.genre_id, g.genre_name
ORDER BY total_revenue DESC;
