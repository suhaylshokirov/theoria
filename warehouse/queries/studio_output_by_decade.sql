-- The leading studio (by film count) for each decade of release.
--
-- A full (studio x decade) crosstab across 1,383 companies would be either an
-- unreadably wide table or thousands of rows — neither fits the dashboard's
-- flat-ranked-table shape every other panel uses. "Output by decade" is
-- interpreted here as *who led* each decade, which stays one row per decade
-- (bounded, ~10 rows) while still surfacing the studio/decade join.
--
-- bridge_movie_company is already one row per (movie, company) pair (its own
-- PK), so no de-duplication CTE is needed the way fact_movie_metrics needs
-- one elsewhere in this directory — ties are broken alphabetically so the
-- result is deterministic.

WITH studio_decade AS (
    SELECT
        dd.decade,
        c.company_id,
        c.name                          AS studio_name,
        COUNT(DISTINCT bmc.movie_id)    AS movie_count
    FROM bridge_movie_company bmc
    JOIN dim_movie dm  ON dm.movie_id = bmc.movie_id
    JOIN dim_date dd   ON dd.full_date = dm.release_date
    JOIN dim_company c ON c.company_id = bmc.company_id
    GROUP BY dd.decade, c.company_id, c.name
),
ranked AS (
    SELECT
        decade, studio_name, movie_count,
        RANK() OVER (PARTITION BY decade ORDER BY movie_count DESC, studio_name) AS rnk
    FROM studio_decade
)
SELECT decade, studio_name, movie_count
FROM ranked
WHERE rnk = 1
ORDER BY decade
LIMIT 20;
