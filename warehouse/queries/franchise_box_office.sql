-- Film series by what their movies earned against what they cost.
--
-- Catalog movies only: a franchise's other parts are listed, not held, and carry
-- no budget or revenue. The same cut as the rest of the Money chapter: a known
-- budget of at least $1M and box-office revenue, so the multiple is not a silly
-- number from a $5k film. A series needs 2 such movies to be compared at all.
--
-- return_multiple is the franchise's revenue over its budget in total, not the
-- average of its films' multiples, so one cheap runaway hit cannot stand for the
-- series. dim_movie is one row per film, so nothing needs de-duplicating.

SELECT
    c.slug                                                  AS franchise_slug,
    c.name                                                  AS franchise_name,
    COUNT(*)                                                AS movie_count,
    SUM(m.revenue)                                          AS total_revenue,
    SUM(m.budget)                                           AS total_budget,
    ROUND(SUM(m.revenue)::numeric / SUM(m.budget), 2)       AS return_multiple
FROM dim_movie m
JOIN dim_collection c ON c.collection_id = m.collection_id
WHERE m.budget >= 1000000
  AND m.revenue > 0
GROUP BY c.collection_id, c.slug, c.name
HAVING COUNT(*) >= 2
ORDER BY total_revenue DESC, franchise_name
LIMIT 10;
