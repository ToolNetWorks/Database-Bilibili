-- Migration: Add metadata columns to bilibili_videos
-- Adds description, published_at, and views columns for richer video metadata.

BEGIN;

ALTER TABLE bilibili_videos
    ADD COLUMN IF NOT EXISTS description TEXT;

ALTER TABLE bilibili_videos
    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;

ALTER TABLE bilibili_videos
    ADD COLUMN IF NOT EXISTS views BIGINT;

-- Index for sorting by published date
CREATE INDEX IF NOT EXISTS idx_bilibili_videos_published
    ON bilibili_videos(published_at DESC NULLS LAST);

COMMIT;