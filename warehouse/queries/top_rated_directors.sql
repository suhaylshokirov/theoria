-- Top-rated directors, weighted toward directors with a meaningful body of work.
-- fact_movie_metrics has one row per (movie, genre), so ratings/vote_counts are
-- de-duplicated per movie first to avoid double-counting a multi-genre film.
--
-- "Director" is now a job on a credit rather than a table someone belongs to,
-- so the filter is job = 'Director' against fact_credit.

WITH movie_ratings AS (
    SELECT DISTINCT movie_id, rating, vote_count
    FROM fact_movie_metrics
)
SELECT
    p.person_id                     AS director_id,
    p.name                          AS director_name,
    COUNT(DISTINCT fc.movie_id)     AS movie_count,
    ROUND(AVG(mr.rating), 2)        AS avg_rating,
    SUM(mr.vote_count)              AS total_votes
FROM fact_credit fc
JOIN dim_person p     ON p.person_id = fc.person_id
JOIN movie_ratings mr ON mr.movie_id = fc.movie_id
WHERE fc.job = 'Director'
GROUP BY p.person_id, p.name
HAVING COUNT(DISTINCT fc.movie_id) >= 3
ORDER BY avg_rating DESC, total_votes DESC
LIMIT 20;
