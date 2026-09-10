-- Lets an admin mark a character as "pick this last": still eligible for
-- the daily draw, but pick_random_unused_character always exhausts every
-- non-deprioritized character first (see the ORDER BY there) rather than
-- picking with reduced probability. Defaults to 0 so every existing
-- character stays exactly as eligible/prioritized as before this column
-- existed.
ALTER TABLE characters ADD COLUMN deprioritized INTEGER NOT NULL DEFAULT 0;
