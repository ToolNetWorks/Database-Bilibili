-- Migration: Add sync metadata tables for dual database replication
-- This migration creates the outbox and checkpoint tables needed for
-- reliable data synchronization between primary and standby databases.

BEGIN;

-- Outbox table for capturing data changes in the same transaction
-- as the business data. This ensures no data loss during replication.
CREATE TABLE IF NOT EXISTS sync_outbox (
    id BIGSERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

-- Index for efficient polling of unprocessed changes
CREATE INDEX IF NOT EXISTS idx_sync_outbox_unprocessed
    ON sync_outbox (created_at)
    WHERE processed_at IS NULL;

-- Additional index for cleanup of old processed entries
CREATE INDEX IF NOT EXISTS idx_sync_outbox_processed
    ON sync_outbox (processed_at)
    WHERE processed_at IS NOT NULL;

-- Checkpoint table for tracking replication progress on standby
CREATE TABLE IF NOT EXISTS sync_checkpoint (
    id INTEGER PRIMARY KEY DEFAULT 1,
    last_processed_id BIGINT NOT NULL DEFAULT 0,
    last_processed_at TIMESTAMPTZ,
    lag_seconds DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Initialize checkpoint row
INSERT INTO sync_checkpoint (id, last_processed_id)
VALUES (1, 0)
ON CONFLICT (id) DO NOTHING;

-- Function to clean up old processed outbox entries (older than 7 days)
-- This should be run periodically via pg_cron or external scheduler
CREATE OR REPLACE FUNCTION cleanup_sync_outbox(retention_days INTEGER DEFAULT 7)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM sync_outbox
    WHERE processed_at IS NOT NULL
      AND processed_at < NOW() - (retention_days || ' days')::INTERVAL;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Function to get replication lag in seconds
CREATE OR REPLACE FUNCTION get_replication_lag()
RETURNS DOUBLE PRECISION AS $$
DECLARE
    lag_seconds DOUBLE PRECISION;
BEGIN
    SELECT COALESCE(lag_seconds, 0)
    INTO lag_seconds
    FROM sync_checkpoint
    WHERE id = 1;

    RETURN lag_seconds;
END;
$$ LANGUAGE plpgsql;

-- View for monitoring sync status
CREATE OR REPLACE VIEW sync_status AS
SELECT
    (SELECT COUNT(*) FROM sync_outbox WHERE processed_at IS NULL) AS pending_changes,
    (SELECT last_processed_id FROM sync_checkpoint WHERE id = 1) AS last_processed_id,
    (SELECT last_processed_at FROM sync_checkpoint WHERE id = 1) AS last_processed_at,
    (SELECT lag_seconds FROM sync_checkpoint WHERE id = 1) AS replication_lag_seconds,
    (SELECT MAX(created_at) FROM sync_outbox) AS latest_change_at,
    (SELECT COUNT(*) FROM sync_outbox WHERE processed_at IS NOT NULL) AS total_processed,
    (SELECT COUNT(*) FROM sync_outbox WHERE retry_count > 0) AS failed_changes;

COMMIT;