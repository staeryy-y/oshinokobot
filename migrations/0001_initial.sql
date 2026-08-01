CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    series TEXT,
    image_path TEXT NOT NULL,
    source_url TEXT,
    uploaded_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE archetype_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- Singleton row (id fixed at 1) — this bot only ever lives in one guild
-- (see PLAN.md), so there's nothing to key config rows by. The guild's
-- identity itself comes from DISCORD_GUILD_ID in the environment; this
-- table only holds the parts an admin should be able to change without a
-- redeploy.
CREATE TABLE guild_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    channel_id INTEGER,
    poll_post_time TEXT NOT NULL DEFAULT '09:00',
    poll_timezone TEXT NOT NULL DEFAULT 'America/New_York'
);
INSERT INTO guild_config (id, channel_id) VALUES (1, NULL);

CREATE TABLE polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    posted_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE appeal_votes (
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    user_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES archetype_tags(id),
    PRIMARY KEY (poll_id, user_id)
);

CREATE TABLE tier_votes (
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    user_id INTEGER NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('S', 'A', 'B', 'C', 'D')),
    PRIMARY KEY (poll_id, user_id)
);
