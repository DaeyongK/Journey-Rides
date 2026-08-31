-- ─────────────────────────────────────────────────────────────
-- Announcements
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS announcements (
    id UUID PRIMARY KEY,

    title TEXT NOT NULL,
    content TEXT NOT NULL,
    content_category TEXT,

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
    dashboard_page INTEGER NOT NULL DEFAULT 0,

    -- Calendar date of the ride this announcement is for (nullable). When set,
    -- drivers assigned to that (ride_date, content_category) in
    -- availability_assignments are auto-registered when the announcement is sent.
    ride_date DATE
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
    phone TEXT NOT NULL,
    info TEXT,
    row_num INTEGER NOT NULL DEFAULT 1,

    -- TRUE when this driver row was created by the availability→announcement
    -- pipeline (not a manual signup). Only such rows are auto-withdrawn when
    -- assignments change.
    auto_assigned BOOLEAN NOT NULL DEFAULT FALSE,

    PRIMARY KEY (announcement_id, user_id)
);

-- ─────────────────────────────────────────────────────────────
-- Saved Information
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS saved_info (
    -- Discord user ID (snowflake)
    user_id BIGINT NOT NULL,

    role TEXT NOT NULL,

    -- Only meaningful for drivers
    seats INTEGER CHECK (seats >= 0),

    phone TEXT NOT NULL,

    PRIMARY KEY (user_id)
);

-- ─────────────────────────────────────────────────────────────
-- Monthly Driver Availability & Assignment
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS availability_polls (
    id UUID PRIMARY KEY,

    -- 'YYYY-MM' (Eastern)
    month TEXT NOT NULL,

    -- Discord message IDs (snowflakes), both in ADMIN_CHANNEL_ID
    admin_message_id BIGINT,              -- driving schedule
    admin_availability_message_id BIGINT, -- every driver's availability (live-updated)

    -- open | assigned | closed
    state TEXT NOT NULL DEFAULT 'open'
        CHECK (state IN ('open', 'assigned', 'closed')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One availability poll per calendar month, ever.
CREATE UNIQUE INDEX IF NOT EXISTS uq_availability_polls_month
    ON availability_polls (month);

-- One driver-facing availability dropdown message per school, each in that
-- school's own channel.
CREATE TABLE IF NOT EXISTS availability_poll_messages (
    poll_id UUID NOT NULL
        REFERENCES availability_polls(id)
        ON DELETE CASCADE,
    school TEXT NOT NULL,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    PRIMARY KEY (poll_id, school)
);

CREATE TABLE IF NOT EXISTS availability_occurrences (
    poll_id UUID NOT NULL
        REFERENCES availability_polls(id)
        ON DELETE CASCADE,

    ride_date DATE NOT NULL,

    -- F = Friday PM, S = Sunday Service
    ride_type TEXT NOT NULL CHECK (ride_type IN ('F', 'S')),

    -- Comma-separated schools this ride's dropdown option is shown to and
    -- assigned for (e.g. 'GT,Emory', 'GT', 'Emory'). Set per Sunday from the
    -- host campus on /availability_create; Fridays default to all schools.
    schools TEXT NOT NULL DEFAULT 'GT,Emory',

    PRIMARY KEY (poll_id, ride_date, ride_type)
);

CREATE TABLE IF NOT EXISTS availability_entries (
    poll_id UUID NOT NULL
        REFERENCES availability_polls(id)
        ON DELETE CASCADE,

    -- Discord user ID (snowflake)
    user_id BIGINT NOT NULL,

    ride_date DATE NOT NULL,
    ride_type TEXT NOT NULL,
    school TEXT NOT NULL,

    PRIMARY KEY (poll_id, user_id, ride_date, ride_type)
);

CREATE TABLE IF NOT EXISTS availability_assignments (
    poll_id UUID NOT NULL
        REFERENCES availability_polls(id)
        ON DELETE CASCADE,

    -- Discord user ID (snowflake)
    user_id BIGINT NOT NULL,

    ride_date DATE NOT NULL,
    ride_type TEXT NOT NULL,
    school TEXT NOT NULL,

    -- 'auto' or an admin display name
    assigned_by TEXT NOT NULL DEFAULT 'auto',

    PRIMARY KEY (poll_id, user_id, ride_date, ride_type)
);

-- ─────────────────────────────────────────────────────────────
-- Performance Indexes
-- (single-column poll_id / announcement_id lookups are already served by the
--  leftmost column of each table's composite PRIMARY KEY)
-- ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_announcements_state_send
    ON announcements (state, send_at);

CREATE INDEX IF NOT EXISTS idx_announcements_end_at
    ON announcements (end_at);

CREATE INDEX IF NOT EXISTS idx_avail_assign_ride
    ON availability_assignments (ride_date, ride_type);

-- ─────────────────────────────────────────────────────────────
-- Migrations for pre-existing databases (idempotent)
--
-- The columns below also live in the CREATE TABLE bodies above (for fresh
-- installs), but CREATE TABLE IF NOT EXISTS is a no-op when the table already
-- exists, so existing databases need these explicit ADD COLUMNs.
-- ─────────────────────────────────────────────────────────────
ALTER TABLE announcements
    ADD COLUMN IF NOT EXISTS ride_date DATE;

ALTER TABLE ride_entries
    ADD COLUMN IF NOT EXISTS auto_assigned BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE availability_polls
    ADD COLUMN IF NOT EXISTS admin_availability_message_id BIGINT;

ALTER TABLE availability_occurrences
    ADD COLUMN IF NOT EXISTS schools TEXT NOT NULL DEFAULT 'GT,Emory';

-- ─────────────────────────────────────────────────────────────
-- One-time cleanup of superseded columns / indexes (idempotent)
-- ─────────────────────────────────────────────────────────────
ALTER TABLE availability_polls       DROP COLUMN IF EXISTS channel_message_id;
ALTER TABLE availability_entries     DROP COLUMN IF EXISTS updated_at;
ALTER TABLE availability_assignments DROP COLUMN IF EXISTS updated_at;

DROP INDEX IF EXISTS idx_ride_entries_announcement;
DROP INDEX IF EXISTS idx_avail_occurrences_poll;
DROP INDEX IF EXISTS idx_avail_entries_poll;
DROP INDEX IF EXISTS idx_avail_assign_poll;
