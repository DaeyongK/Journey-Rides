-- ─────────────────────────────────────────────────────────────
-- Announcements
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS announcements (
    id UUID PRIMARY KEY,

    title TEXT NOT NULL,
    content TEXT NOT NULL,

    -- UTC timestamps, timezone-aware
    send_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,

    -- scheduled | sent | closed
    state TEXT NOT NULL CHECK (state IN ('scheduled', 'sent', 'closed')),

    -- Whether users can interact (buttons enabled)
    reactable BOOLEAN NOT NULL,

    -- Discord message IDs (snowflakes)
    message_id BIGINT,
    dashboard_message_id BIGINT,

    -- Pagination state for admin dashboard
    dashboard_page INTEGER NOT NULL DEFAULT 0
);

-- ─────────────────────────────────────────────────────────────
-- Ride Entries
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ride_entries (
    announcement_id UUID NOT NULL
        REFERENCES announcements(id)
        ON DELETE CASCADE,

    -- Discord user ID (snowflake)
    user_id BIGINT NOT NULL,

    school TEXT NOT NULL,
    role TEXT NOT NULL,

    -- Only meaningful for drivers
    seats INTEGER CHECK (seats >= 0),

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (announcement_id, user_id)
);

-- ─────────────────────────────────────────────────────────────
-- Performance Indexes
-- ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_announcements_state_send
    ON announcements (state, send_at);

CREATE INDEX IF NOT EXISTS idx_announcements_end_at
    ON announcements (end_at);

CREATE INDEX IF NOT EXISTS idx_ride_entries_announcement
    ON ride_entries (announcement_id);
