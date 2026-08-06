-- How many people and credits each craft department accounts for.
--
-- The point of this panel: acting is a minority of the work that makes a film.
-- Before fact_credit existed the warehouse held only cast and directors, so
-- every one of these rows except two was unrepresentable.

SELECT
    fc.department,
    COUNT(*)                          AS credits,
    COUNT(DISTINCT fc.person_id)      AS people,
    COUNT(DISTINCT fc.movie_id)       AS films,
    ROUND(COUNT(*)::numeric / COUNT(DISTINCT fc.movie_id), 1) AS credits_per_film
FROM fact_credit fc
GROUP BY fc.department
ORDER BY credits DESC;
