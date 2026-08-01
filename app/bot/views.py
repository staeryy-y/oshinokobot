from __future__ import annotations

import discord

from .. import db

TIERS = ["S", "A", "B", "C", "D"]

# Discord's own select-menu limit — with more tags than this, the daily
# poll can only offer the first 25 (see PLAN.md's noted v1 limit).
MAX_SELECT_OPTIONS = 25


def _truncate(label: str, limit: int = 100) -> str:
    return label if len(label) <= limit else label[: limit - 1] + "…"


class TierButton(discord.ui.Button):
    def __init__(self, poll_id: int, tier: str, count: int, *, disabled: bool = False):
        label = f"{tier} ({count})" if count > 0 else tier
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success if count > 0 else discord.ButtonStyle.secondary,
            custom_id=f"tier:{poll_id}:{tier}",
            disabled=disabled,
        )
        self.poll_id = poll_id
        self.tier = tier

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        poll = await db.get_poll(bot.db, self.poll_id)  # type: ignore[attr-defined]
        if poll is None or poll["status"] != "open":
            await interaction.response.send_message(
                "This poll isn't open anymore.", ephemeral=True
            )
            return

        await db.upsert_tier_vote(  # type: ignore[attr-defined]
            bot.db, poll_id=self.poll_id, user_id=interaction.user.id, tier=self.tier
        )

        updated_view = await rebuild_view(bot, self.poll_id)
        # The button's own count/color is the confirmation — no separate
        # "you voted X" message on top of it, same reasoning as scheduler-bot.
        await interaction.response.edit_message(view=updated_view)


class TagSelect(discord.ui.Select):
    def __init__(
        self,
        poll_id: int,
        tags: list,
        counts: dict[int, int] | None = None,
        *,
        disabled: bool = False,
    ):
        counts = counts or {}
        options = [
            discord.SelectOption(
                label=_truncate(
                    f"{t['name']} ({counts[t['id']]})" if counts.get(t["id"]) else t["name"]
                ),
                value=str(t["id"]),
            )
            for t in tags[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(
            placeholder="Who would this appeal to?",
            options=options or [discord.SelectOption(label="No tags configured yet", value="_")],
            custom_id=f"appeal:{poll_id}",
            disabled=disabled or not options,
        )
        self.poll_id = poll_id

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        poll = await db.get_poll(bot.db, self.poll_id)  # type: ignore[attr-defined]
        if poll is None or poll["status"] != "open":
            await interaction.response.send_message(
                "This poll isn't open anymore.", ephemeral=True
            )
            return

        tag_id = int(self.values[0])
        await db.upsert_appeal_vote(  # type: ignore[attr-defined]
            bot.db, poll_id=self.poll_id, user_id=interaction.user.id, tag_id=tag_id
        )

        updated_view = await rebuild_view(bot, self.poll_id)
        await interaction.response.edit_message(view=updated_view)


async def rebuild_view(bot, poll_id: int) -> "PollView":
    tags = await db.list_tags(bot.db)
    tier_counts = await db.get_tier_vote_counts(bot.db, poll_id)
    appeal_counts = await db.get_appeal_vote_counts(bot.db, poll_id)
    return PollView(poll_id, tags, tier_counts=tier_counts, appeal_counts=appeal_counts)


class PollView(discord.ui.View):
    def __init__(
        self,
        poll_id: int,
        tags: list,
        *,
        tier_counts: dict[str, int] | None = None,
        appeal_counts: dict[int, int] | None = None,
        disabled: bool = False,
    ):
        super().__init__(timeout=None)
        tier_counts = tier_counts or {}
        # Always present, even with zero tags configured — TagSelect disables
        # itself in that case rather than the poll silently having no appeal
        # question at all.
        self.add_item(TagSelect(poll_id, tags, appeal_counts, disabled=disabled))
        for tier in TIERS:
            self.add_item(
                TierButton(poll_id, tier, tier_counts.get(tier, 0), disabled=disabled)
            )
