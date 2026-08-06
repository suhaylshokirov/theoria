-- The recurring creative partnerships behind a director's work.
--
-- A director's signature is rarely theirs alone: the same editor, composer or
-- cinematographer turns up film after film. This joins each director to the key
-- craft people credited on the same films and counts the repeats.
--
-- Restricted to >= 3 shared films so this shows working relationships rather
-- than coincidence.

WITH directing AS (
    SELECT person_id AS director_id, movie_id
    FROM fact_credit
    WHERE job = 'Director'
),
craft AS (
    SELECT person_id, movie_id, job
    FROM fact_credit
    WHERE job IN ('Original Music Composer', 'Director of Photography',
                  'Editor', 'Production Design', 'Screenplay')
)
SELECT
    d.name                          AS director_name,
    c.name                          AS collaborator_name,
    cr.job                          AS craft,
    COUNT(DISTINCT dir.movie_id)    AS films_together,
    MIN(dd.year)                    AS first_year,
    MAX(dd.year)                    AS last_year
FROM directing dir
JOIN craft cr     ON cr.movie_id = dir.movie_id AND cr.person_id <> dir.director_id
JOIN dim_person d ON d.person_id = dir.director_id
JOIN dim_person c ON c.person_id = cr.person_id
JOIN dim_movie dm ON dm.movie_id = dir.movie_id
JOIN dim_date dd  ON dd.full_date = dm.release_date
GROUP BY d.name, c.name, cr.job
HAVING COUNT(DISTINCT dir.movie_id) >= 3
ORDER BY films_together DESC, d.name
LIMIT 40;
