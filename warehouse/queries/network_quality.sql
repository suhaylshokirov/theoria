-- The networks and streamers carrying the most shows, and how those shows are
-- rated: volume next to quality.
--
-- At least 5 rated shows per network, so a median means something; ranked by how
-- many shows that is. A show on two networks counts for both. There is no
-- network detail page, so no slug is carried. Rating is IMDb's from
-- fact_series_rating, one row per show.

SELECT
    n.name                                                                     AS network_name,
    COUNT(DISTINCT sn.series_id)                                               AS series_count,
    ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r.rating))::numeric, 2) AS median_rating
FROM bridge_series_network sn
JOIN dim_network n ON n.network_id = sn.network_id
JOIN fact_series_rating r ON r.series_id = sn.series_id AND r.source = 'imdb'
GROUP BY n.network_id, n.name
HAVING COUNT(DISTINCT sn.series_id) >= 5
ORDER BY series_count DESC, network_name
LIMIT 10;
