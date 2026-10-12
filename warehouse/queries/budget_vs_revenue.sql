-- One row per movie with a usable budget and revenue: the points of the
-- budget-vs-revenue scatter, and the pool the "biggest return" callouts and
-- the headline shares are computed from.
--
-- Usable = budget of at least $1M (smaller figures are mostly placeholders:
-- TMDB stores 1, 100, 1000 for "unknown") and a non-zero revenue (0 means
-- unknown, or a streaming release with no box office). Not adjusted for
-- inflation; revenue is worldwide gross, budget excludes marketing.
--
-- dim_movie is one row per film, so nothing is de-duplicated. Newest-first
-- ordering keeps the output stable; the page sorts for itself.

SELECT
    dm.slug                                   AS movie_slug,
    dm.title,
    dd.year                                   AS release_year,
    dm.poster_path,
    dm.budget,
    dm.revenue,
    ROUND(dm.revenue::numeric / dm.budget, 2) AS return_multiple
FROM dim_movie dm
JOIN dim_date dd ON dd.full_date = dm.release_date
WHERE dm.budget >= 1000000
  AND dm.revenue > 0
ORDER BY dd.year DESC, dm.title;
