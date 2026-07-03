-- Actor pairs who have appeared together most often, via a self-join on fact_casting.
-- fc1.actor_id < fc2.actor_id keeps each pair once (a,b) instead of twice (a,b)+(b,a)
-- and avoids pairing an actor with themself.

SELECT
    a1.actor_id  AS actor_1_id,
    a1.name      AS actor_1_name,
    a2.actor_id  AS actor_2_id,
    a2.name      AS actor_2_name,
    COUNT(DISTINCT fc1.movie_id) AS movies_together
FROM fact_casting fc1
JOIN fact_casting fc2
    ON fc1.movie_id = fc2.movie_id
   AND fc1.actor_id < fc2.actor_id
JOIN dim_actor a1 ON a1.actor_id = fc1.actor_id
JOIN dim_actor a2 ON a2.actor_id = fc2.actor_id
GROUP BY a1.actor_id, a1.name, a2.actor_id, a2.name
HAVING COUNT(DISTINCT fc1.movie_id) >= 2
ORDER BY movies_together DESC, a1.name, a2.name
LIMIT 50;
