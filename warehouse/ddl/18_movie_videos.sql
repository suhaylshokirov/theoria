-- Task 74 (Trailers & clips): dim_movie_video — a film's trailers and clips,
-- one row per (movie_id, video_id).
--
-- Naming — and it is NOT the one the last three additions used:
--   - Not fact_. There is no measure here. `size` is a resolution attribute
--     (2160/1080/720/480/360), not something summed or averaged. A fact_
--     prefix would promise a measure that does not exist.
--   - Not bridge_. A bridge joins *two* dimensions (bridge_movie_company ties
--     dim_movie to dim_company). There is no dim_video and nothing will ever
--     join to one — a video carries only descriptive columns and is read
--     exclusively by joining down from dim_movie. A bridge_ prefix would
--     assert a second dimension that does not exist.
--   - dim_movie_video. This is a multi-valued attribute of dim_movie: one
--     film, many videos, each purely descriptive. That is dimension-shaped.
--
-- PRIMARY KEY is TMDB's own video_id, not `key`. Both are distinct across the
-- catalogue, but `key` is the *site-specific* id (a YouTube watch id, a Vimeo
-- id) and those share no namespace — a YouTube key and a Vimeo key could
-- collide in principle. video_id is TMDB's stable identifier for the row.
--
-- Index on (movie_id, type): every read is "this film's trailers" or "this
-- film's clips".
--
-- Load strategy is REPLACE, not upsert (see load_dim_movie_video in
-- etl/warehouse_loader/load_dimensions.py). Videos are the first entity here
-- that can *shrink*: TMDB removes videos and YouTube keys rot when an upload
-- is deleted or made private. A pure upsert would leave a dead embed on the
-- page forever, with no failing check. The loader deletes every row for the
-- movie_ids in the current Silver partition, then inserts the current set —
-- a scoped delete, never a blanket TRUNCATE.
--
-- Also folded into 01_dimensions.sql for fresh bootstraps (it is a dim_, so
-- 01). Safe to re-run (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS dim_movie_video (
    movie_id       INTEGER      NOT NULL,
    video_id       VARCHAR(24)  NOT NULL,
    name           TEXT,
    key            TEXT,
    site           TEXT,
    type           TEXT,
    official       BOOLEAN,
    size           INTEGER,
    iso_639_1      VARCHAR(8),
    iso_3166_1     VARCHAR(8),
    published_at   TIMESTAMPTZ,
    ingestion_date DATE         NOT NULL,
    CONSTRAINT pk_dim_movie_video PRIMARY KEY (movie_id, video_id),
    CONSTRAINT fk_dim_movie_video_movie
        FOREIGN KEY (movie_id) REFERENCES dim_movie (movie_id)
);
CREATE INDEX IF NOT EXISTS idx_dim_movie_video_movie_type
    ON dim_movie_video (movie_id, type);
