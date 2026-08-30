-- Movie count, average IMDb rating, and total revenue per decade of release.
-- The rating is IMDb's, read from fact_movie_rating at its true grain (one row
-- per film) — no per-movie de-duplication needed, unlike a read off
-- fact_movie_metrics, which repeats a rating once per genre.

SELECT
    dd.decade,
    COUNT(DISTINCT dm.movie_id)     AS movie_count,
    ROUND(AVG(r.rating), 2)         AS avg_rating,
    SUM(dm.revenue)                 AS total_revenue
FROM dim_movie dm
JOIN dim_date dd ON dd.full_date = dm.release_date
LEFT JOIN fact_movie_rating r ON r.movie_id = dm.movie_id AND r.source = 'imdb'
GROUP BY dd.decade
ORDER BY dd.decade;
