CREATE TABLE poll_notes (
    id INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    message TEXT NOT NULL CHECK(length(message) BETWEEN 1 AND 300),
    created_at TEXT NOT NULL
);
CREATE INDEX poll_notes_poll_id ON poll_notes(poll_id, id);
