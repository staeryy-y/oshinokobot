-- The character's "core" — the appeal tag that got the most votes on a
-- poll, computed the same way and at the same time as result_tier
-- (majority vote, tied ones broken randomly, NULL if there were zero
-- appeal votes). Mirrors result_tier's semantics exactly, just for the
-- other question.
ALTER TABLE polls ADD COLUMN result_tag_id INTEGER REFERENCES archetype_tags(id);
