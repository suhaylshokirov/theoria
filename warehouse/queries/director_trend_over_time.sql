-- Director output and average rating by release year, to spot trends over a career.
--
-- Restricted to directors with a real body of work (>= 3 films) and capped, because
-- this returns one row per (director, year): at ~1,200 films that is well over a
-- thousand rows, which is more than the dashboard panel can usefully show.

WITH movie_ratings AS (
    SELECT DISTINCT movie_id, rating
    FROM fact_movie_metrics
),
directing AS (
    SELECT person_id, movie_id
    FROM fact_credit
    WHERE job = 'Director'
),
prolific AS (
    SELECT person_id
    FROM directing
    GROUP BY person_id
    HAVING COUNT(DISTINCT movie_id) >= 3
)
SELECT
    p.person_id                     AS director_id,
    p.name                          AS director_name,
    dd.year,
    COUNT(DISTINCT d.movie_id)      AS movie_count,
    ROUND(AVG(mr.rating), 2)        AS avg_rating
FROM directing d
JOIN prolific pr      ON pr.person_id = d.person_id
JOIN dim_person p     ON p.person_id = d.person_id
JOIN dim_movie dm     ON dm.movie_id = d.movie_id
JOIN dim_date dd      ON dd.full_date = dm.release_date
JOIN movie_ratings mr ON mr.movie_id = d.movie_id
GROUP BY p.person_id, p.name, dd.year
ORDER BY p.name, dd.year
LIMIT 300;
