-- Does a show's rating change as it runs longer, measured against the show's
-- own first season?
--
-- A plain average rating by season number is misleading: only good shows reach
-- season 15, so the line rises or flattens just because weak shows were
-- cancelled earlier (survivorship). Here each show contributes the DIFFERENCE
-- between a season's average episode rating and its own season 1, so every point
-- answers "how did the shows that got this far change?".
--
-- Season 0 ("Specials", TMDB's array-position convention, not a viewing order)
-- is excluded. A show needs a rated season 1 to take part. A season number needs
-- at least 15 such shows behind it: measured on the local warehouse the series
-- count falls from 300 at season 1 to 18 at season 15, and the tail past that is
-- noise (13 shows, -0.59 at season 23).
-- Rating is IMDb's, from fact_episode_rating at one row per episode.

WITH season_avg AS (
    SELECT e.series_id, e.season_number, AVG(er.rating) AS season_rating
    FROM dim_episode e
    JOIN fact_episode_rating er ON er.episode_id = e.episode_id AND er.source = 'imdb'
    WHERE e.season_number > 0
    GROUP BY e.series_id, e.season_number
),
first_season AS (
    SELECT series_id, season_rating AS first_rating
    FROM season_avg
    WHERE season_number = 1
)
SELECT
    s.season_number,
    COUNT(*)                                             AS show_count,
    ROUND(AVG(s.season_rating)::numeric, 2)              AS avg_rating,
    ROUND(AVG(s.season_rating - f.first_rating)::numeric, 2) AS avg_change_from_season_1
FROM season_avg s
JOIN first_season f ON f.series_id = s.series_id
GROUP BY s.season_number
HAVING COUNT(*) >= 15
ORDER BY s.season_number;
