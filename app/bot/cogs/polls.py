from __future__ import annotations

import logging
import random
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ... import db
from .. import views

logger = logging.getLogger("oshinokobot.bot.polls")


def _poll_local_date(poll_row, tz: ZoneInfo) -> date:
    posted_at = datetime.fromisoformat(poll_row["posted_at"])
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return posted_at.astimezone(tz).date()


# Caps how many names one line lists before falling back to "+N more" —
# a safety net against a single embed field blowing past Discord's 1024
# character limit on a poll with a lot of voters piled onto one tier/tag,
# not something expected to matter at this bot's normal scale.
MAX_NAMES_PER_LINE = 10


def _voter_name(vote_row) -> str:
    return vote_row["display_name"] or f"user {vote_row['user_id']}"


def _join_names(names: list[str]) -> str:
    if len(names) <= MAX_NAMES_PER_LINE:
        return ", ".join(names)
    shown = names[:MAX_NAMES_PER_LINE]
    return ", ".join(shown) + f", +{len(names) - MAX_NAMES_PER_LINE} more"


def _format_tier_results(tier_votes: list) -> str:
    """Grouped by tier, listing who voted for it — 'S: Bob, Jim' rather
    than just a count — for every tier row, not just the ones with
    votes, so the shape stays a consistent 5-line block. This is one
    poll's voters, not the server's cumulative history — see
    _format_cumulative_tier_list for that."""
    grouped: dict[str, list[str]] = {}
    for vote in tier_votes:
        grouped.setdefault(vote["tier"], []).append(_voter_name(vote))
    return "\n".join(
        f"**{tier}**: {_join_names(grouped[tier])}" if grouped.get(tier) else f"**{tier}**: —"
        for tier in views.TIERS
    )


def _format_appeal_results(appeal_votes: list, tags_by_id: dict[int, str]) -> str:
    """Grouped by tag, listing who voted for it. Unlike tier results, only
    tags that actually got a vote are shown — with up to 25 possible tags,
    a full always-show-every-row listing would get noisy fast — ranked by
    voter count, most-picked first."""
    if not appeal_votes:
        return "No votes"
    grouped: dict[str, list[str]] = {}
    for vote in appeal_votes:
        tag_name = tags_by_id.get(vote["tag_id"], "unknown tag")
        grouped.setdefault(tag_name, []).append(_voter_name(vote))
    ranked = sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True)
    return "\n".join(f"**{tag_name}**: {_join_names(names)}" for tag_name, names in ranked)


def _format_cumulative_tier_list(dubbed_rows: list) -> str:
    """Every closed poll's character, grouped by its official dub, across
    the server's whole history — the running tier list this bot is
    actually for, as opposed to any single day's poll. Characters that
    closed with no votes (result_tier is NULL) get their own trailing
    line rather than being silently dropped."""
    grouped: dict[str | None, list[str]] = {}
    for row in dubbed_rows:
        grouped.setdefault(row["result_tier"], []).append(row["character_name"])

    lines = [
        f"**{tier}**: {_join_names(grouped[tier])}" if grouped.get(tier) else f"**{tier}**: —"
        for tier in views.TIERS
    ]
    undubbed = grouped.get(None, [])
    if undubbed:
        lines.append(f"**Not dubbed** (no votes): {_join_names(undubbed)}")
    return "\n".join(lines)


def _compute_result_tier(tier_counts: dict[str, int]) -> str | None:
    """The character's official dub: whichever tier got the most votes.
    Zero tier votes (empty dict — get_tier_vote_counts only includes tiers
    with at least one vote) means no dub at all, not a default one. A tie
    among the top tier(s) is broken with a random pick among just those
    tied tiers, not all five."""
    if not tier_counts:
        return None
    top_count = max(tier_counts.values())
    winners = [tier for tier, count in tier_counts.items() if count == top_count]
    return random.choice(winners)


def _format_dub(result_tier: str | None) -> str:
    return f"**{result_tier}**" if result_tier else "No tier votes — not dubbed"


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

        active_series = db.parse_active_series(config)
        unused = await db.list_characters(
            self.bot.db, unused_only=True, active_series=active_series
        )
        if not unused:
            if active_series:
                return False, (
                    "No unused characters left for the active game(s) "
                    f"({', '.join(active_series)}) — upload more, or widen the game filter in Config."
                )
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

        active_series = db.parse_active_series(config)
        character = await db.pick_random_unused_character(self.bot.db, active_series=active_series)
        if character is None:
            if active_series:
                logger.warning(
                    "no unused characters left for active game(s) %s — skipping today's poll",
                    active_series,
                )
            else:
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
        # Labels the two button groups below — the row gap between them
        # (tags fill rows 0-3, tiers always sit alone on row 4) is the visual
        # separation; these headers are what actually says what each is for.
        embed.add_field(name="Who would this appeal to?", value="Tap a tag below.", inline=False)
        embed.add_field(
            name="What tier would you rate this character?",
            value="Tap S–D below.",
            inline=False,
        )
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
        tags = await db.list_tags(self.bot.db)
        # Raw per-voter rows for the "who voted for what" text below;
        # counts separately for _compute_result_tier and the closed
        # view's button labels (buttons only have room for a number, not
        # a roster of names).
        tier_votes = await db.get_tier_votes(self.bot.db, poll["id"])
        appeal_votes = await db.get_appeal_votes(self.bot.db, poll["id"])
        tier_counts = await db.get_tier_vote_counts(self.bot.db, poll["id"])
        appeal_counts = await db.get_appeal_vote_counts(self.bot.db, poll["id"])
        tags_by_id = {t["id"]: t["name"] for t in tags}
        result_tier = _compute_result_tier(tier_counts)

        await db.close_poll(
            self.bot.db,
            poll["id"],
            closed_at=datetime.now(timezone.utc).isoformat(),
            result_tier=result_tier,
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

        embed = message.embeds[0] if message.embeds else discord.Embed()
        embed.add_field(
            name="Final tier results", value=_format_tier_results(tier_votes), inline=False
        )
        embed.add_field(
            name="Final appeal results",
            value=_format_appeal_results(appeal_votes, tags_by_id),
            inline=False,
        )
        embed.add_field(name="Officially dubbed", value=_format_dub(result_tier), inline=False)
        embed.set_footer(text="Poll closed")

        # Passing the real tags/counts (not empty) so the disabled buttons
        # left on the message still reflect the final state, matching what
        # the "Final results" fields above them say.
        closed_view = views.PollView(
            poll["id"], tags, tier_counts=tier_counts, appeal_counts=appeal_counts, disabled=True
        )
        await message.edit(embed=embed, view=closed_view)

    @app_commands.command(
        name="results", description="Show the server's cumulative tier list so far"
    )
    async def results(self, interaction: discord.Interaction) -> None:
        dubbed = await db.list_dubbed_characters(self.bot.db)
        if not dubbed:
            await interaction.response.send_message("No polls have finished yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Tier list so far",
            description=_format_cumulative_tier_list(dubbed),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{len(dubbed)} character{'s' if len(dubbed) != 1 else ''} polled")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="force-poll", description="Close the current poll (if any) and post a new one now"
    )
    @app_commands.default_permissions(manage_guild=True)
    async def force_poll(self, interaction: discord.Interaction) -> None:
        # Deferred: post_new_poll closes the previous message (an edit) and
        # sends a new one with a file attachment, which can take longer than
        # Discord's 3-second initial-response window.
        await interaction.response.defer(ephemeral=True)
        ok, message = await self.post_new_poll()
        await interaction.followup.send(message, ephemeral=True)
