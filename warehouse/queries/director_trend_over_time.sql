-- Director output and average rating by release year, to spot trends over a career.

WITH movie_ratings AS (
    SELECT DISTINCT movie_id, rating
    FROM fact_movie_metrics
)
SELECT
    d.director_id,
    d.name                          AS director_name,
    dd.year,
    COUNT(DISTINCT fc.movie_id)     AS movie_count,
    ROUND(AVG(mr.rating), 2)        AS avg_rating
FROM fact_casting fc
JOIN dim_director d ON d.director_id = fc.director_id
JOIN dim_movie dm   ON dm.movie_id = fc.movie_id
JOIN dim_date dd    ON dd.full_date = dm.release_date
JOIN movie_ratings mr ON mr.movie_id = fc.movie_id
GROUP BY d.director_id, d.name, dd.year
ORDER BY d.name, dd.year;
