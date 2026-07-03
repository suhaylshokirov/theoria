-- Watermark tracking for incremental warehouse loads.
-- Run after 01_dimensions.sql / 02_facts.sql; IF NOT EXISTS so re-runs are safe.

CREATE TABLE IF NOT EXISTS etl_watermarks (
    loader_name         TEXT        NOT NULL,
    last_ingestion_date DATE        NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_etl_watermarks PRIMARY KEY (loader_name)
);
