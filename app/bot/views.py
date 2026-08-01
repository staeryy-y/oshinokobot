from __future__ import annotations

import discord

from .. import db

TIERS = ["S", "A", "B", "C", "D"]

# A message caps out at 25 components total (5 rows x 5 each). The tier row
# always takes a full row of 5, leaving 4 rows for tag buttons — so 20 is
# the real ceiling, not Discord's per-select 25 (this used to be a select
# menu; now every tag is its own button, and buttons are the tighter
# constraint). Past this, the daily poll only offers the first 20.
MAX_TAG_BUTTONS = 20

# Buttons max out at 5 per row; tag buttons fill rows 0-3, tier buttons
# always sit alone on row 4 — a full empty row of visual gap is what makes
# "these are two different questions" obvious without needing anything
# fancier than discord.py's classic component layout.
TIER_ROW = 4


def _truncate(label: str, limit: int = 80) -> str:
    return label if len(label) <= limit else label[: limit - 1] + "…"


class TierButton(discord.ui.Button):
    def __init__(self, poll_id: int, tier: str, count: int, *, disabled: bool = False):
        label = f"{tier} ({count})" if count > 0 else tier
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success if count > 0 else discord.ButtonStyle.secondary,
            custom_id=f"tier:{poll_id}:{tier}",
            row=TIER_ROW,
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


class TagButton(discord.ui.Button):
    def __init__(self, poll_id: int, tag, count: int, *, row: int, disabled: bool = False):
        label = f"{tag['name']} ({count})" if count > 0 else tag["name"]
        super().__init__(
            label=_truncate(label),
            style=discord.ButtonStyle.primary if count > 0 else discord.ButtonStyle.secondary,
            custom_id=f"appeal:{poll_id}:{tag['id']}",
            row=row,
            disabled=disabled,
        )
        self.poll_id = poll_id
        self.tag_id = tag["id"]

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        poll = await db.get_poll(bot.db, self.poll_id)  # type: ignore[attr-defined]
        if poll is None or poll["status"] != "open":
            await interaction.response.send_message(
                "This poll isn't open anymore.", ephemeral=True
            )
            return

        await db.upsert_appeal_vote(  # type: ignore[attr-defined]
            bot.db, poll_id=self.poll_id, user_id=interaction.user.id, tag_id=self.tag_id
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
        appeal_counts = appeal_counts or {}

        # Always present, even with zero tags configured — a disabled
        # placeholder rather than the poll silently having no appeal
        # question at all.
        if not tags:
            self.add_item(
                discord.ui.Button(
                    label="No tags configured yet",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"appeal-empty:{poll_id}",
                    row=0,
                    disabled=True,
                )
            )
        else:
            for index, tag in enumerate(tags[:MAX_TAG_BUTTONS]):
                self.add_item(
                    TagButton(
                        poll_id,
                        tag,
                        appeal_counts.get(tag["id"], 0),
                        row=index // 5,
                        disabled=disabled,
                    )
                )

        for tier in TIERS:
            self.add_item(
                TierButton(poll_id, tier, tier_counts.get(tier, 0), disabled=disabled)
            )
