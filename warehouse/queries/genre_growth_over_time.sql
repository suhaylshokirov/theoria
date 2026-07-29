-- Number of movies released per genre per year, to track genre popularity over time.
--
-- Capped: this returns one row per (genre, year), so with 19 genres over ~57 years of
-- catalogue it grows into the high hundreds. Ordered most-recent-first so the cap drops
-- the sparse early years rather than the dense recent ones.

WITH movie_genre AS (
    SELECT DISTINCT movie_id, genre_id
    FROM fact_movie_metrics
)
SELECT
    g.genre_id,
    g.genre_name,
    dd.year,
    COUNT(DISTINCT mg.movie_id) AS movie_count
FROM movie_genre mg
JOIN dim_genre g  ON g.genre_id = mg.genre_id
JOIN dim_movie dm ON dm.movie_id = mg.movie_id
JOIN dim_date dd  ON dd.full_date = dm.release_date
GROUP BY g.genre_id, g.genre_name, dd.year
ORDER BY dd.year DESC, g.genre_name
LIMIT 300;
