-- Server-side sessions for the admin login (replaces HTTP Basic Auth).
-- The cookie only carries the opaque id; everything else lives here so a
-- session can be revoked (logout, or manually) without any client-side
-- signing/secret-key machinery.
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
