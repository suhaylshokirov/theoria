-- Most productive actors by distinct movie count, plus their TMDB popularity score.
--
-- An "actor" is someone holding an Acting credit — dim_person holds everybody, so
-- the department is the filter rather than the table.

SELECT
    p.person_id                     AS actor_id,
    p.name                          AS actor_name,
    p.popularity,
    COUNT(DISTINCT fc.movie_id)     AS movie_count
FROM fact_credit fc
JOIN dim_person p ON p.person_id = fc.person_id
WHERE fc.department = 'Acting'
GROUP BY p.person_id, p.name, p.popularity
ORDER BY movie_count DESC, p.popularity DESC
LIMIT 20;
