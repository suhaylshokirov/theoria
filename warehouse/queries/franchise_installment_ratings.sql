-- Does a series hold its quality as it goes on? The average IMDb rating of a
-- franchise's first, second, third ... movie, by place in release order.
--
-- The place comes from EVERY part TMDB lists for the franchise, in release order
-- (earliest first; undated parts last, so they cannot push a dated one down), so a
-- film that is third in its series counts as third even when the first two are
-- movies the catalog only lists. `adult` parts are left out, as on the site.
-- Only catalog movies with an IMDb rating contribute a rating.
--
-- A place needs 10 franchises behind it: measured on 2026-11-17, 335 franchises
-- have a rated first movie, 131 a second, 68 a third, 31 a fourth, 16 a fifth,
-- 11 a sixth and 6 a seventh; past that it is one or two series, not a trend.
-- Survivorship applies, as in season_trajectory.sql: only series that keep going
-- reach a high number, and the caption on the page says so.
-- Rating is IMDb's, one row per film in fact_movie_rating, so AVG needs no guard.

WITH installments AS (
    SELECT
        p.collection_id,
        p.movie_id,
        ROW_NUMBER() OVER (
            PARTITION BY p.collection_id
            ORDER BY p.release_date NULLS LAST, p.position, p.movie_id
        ) AS installment_number
    FROM dim_collection_part p
    WHERE p.adult IS NOT TRUE
)
SELECT
    i.installment_number,
    COUNT(*)                        AS franchise_count,
    ROUND(AVG(r.rating)::numeric, 2) AS avg_rating
FROM installments i
JOIN dim_movie m ON m.movie_id = i.movie_id AND m.collection_id = i.collection_id
JOIN fact_movie_rating r ON r.movie_id = i.movie_id AND r.source = 'imdb'
GROUP BY i.installment_number
HAVING COUNT(*) >= 10
ORDER BY i.installment_number;
