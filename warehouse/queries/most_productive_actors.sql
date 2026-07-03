-- Most productive actors by distinct movie count, plus their TMDB popularity score.

SELECT
    a.actor_id,
    a.name                          AS actor_name,
    a.popularity,
    COUNT(DISTINCT fc.movie_id)     AS movie_count
FROM fact_casting fc
JOIN dim_actor a ON a.actor_id = fc.actor_id
GROUP BY a.actor_id, a.name, a.popularity
ORDER BY movie_count DESC, a.popularity DESC
LIMIT 20;
