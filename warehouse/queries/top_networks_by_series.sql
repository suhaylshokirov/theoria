-- Top networks by how many catalogue shows they've carried, with a minimum
-- series-count floor — the same >=3 shape top_studios_by_revenue.sql and
-- top_rated_directors.sql use — so a network with one show can't tie a
-- network with a long, steady slate. No revenue column: TV carries no money
-- measure (see 21_series_ratings.sql). No link out either — there is no
-- network detail page (series_detail.html renders a network as plain text),
-- so this carries no slug.
--
-- Rating is IMDb's from fact_series_rating, one row per show, so AVG needs
-- no de-duplication guard.

SELECT
    n.name                           AS network_name,
    COUNT(DISTINCT sn.series_id)     AS series_count,
    ROUND(AVG(r.rating), 2)          AS avg_rating
FROM bridge_series_network sn
JOIN dim_network n ON n.network_id = sn.network_id
LEFT JOIN fact_series_rating r ON r.series_id = sn.series_id AND r.source = 'imdb'
GROUP BY n.network_id, n.name
HAVING COUNT(DISTINCT sn.series_id) >= 3
ORDER BY series_count DESC
LIMIT 20;
