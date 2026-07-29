-- Director output and average rating by release year, to spot trends over a career.
--
-- Restricted to directors with a real body of work (>= 3 films) and capped, because
-- this returns one row per (director, year): at ~1,200 films that is well over a
-- thousand rows, which is more than the dashboard panel can usefully show.

WITH movie_ratings AS (
    SELECT DISTINCT movie_id, rating
    FROM fact_movie_metrics
),
prolific AS (
    SELECT director_id
    FROM fact_crew
    GROUP BY director_id
    HAVING COUNT(DISTINCT movie_id) >= 3
)
SELECT
    d.director_id,
    d.name                          AS director_name,
    dd.year,
    COUNT(DISTINCT fc.movie_id)     AS movie_count,
    ROUND(AVG(mr.rating), 2)        AS avg_rating
FROM fact_crew fc
JOIN prolific p     ON p.director_id = fc.director_id
JOIN dim_director d ON d.director_id = fc.director_id
JOIN dim_movie dm   ON dm.movie_id = fc.movie_id
JOIN dim_date dd    ON dd.full_date = dm.release_date
JOIN movie_ratings mr ON mr.movie_id = fc.movie_id
GROUP BY d.director_id, d.name, dd.year
ORDER BY d.name, dd.year
LIMIT 300;
