-- How shows are rated depending on where they stand today: still running
-- (Returning Series), finished (Ended) or cancelled.
--
-- Median IMDb rating per status, with the number of rated shows behind it.
-- fact_series_rating is one row per show, so no de-duplication is needed. A show
-- with no status recorded is left out.

SELECT
    ds.status,
    COUNT(*)                                                             AS series_count,
    ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r.rating))::numeric, 2) AS median_rating
FROM dim_series ds
JOIN fact_series_rating r ON r.series_id = ds.series_id AND r.source = 'imdb'
WHERE ds.status IS NOT NULL
GROUP BY ds.status
ORDER BY series_count DESC;
