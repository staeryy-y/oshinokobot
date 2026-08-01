-- The vote tables always stored user_id, but that's a bare snowflake with
-- no human-readable identity attached — the admin UI had no way to show
-- "who voted for what" without calling back to Discord's API (which
-- wouldn't even work in admin-only mode, and wouldn't survive a user
-- leaving the server). This snapshots a display name at vote time instead,
-- so per-voter history in the admin UI is self-contained in SQLite like
-- everything else. Nullable: existing rows from before this migration have
-- no snapshot and just fall back to showing the raw id.
ALTER TABLE appeal_votes ADD COLUMN display_name TEXT;
ALTER TABLE tier_votes ADD COLUMN display_name TEXT;
