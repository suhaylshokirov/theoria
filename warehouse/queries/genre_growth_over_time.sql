-- Number of movies released per genre per year, to track genre popularity over time.

WITH movie_genre AS (
    SELECT DISTINCT movie_id, genre_id
    FROM fact_movie_metrics
)
SELECT
    g.genre_id,
    g.genre_name,
    dd.year,
    COUNT(DISTINCT mg.movie_id) AS movie_count
FROM movie_genre mg
JOIN dim_genre g  ON g.genre_id = mg.genre_id
JOIN dim_movie dm ON dm.movie_id = mg.movie_id
JOIN dim_date dd  ON dd.full_date = dm.release_date
GROUP BY g.genre_id, g.genre_name, dd.year
ORDER BY g.genre_name, dd.year;
