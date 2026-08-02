from __future__ import annotations

# Numeric scoring for an *average* tier (as opposed to polls.result_tier,
# which is the majority/mode) — used by the public results page's
# cumulative ranking. S highest, D lowest, standard tier-list convention.
TIER_VALUES = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
_VALUE_TO_TIER = {value: tier for tier, value in TIER_VALUES.items()}


def nearest_tier(average: float) -> str:
    nearest_value = min(_VALUE_TO_TIER, key=lambda value: abs(value - average))
    return _VALUE_TO_TIER[nearest_value]


def build_voter_rows(
    tier_votes: list, appeal_votes: list, tags_by_id: dict[int, str]
) -> list[dict]:
    """Merges one poll's tier + appeal votes into one row per voter — the
    two questions are independent, so a voter who only answered one still
    gets a row, with '—' for the one they skipped. Shared by the admin
    poll detail page and the public results page, since both show this
    same per-voter breakdown."""
    tier_by_user = {vote["user_id"]: vote for vote in tier_votes}
    appeal_by_user = {vote["user_id"]: vote for vote in appeal_votes}

    rows = []
    for user_id in set(tier_by_user) | set(appeal_by_user):
        tier_vote = tier_by_user.get(user_id)
        appeal_vote = appeal_by_user.get(user_id)
        display_name = (tier_vote or appeal_vote)["display_name"] or f"user {user_id}"
        rows.append(
            {
                "user_id": user_id,
                "display_name": display_name,
                "tier": tier_vote["tier"] if tier_vote else None,
                "appeal_tag": tags_by_id.get(appeal_vote["tag_id"], "unknown tag")
                if appeal_vote
                else None,
            }
        )
    rows.sort(key=lambda row: row["display_name"].lower())
    return rows
