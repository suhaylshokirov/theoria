-- How a genre's movies return their budget: the number of movies in each
-- return band (revenue / budget), plus the genre's median multiple.
--
-- Bands: under 1x (box office below budget), 1-2x, 2-5x, 5x or more. A fixed
-- "hit" cut-off at 2.5x was tried and rejected: 76% of movies clear it and every
-- genre sits between 67% and 93%, so it separates nothing. Bands show the whole
-- shape instead.
--
-- A multi-genre movie counts once in each of its genres (the movie_genre CTE
-- collapses fact_movie_metrics' per-genre repeats of the same pair), so the genre
-- counts sum to more than the catalogue. Genres with under 30 usable movies are
-- left out: a median of ten movies is an anecdote.
-- Usable movies: budget >= $1M and revenue > 0, as in budget_vs_revenue.sql.

WITH movie_genre AS (
    SELECT DISTINCT movie_id, genre_id
    FROM fact_movie_metrics
)
SELECT
    g.genre_name,
    COUNT(*)                                                        AS movie_count,
    ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP
           (ORDER BY dm.revenue::numeric / dm.budget))::numeric, 2) AS median_multiple,
    COUNT(*) FILTER (WHERE dm.revenue < dm.budget)                  AS under_1x,
    COUNT(*) FILTER (WHERE dm.revenue >= dm.budget
                       AND dm.revenue < 2 * dm.budget)              AS from_1x_to_2x,
    COUNT(*) FILTER (WHERE dm.revenue >= 2 * dm.budget
                       AND dm.revenue < 5 * dm.budget)              AS from_2x_to_5x,
    COUNT(*) FILTER (WHERE dm.revenue >= 5 * dm.budget)             AS over_5x
FROM movie_genre mg
JOIN dim_genre g  ON g.genre_id = mg.genre_id
JOIN dim_movie dm ON dm.movie_id = mg.movie_id
WHERE dm.budget >= 1000000
  AND dm.revenue > 0
GROUP BY g.genre_id, g.genre_name
HAVING COUNT(*) >= 30
ORDER BY median_multiple DESC, g.genre_name;
