-- Task 90 (TV trailer/home/analytics): dim_series_video — a show's trailers
-- and clips, one row per (series_id, video_id). An exact copy of
-- 18_movie_videos.sql's dim_movie_video, with series_id in place of movie_id
-- — same naming reasoning (not fact_: no measure, `size` is a resolution;
-- not bridge_: there is no dim_video to join to), same PRIMARY KEY choice
-- (TMDB's own video_id, not the site-specific `key`), same index shape.
--
-- The data has been in Bronze since Task 77 (`append_to_response=
-- aggregate_credits,external_ids,videos`), so there is no "pre-feature
-- partition" gap here the way dim_movie_video has for anything ingested
-- before Task 73 — every bronze/series_details payload already carries a
-- videos key.
--
-- Load strategy is REPLACE, not upsert (see load_dim_series_video in
-- etl/warehouse_loader/load_dimensions.py) — the dim_movie_video reasoning
-- applies unchanged: TMDB removes videos and YouTube keys rot, so a pure
-- upsert would leave a dead embed on the show page forever. The loader
-- deletes every row for the series_ids in the current Silver partition, then
-- inserts the current set — a scoped delete, never a blanket TRUNCATE.
--
-- Numbered 24, not 23 — 23_reclaim_warehouse_storage.sql (2026-09-12, the
-- same-day ad-hoc Neon storage cleanup) claimed that number first.
--
-- Also folded into 01_dimensions.sql for fresh bootstraps (it is a dim_, so
-- 01). Safe to re-run (IF NOT EXISTS throughout).

CREATE TABLE IF NOT EXISTS dim_series_video (
    series_id      INTEGER      NOT NULL,
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
    CONSTRAINT pk_dim_series_video PRIMARY KEY (series_id, video_id),
    CONSTRAINT fk_dim_series_video_series
        FOREIGN KEY (series_id) REFERENCES dim_series (series_id)
);
CREATE INDEX IF NOT EXISTS idx_dim_series_video_series_type
    ON dim_series_video (series_id, type);
