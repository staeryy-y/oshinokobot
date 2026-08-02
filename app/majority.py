from __future__ import annotations

import random


def pick_majority(counts: dict) -> object | None:
    """Generic majority-vote winner with a random tie-break among only the
    tied top entries. Empty counts (no votes at all) means no winner, not
    a default one. Shared by the bot (computed once at poll-close time)
    and the migration backfill (computed retroactively for polls that
    closed before this logic existed) so there's exactly one
    implementation of the rule."""
    if not counts:
        return None
    top_count = max(counts.values())
    winners = [key for key, count in counts.items() if count == top_count]
    return random.choice(winners)
