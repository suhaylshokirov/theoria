-- Actor pairs who have appeared together most often.
--
-- Reads fact_collaboration rather than self-joining the credits: the pair counts
-- are already derived in Gold, canonically ordered (person_a_id < person_b_id) so
-- each pair appears once, and indexed on films_together. The self-join version
-- computed the same numbers from scratch on every dashboard load.
--
-- Restricted to pairs who have both acted, so this stays a *casting* question —
-- fact_collaboration itself spans every key credit, including crew.

WITH actors AS (
    SELECT DISTINCT person_id
    FROM fact_credit
    WHERE department = 'Acting'
)
SELECT
    c.person_a_id  AS actor_1_id,
    a.name         AS actor_1_name,
    c.person_b_id  AS actor_2_id,
    b.name         AS actor_2_name,
    c.films_together AS movies_together
FROM fact_collaboration c
JOIN actors aa    ON aa.person_id = c.person_a_id
JOIN actors bb    ON bb.person_id = c.person_b_id
JOIN dim_person a ON a.person_id = c.person_a_id
JOIN dim_person b ON b.person_id = c.person_b_id
WHERE c.films_together >= 2
ORDER BY c.films_together DESC, a.name, b.name
LIMIT 50;
