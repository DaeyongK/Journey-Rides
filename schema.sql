CREATE TABLE IF NOT EXISTS announcements (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    send_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    content TEXT NOT NULL,
    reactable INTEGER NOT NULL,
    state TEXT NOT NULL,
    message_id INTEGER,
    dashboard_message_id INTEGER,
    dashboard_page INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ride_entries (
    announcement_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    school TEXT NOT NULL,
    role TEXT NOT NULL,
    seats INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (announcement_id, user_id)
);
