-- The tier a poll closes with — computed once, at close time, from
-- majority vote (see app/bot/cogs/polls.py::_compute_result_tier):
-- plurality winner, ties broken randomly, NULL if there were zero tier
-- votes at all ("doesn't count", not defaulted to any tier). Nullable
-- for that reason, and because open polls have no result yet.
-- NULL is exempt from CHECK evaluation in SQLite, so this is safe to add
-- against existing rows without a backfill.
ALTER TABLE polls ADD COLUMN result_tier TEXT CHECK (result_tier IN ('S', 'A', 'B', 'C', 'D'));
