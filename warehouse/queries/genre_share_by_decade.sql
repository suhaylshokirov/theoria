-- Which genres each decade's movies belong to, as a share of that decade.
--
-- A share, not a count, because the archive holds roughly the same number of
-- movies per decade; raw counts would only show that sampling rule. A movie
-- counts in each of its genres, so a decade's shares add up to more than 100%.
--
-- Limited to the ten genres with the most movies overall (a fifteen-row heatmap
-- is unreadable on a phone) and to decades with at least 20 movies. The
-- denominator is the decade's movies that carry any genre.

WITH movie_genre AS (
    SELECT DISTINCT movie_id, genre_id
    FROM fact_movie_metrics
),
decade_totals AS (
    SELECT dd.decade, COUNT(DISTINCT mg.movie_id) AS decade_movies
    FROM movie_genre mg
    JOIN dim_movie dm ON dm.movie_id = mg.movie_id
    JOIN dim_date dd  ON dd.full_date = dm.release_date
    GROUP BY dd.decade
    HAVING COUNT(DISTINCT mg.movie_id) >= 20
),
top_genres AS (
    SELECT genre_id
    FROM movie_genre
    GROUP BY genre_id
    ORDER BY COUNT(*) DESC
    LIMIT 10
)
SELECT
    dd.decade,
    g.genre_name,
    COUNT(DISTINCT mg.movie_id)                                       AS movie_count,
    dt.decade_movies,
    ROUND(100.0 * COUNT(DISTINCT mg.movie_id) / dt.decade_movies, 1)  AS share_pct
FROM movie_genre mg
JOIN top_genres tg    ON tg.genre_id = mg.genre_id
JOIN dim_genre g      ON g.genre_id = mg.genre_id
JOIN dim_movie dm     ON dm.movie_id = mg.movie_id
JOIN dim_date dd      ON dd.full_date = dm.release_date
JOIN decade_totals dt ON dt.decade = dd.decade
GROUP BY dd.decade, g.genre_id, g.genre_name, dt.decade_movies
ORDER BY dd.decade, share_pct DESC, g.genre_name;
