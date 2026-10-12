-- The headline numbers at the top of the analytics page: one row of counts and
-- medians that the chapters below then explain.
--
-- Medians, not means: ratings and return multiples are skewed (a few huge
-- blockbusters, a long tail of low scores), so the mean describes no real movie.
--
-- median_return_multiple counts only movies with a known budget of at least
-- $1M and a non-zero revenue. TMDB stores "unknown" as 0, so revenue = 0 means
-- the figure is missing (or a streaming release with no box office), not that
-- the movie earned nothing. dim_movie is one row per film: no fan-out.
--
-- imdb_tmdb_correlation is Pearson's r between the two rating sources over
-- movies with at least 50 TMDB votes (a TMDB score on a handful of votes is noise).

SELECT
    (SELECT COUNT(*) FROM dim_movie)                                   AS movie_count,
    (SELECT COUNT(*) FROM dim_series)                                  AS series_count,
    (SELECT COUNT(*) FROM fact_episode_rating WHERE source = 'imdb')   AS rated_episode_count,
    (SELECT ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rating))::numeric, 2)
       FROM fact_movie_rating WHERE source = 'imdb')                   AS median_movie_rating,
    (SELECT ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rating))::numeric, 2)
       FROM fact_series_rating WHERE source = 'imdb')                  AS median_series_rating,
    (SELECT ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP
                   (ORDER BY revenue::numeric / budget))::numeric, 2)
       FROM dim_movie WHERE budget >= 1000000 AND revenue > 0)         AS median_return_multiple,
    (SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE original_language <> 'en')
                  / NULLIF(COUNT(original_language), 0), 1)
       FROM dim_movie)                                                 AS non_english_pct,
    (SELECT ROUND(CORR(i.rating, t.rating)::numeric, 2)
       FROM fact_movie_rating i
       JOIN fact_movie_rating t ON t.movie_id = i.movie_id AND t.source = 'tmdb'
      WHERE i.source = 'imdb' AND t.vote_count >= 50)                  AS imdb_tmdb_correlation;
