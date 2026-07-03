-- Top-rated directors, weighted toward directors with a meaningful body of work.
-- fact_movie_metrics has one row per (movie, genre), so ratings/vote_counts are
-- de-duplicated per movie first to avoid double-counting a multi-genre film.

WITH movie_ratings AS (
    SELECT DISTINCT movie_id, rating, vote_count
    FROM fact_movie_metrics
)
SELECT
    d.director_id,
    d.name                          AS director_name,
    COUNT(DISTINCT fc.movie_id)     AS movie_count,
    ROUND(AVG(mr.rating), 2)        AS avg_rating,
    SUM(mr.vote_count)              AS total_votes
FROM fact_casting fc
JOIN dim_director d  ON d.director_id = fc.director_id
JOIN movie_ratings mr ON mr.movie_id = fc.movie_id
GROUP BY d.director_id, d.name
HAVING COUNT(DISTINCT fc.movie_id) >= 3
ORDER BY avg_rating DESC, total_votes DESC
LIMIT 20;
