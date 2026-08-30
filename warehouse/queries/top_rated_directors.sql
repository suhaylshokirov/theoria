-- Top-rated directors, weighted toward directors with a meaningful body of work.
-- Rating and vote count come from fact_movie_rating (IMDb, one row per film), so
-- no per-movie de-duplication is needed — the movie_ratings CTE that used to read
-- fact_movie_metrics here existed only to collapse that table's per-genre fan-out.
--
-- "Director" is now a job on a credit rather than a table someone belongs to,
-- so the filter is job = 'Director' against fact_credit. The join to
-- fact_movie_rating is a LEFT JOIN so movie_count stays "films directed", not
-- "rated films directed"; AVG/SUM ignore the nulls a rating-less film contributes.

SELECT
    p.person_id                     AS director_id,
    p.name                          AS director_name,
    COUNT(DISTINCT fc.movie_id)     AS movie_count,
    ROUND(AVG(r.rating), 2)         AS avg_rating,
    SUM(r.vote_count)               AS total_votes
FROM fact_credit fc
JOIN dim_person p     ON p.person_id = fc.person_id
LEFT JOIN fact_movie_rating r ON r.movie_id = fc.movie_id AND r.source = 'imdb'
WHERE fc.job = 'Director'
GROUP BY p.person_id, p.name
HAVING COUNT(DISTINCT fc.movie_id) >= 3
ORDER BY avg_rating DESC NULLS LAST, total_votes DESC
LIMIT 20;
