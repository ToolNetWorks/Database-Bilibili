BEGIN;

CREATE TABLE IF NOT EXISTS bilibili_channels (
    uid VARCHAR(20) PRIMARY KEY,
    name TEXT,
    source_url TEXT NOT NULL,
    total_videos INTEGER NOT NULL DEFAULT 0,
    last_scan_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_channel_uid
        CHECK (uid ~ '^[0-9]{1,20}$'),

    CONSTRAINT valid_total_videos
        CHECK (total_videos >= 0)
);

CREATE TABLE IF NOT EXISTS bilibili_videos (
    bvid VARCHAR(12) PRIMARY KEY,

    channel_uid VARCHAR(20) NOT NULL
        REFERENCES bilibili_channels(uid)
        ON DELETE RESTRICT,

    aid BIGINT,
    cid BIGINT,

    url TEXT NOT NULL,
    title TEXT,
    thumbnail TEXT,

    duration_seconds INTEGER,

    status VARCHAR(30) NOT NULL DEFAULT 'UNKNOWN',

    is_upower_exclusive BOOLEAN,
    is_upower_preview BOOLEAN,
    is_upower_play BOOLEAN,

    rights JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    last_checked_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_bvid
        CHECK (bvid ~ '^BV[A-Za-z0-9]{10}$'),

    CONSTRAINT valid_duration
        CHECK (
            duration_seconds IS NULL
            OR duration_seconds >= 0
        ),

    CONSTRAINT valid_access_status
        CHECK (
            status IN (
                'FREE_CANDIDATE',
                'POWER',
                'POWER_PREVIEW',
                'PAID',
                'UNKNOWN'
            )
        )
);

CREATE INDEX IF NOT EXISTS idx_bilibili_videos_channel
    ON bilibili_videos(channel_uid);

CREATE INDEX IF NOT EXISTS idx_bilibili_videos_status
    ON bilibili_videos(status);

CREATE INDEX IF NOT EXISTS idx_bilibili_videos_channel_status
    ON bilibili_videos(channel_uid, status);

COMMIT;
