-- Restricts which characters.series (games/shows) the daily poll draws
-- from. NULL means unrestricted (all games) - the default, and the state
-- new series automatically stay included in as long as nobody's ever
-- narrowed it. Non-NULL is a JSON-encoded array of series values,
-- meaning "only these" - an explicit allowlist an admin opted into via
-- the admin UI, not something new games join automatically.
ALTER TABLE guild_config ADD COLUMN active_series TEXT;
