-- Appeal votes become multi-select: a character can appeal to more than one
-- audience, so a voter should be able to pick several archetype tags on one
-- poll instead of just one. The old PK (poll_id, user_id) only allowed a
-- single row per user per poll (a second pick overwrote the first); folding
-- tag_id into the key lets multiple picks coexist. SQLite can't ALTER a
-- PRIMARY KEY in place, so this is the standard rebuild-and-copy — existing
-- single-pick rows carry over unchanged, nothing is lost.
CREATE TABLE appeal_votes_new (
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    user_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES archetype_tags(id),
    display_name TEXT,
    PRIMARY KEY (poll_id, user_id, tag_id)
);

INSERT INTO appeal_votes_new (poll_id, user_id, tag_id, display_name)
    SELECT poll_id, user_id, tag_id, display_name FROM appeal_votes;

DROP TABLE appeal_votes;
ALTER TABLE appeal_votes_new RENAME TO appeal_votes;
