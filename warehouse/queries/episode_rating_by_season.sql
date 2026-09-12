-- Does a show decline as it runs longer? Average IMDb episode rating by
-- season number, across every show in the catalog — a catalog-wide trend,
-- not a per-show one (a per-show line chart would need the categorical,
-- multi-series chart machinery this dashboard doesn't have). Season 0
-- ("Specials", TMDB's array-position convention, not a viewing order — see
-- series_detail()) is excluded: it isn't a normal narrative season and would
-- distort the trend.
--
-- A season number needs at least 10 distinct shows behind it to be counted —
-- measured live before picking the floor (2026-09-12): show counts decay
-- gradually from 300 at season 1 to 10 at season 27, then fall under 10 for
-- every season after, so this is where the real sample runs out rather than
-- an arbitrary round number (the same >=N-with-a-measured-floor shape as
-- top_rated_directors.sql's >=3-film one).

SELECT
    e.season_number,
    COUNT(DISTINCT e.series_id)     AS show_count,
    ROUND(AVG(er.rating), 2)        AS avg_rating
FROM dim_episode e
LEFT JOIN fact_episode_rating er ON er.episode_id = e.episode_id AND er.source = 'imdb'
WHERE e.season_number > 0
GROUP BY e.season_number
HAVING COUNT(DISTINCT e.series_id) >= 10
ORDER BY e.season_number;
