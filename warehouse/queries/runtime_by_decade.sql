-- The typical movie's running time, by decade of release.
--
-- Median, not mean, so one three-hour epic doesn't move a decade. A runtime of
-- 0 is TMDB's "unknown" and is left out. Decades with fewer than 20 movies are
-- dropped: the archive holds a single 1930s movie and three from the 1960s, and
-- a line drawn through n=1 reads as a trend it isn't.

SELECT
    dd.decade,
    COUNT(*)                                                                  AS movie_count,
    ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY dm.runtime))::numeric) AS median_runtime
FROM dim_movie dm
JOIN dim_date dd ON dd.full_date = dm.release_date
WHERE dm.runtime > 0
GROUP BY dd.decade
HAVING COUNT(*) >= 20
ORDER BY dd.decade;
