from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from ... import db
from .. import views

logger = logging.getLogger("oshinokobot.bot.polls")


def _poll_local_date(poll_row, tz: ZoneInfo) -> date:
    posted_at = datetime.fromisoformat(poll_row["posted_at"])
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return posted_at.astimezone(tz).date()


def _format_tier_results(counts: dict[str, int]) -> str:
    return "\n".join(f"**{t}**: {counts.get(t, 0)}" for t in views.TIERS)


def _format_appeal_results(counts: dict[int, int], tags_by_id: dict[int, str]) -> str:
    if not counts:
        return "No votes"
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return "\n".join(f"**{tags_by_id.get(tag_id, 'unknown')}**: {c}" for tag_id, c in ranked)


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_poll_check.start()

    def cog_unload(self) -> None:
        self.daily_poll_check.cancel()

    async def cog_load(self) -> None:
        # Restart mid-poll shouldn't orphan the voting buttons — re-register
        # a view with the same custom_ids and current counts so clicks on
        # the still-open message keep working (same pattern as scheduler-bot).
        open_poll = await db.get_open_poll(self.bot.db)
        if open_poll is not None:
            updated_view = await views.rebuild_view(self.bot, open_poll["id"])
            self.bot.add_view(updated_view)

    @tasks.loop(minutes=1)
    async def daily_poll_check(self) -> None:
        config = await db.get_guild_config(self.bot.db)
        if config["channel_id"] is None:
            return

        tz = ZoneInfo(config["poll_timezone"])
        now = datetime.now(tz)
        if now.strftime("%H:%M") < config["poll_post_time"]:
            return

        # Idempotency comes from DB state, not an in-memory flag — comparing
        # the most recent poll's posted date (in the configured timezone)
        # survives a bot restart inside the same minute without double-posting.
        recent = await db.list_polls(self.bot.db, limit=1)
        if recent and _poll_local_date(recent[0], tz) == now.date():
            return

        await self._advance_daily_poll(config)

    @daily_poll_check.before_loop
    async def _before_daily_poll_check(self) -> None:
        await self.bot.wait_until_ready()

    async def post_new_poll(self) -> tuple[bool, str]:
        """Manual override for the admin UI's "Post a new poll now" button —
        bypasses poll_post_time entirely and reuses the same close-then-post
        logic the scheduled check uses. Doesn't touch the scheduler's own
        idempotency (comparing today's date against the most recent poll):
        triggering manually before poll_post_time still fires today just
        means the automatic check later sees a poll already exists for today
        and skips, which is the desired "already posted today" behavior."""
        if not self.bot.is_ready():
            return False, "Bot is still connecting — try again in a few seconds."

        config = await db.get_guild_config(self.bot.db)
        if config["channel_id"] is None:
            return False, "No channel configured — set one in Config first."

        unused = await db.list_characters(self.bot.db, unused_only=True)
        if not unused:
            return False, "No unused characters left in the pool — upload more first."

        await self._advance_daily_poll(config)

        open_poll = await db.get_open_poll(self.bot.db)
        if open_poll is None:
            return False, "Poll didn't post — the configured channel may be unreachable (check logs)."

        character = await db.get_character(self.bot.db, open_poll["character_id"])
        return True, f"Posted a new poll for {character['name']}."

    async def _advance_daily_poll(self, config) -> None:
        open_poll = await db.get_open_poll(self.bot.db)
        if open_poll is not None:
            await self._close_poll(open_poll)

        character = await db.pick_random_unused_character(self.bot.db)
        if character is None:
            logger.warning("no unused characters left in the pool — skipping today's poll")
            return

        channel = self.bot.get_channel(config["channel_id"])
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(config["channel_id"])
            except discord.HTTPException:
                logger.error("configured poll channel %s is not reachable", config["channel_id"])
                return

        tags = await db.list_tags(self.bot.db)
        poll_id = await db.create_poll(
            self.bot.db,
            character_id=character["id"],
            channel_id=channel.id,
            message_id=None,
            posted_at=datetime.now(timezone.utc).isoformat(),
        )

        embed = discord.Embed(title=character["name"], color=discord.Color.blurple())
        if character["series"]:
            embed.description = character["series"]
        filename = Path(character["image_path"]).name
        embed.set_image(url=f"attachment://{filename}")

        view = views.PollView(poll_id, tags)
        self.bot.add_view(view)

        message = await channel.send(
            embed=embed,
            file=discord.File(character["image_path"], filename=filename),
            view=view,
        )
        await db.set_poll_message_id(self.bot.db, poll_id, message.id)
        logger.info(
            "posted poll #%s for character %r in channel %s", poll_id, character["name"], channel.id
        )

    async def _close_poll(self, poll) -> None:
        await db.close_poll(
            self.bot.db, poll["id"], closed_at=datetime.now(timezone.utc).isoformat()
        )
        if poll["message_id"] is None:
            return

        try:
            channel = self.bot.get_channel(poll["channel_id"]) or await self.bot.fetch_channel(
                poll["channel_id"]
            )
            message = await channel.fetch_message(poll["message_id"])
        except discord.HTTPException:
            logger.warning("could not fetch poll #%s's message to close it", poll["id"])
            return

        tier_counts = await db.get_tier_vote_counts(self.bot.db, poll["id"])
        appeal_counts = await db.get_appeal_vote_counts(self.bot.db, poll["id"])
        tags_by_id = {t["id"]: t["name"] for t in await db.list_tags(self.bot.db)}

        embed = message.embeds[0] if message.embeds else discord.Embed()
        embed.add_field(
            name="Final tier results", value=_format_tier_results(tier_counts), inline=False
        )
        embed.add_field(
            name="Final appeal results",
            value=_format_appeal_results(appeal_counts, tags_by_id),
            inline=False,
        )
        embed.set_footer(text="Poll closed")

        closed_view = views.PollView(poll["id"], [], disabled=True)
        await message.edit(embed=embed, view=closed_view)
