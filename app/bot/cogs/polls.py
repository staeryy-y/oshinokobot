from __future__ import annotations

import asyncio
import logging
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ... import db
from ...majority import pick_majority
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


def _format_cumulative_tier_list(polled_rows: list) -> str:
    """Every closed poll's character, grouped by its result tier, across
    the server's whole history — the running tier list this bot is
    actually for, as opposed to any single day's poll. Characters that
    closed with no votes (result_tier is NULL) get their own trailing
    line rather than being silently dropped."""
    grouped: dict[str | None, list[str]] = {}
    for row in polled_rows:
        grouped.setdefault(row["result_tier"], []).append(row["character_name"])

    lines = [
        f"**{tier}**: {_join_names(grouped[tier])}" if grouped.get(tier) else f"**{tier}**: —"
        for tier in views.TIERS
    ]
    no_result = grouped.get(None, [])
    if no_result:
        lines.append(f"**No result** (no votes): {_join_names(no_result)}")
    return "\n".join(lines)


def _compute_result_tier(tier_counts: dict[str, int]) -> str | None:
    """The character's result tier: whichever tier got the most votes."""
    return pick_majority(tier_counts)


def _compute_result_tag(appeal_counts: dict[int, int]) -> int | None:
    """The character's "core": whichever appeal tag got the most votes —
    same rule as the result tier, for the other question."""
    return pick_majority(appeal_counts)


def _format_result_tier(result_tier: str | None) -> str:
    return f"**{result_tier}**" if result_tier else "No tier votes — no result"


def _format_core(result_tag_id: int | None, tags_by_id: dict[int, str]) -> str:
    if result_tag_id is None:
        return "No appeal votes — no core"
    return f"**{tags_by_id.get(result_tag_id, 'unknown tag')}**"


NOTE_PROMPT = (
    "Use `/oshinoko-note message:your thoughts` to leave a note on this character."
)



def _character_embed(character) -> discord.Embed:
    embed = discord.Embed(title=character["name"], color=discord.Color.blurple())
    if character["series"]:
        embed.description = character["series"]
    embed.add_field(name="Who would this appeal to?",
                    value="Tap as many tags below as apply — pick more than one if it fits.", inline=False)
    embed.add_field(name="What tier would you rate this character?",
                    value="Tap S–D below.", inline=False)
    embed.set_image(url=f"attachment://{Path(character['image_path']).name}")
    return embed


async def _poll_message_and_embed(channel, poll, conn):
    try:
        post = await channel.fetch_message(poll["message_id"])
        embed = post.embeds[0].copy() if post.embeds else discord.Embed()
        return post, embed
    except discord.Forbidden as error:
        # Editing our own message does not require reading channel history.
        # Reconstruct the embed from persistent data and preserve attachments/buttons.
        logger.warning("poll #%s fetch forbidden (channel=%s code=%s); editing directly",
                       poll["id"], poll["channel_id"], error.code)
        character = await db.get_character(conn, poll["character_id"])
        return channel.get_partial_message(poll["message_id"]), _character_embed(character)


def _set_note_fields(embed: discord.Embed, notes: list) -> None:
    for index in reversed(range(len(embed.fields))):
        if embed.fields[index].name in ("Leave a note", "Anonymous notes"):
            embed.remove_field(index)
    embed.add_field(name="Leave a note", value=NOTE_PROMPT, inline=False)
    # Reserve space for close-time result fields. All notes remain on the website.
    budget = min(2000, max(0, 3900 - len(embed)))
    shown = []
    for note in reversed(notes):
        text = discord.utils.escape_markdown(discord.utils.escape_mentions(note["message"]))
        line = f"• {text}"
        if len(line) + 2 > budget or len(embed.fields) + len(shown) >= 20:
            break
        shown.append(line)
        budget -= len(line) + 2
    for line in reversed(shown):
        embed.add_field(name="Anonymous notes", value=line, inline=False)
    if len(shown) < len(notes) and len(embed.fields) < 21:
        embed.add_field(name="Anonymous notes", value=f"{len(notes) - len(shown)} more notes saved; all notes appear on the public result page when this poll closes.", inline=False)


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._posting_lock = asyncio.Lock()
        self.daily_poll_check.start()

    def cog_unload(self) -> None:
        logger.info("daily poll scheduler stopping — cog unloaded")
        self.daily_poll_check.cancel()

    async def cog_load(self) -> None:
        # Restart mid-poll shouldn't orphan the voting buttons — re-register
        # a view with the same custom_ids and current counts so clicks on
        # the still-open message keep working (same pattern as scheduler-bot).
        # This is the actual restart-safety mechanism for an in-progress
        # poll; logged explicitly (both branches) so every restart leaves a
        # visible record of whether there was a poll to reattach to, rather
        # than this happening silently. Wrapped in try/except so a transient
        # DB hiccup at exactly this moment (cog_load runs inside setup_hook,
        # before the gateway connection completes) can't take down the
        # entire bot login — better to come up with the open poll's buttons
        # un-reattached (fixable by a `/force-poll` or another restart) than
        # to not come up at all.
        try:
            open_poll = await db.get_open_poll(self.bot.db)
            if open_poll is None:
                logger.info("cog_load: no open poll to reattach")
                return
            updated_view = await views.rebuild_view(self.bot, open_poll["id"])
            self.bot.add_view(updated_view)
            logger.info(
                "cog_load: reattached voting buttons for open poll #%s", open_poll["id"]
            )
        except Exception:
            logger.exception(
                "cog_load: failed to reattach the open poll's voting buttons — "
                "its Discord message may stop responding to clicks until the next restart"
            )

    @tasks.loop(minutes=1)
    async def daily_poll_check(self) -> None:
        # discord.ext.tasks silently stops a loop for good the moment its
        # body raises anything it doesn't recognize as a reconnect-able
        # network error (see the .error handler below) — a single transient
        # hiccup (a locked db, a rate limit on channel.send, a bad timezone
        # string saved in config) would otherwise permanently end daily
        # posting with nothing louder than one buried traceback in the logs.
        # Catching everything here means one bad minute just gets retried
        # next minute instead of killing the scheduler.
        try:
            async with self._posting_lock:
                config = await db.get_guild_config(self.bot.db)
                if config["channel_id"] is None:
                    logger.info("daily poll check: skipped — no posting channel configured")
                    return

                tz = ZoneInfo(config["poll_timezone"])
                now = datetime.now(tz)
                logger.info(
                    "daily poll check: local_time=%s timezone=%s scheduled_time=%s channel=%s",
                    now.isoformat(timespec="seconds"), config["poll_timezone"],
                    config["poll_post_time"], config["channel_id"],
                )
                if now.time() < datetime.strptime(config["poll_post_time"], "%H:%M").time():
                    logger.info("daily poll check: skipped — scheduled time has not arrived")
                    return

                # Idempotency comes from DB state, not an in-memory flag —
                # comparing the most recent poll's posted date (in the
                # configured timezone) survives a bot restart inside the same
                # minute without double-posting.
                recent = await db.list_polls(self.bot.db, limit=1, posted_only=True)
                if recent and _poll_local_date(recent[0], tz) == now.date():
                    logger.info(
                        "daily poll check: skipped — poll #%s already posted today (message=%s)",
                        recent[0]["id"], recent[0]["message_id"],
                    )
                    return

                logger.info("daily poll check: due — attempting to post")
                await self._advance_daily_poll(config)
        except Exception:
            logger.exception("daily_poll_check tick failed — will retry next minute")

    @daily_poll_check.before_loop
    async def _before_daily_poll_check(self) -> None:
        logger.info("daily poll scheduler waiting for Discord connection")
        await self.bot.wait_until_ready()
        logger.info("daily poll scheduler started — checking every 60 seconds")

    @daily_poll_check.error
    async def _daily_poll_check_error(self, error: BaseException) -> None:
        # The failed task is still running while this callback executes.
        # restart() registers a callback to start again once that task exits.
        logger.exception("daily_poll_check crashed — restarting the loop", exc_info=error)
        self.daily_poll_check.restart()

    async def post_new_poll(self) -> tuple[bool, str]:
        """Manual override for the admin UI's "Post a new poll now" button —
        bypasses poll_post_time entirely and reuses the same close-then-post
        logic the scheduled check uses. Doesn't touch the scheduler's own
        idempotency (comparing today's date against the most recent poll):
        triggering manually before poll_post_time still fires today just
        means the automatic check later sees a poll already exists for today
        and skips, which is the desired "already posted today" behavior."""
        logger.info("manual poll requested")
        async with self._posting_lock:
            result = await self._post_new_poll()
            logger.info("manual poll result: success=%s detail=%s", *result)
            return result

    async def _post_new_poll(self) -> tuple[bool, str]:
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
            logger.info("closing previous poll #%s before posting the next poll", open_poll["id"])
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

        logger.info(
            "sending poll #%s for character %r (id=%s) in channel %s",
            poll_id, character["name"], character["id"], channel.id,
        )
        view = None
        try:
            embed = _character_embed(character)
            _set_note_fields(embed, [])
            filename = Path(character["image_path"]).name
            embed.set_image(url=f"attachment://{filename}")

            view = views.PollView(poll_id, tags)

            with closing(discord.File(character["image_path"], filename=filename)) as attachment:
                message = await channel.send(embed=embed, file=attachment, view=view)
        except Exception:
            # File access, view construction and transport errors can all
            # fail before a message exists. None should count as today's poll.
            if view is not None:
                view.stop()
            logger.exception(
                "failed to send poll message for character %r in channel %s — freeing the poll",
                character["name"], channel.id,
            )
            await db.delete_poll(self.bot.db, poll_id)
            return

        await db.set_poll_message_id(self.bot.db, poll_id, message.id)
        logger.info(
            "posted poll #%s for character %r in channel %s (message=%s)",
            poll_id, character["name"], channel.id, message.id
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
        result_tag_id = _compute_result_tag(appeal_counts)

        await db.close_poll(
            self.bot.db,
            poll["id"],
            closed_at=datetime.now(timezone.utc).isoformat(),
            result_tier=result_tier,
            result_tag_id=result_tag_id,
        )
        if poll["message_id"] is None:
            return

        try:
            channel = self.bot.get_channel(poll["channel_id"]) or await self.bot.fetch_channel(
                poll["channel_id"]
            )
            message, embed = await _poll_message_and_embed(channel, poll, self.bot.db)
        except discord.HTTPException:
            logger.warning("could not fetch poll #%s's message to close it", poll["id"])
            return

        _set_note_fields(embed, await db.get_poll_notes(self.bot.db, poll["id"], public=True))
        embed.add_field(
            name="Final tier results", value=_format_tier_results(tier_votes), inline=False
        )
        embed.add_field(
            name="Final appeal results",
            value=_format_appeal_results(appeal_votes, tags_by_id),
            inline=False,
        )
        embed.add_field(name="Result tier", value=_format_result_tier(result_tier), inline=False)
        embed.add_field(name="Core", value=_format_core(result_tag_id, tags_by_id), inline=False)
        embed.set_footer(text="Poll closed")

        # Passing the real tags/counts (not empty) so the disabled buttons
        # left on the message still reflect the final state, matching what
        # the "Final results" fields above them say.
        closed_view = views.PollView(
            poll["id"], tags, tier_counts=tier_counts, appeal_counts=appeal_counts, disabled=True
        )
        await message.edit(embed=embed, view=closed_view)

    @app_commands.command(
        name="oshinoko-note", description="Leave a publicly anonymous note on the current character (admins see authors)"
    )
    @app_commands.guild_only()
    @app_commands.describe(message="Your note (1–300 characters; author visible to admins)")
    async def oshinoko_note(self, interaction: discord.Interaction,
                           message: app_commands.Range[str, 1, 300]) -> None:
        await interaction.response.defer(ephemeral=True)
        message = message.strip()
        if not message:
            await interaction.followup.send("Write a non-empty note (up to 300 characters).", ephemeral=True)
            return
        async with self._posting_lock:
            poll = await db.get_open_poll(self.bot.db)
            if poll is None or poll["message_id"] is None:
                await interaction.followup.send("There isn't an open poll to leave a note on.", ephemeral=True)
                return
            try:
                channel = self.bot.get_channel(poll["channel_id"]) or await self.bot.fetch_channel(poll["channel_id"])
                if channel.guild.id != interaction.guild_id:
                    await interaction.followup.send("There isn't an open poll in this server.", ephemeral=True)
                    return
                post, embed = await _poll_message_and_embed(channel, poll, self.bot.db)
            except discord.HTTPException as error:
                logger.warning("could not reach poll #%s (channel=%s message=%s status=%s code=%s)",
                               poll["id"], poll["channel_id"], poll["message_id"], error.status, error.code)
                detail = ("The poll message or channel was deleted. Ask an admin to post a new poll."
                          if isinstance(error, discord.NotFound)
                          else "Couldn't access the poll channel. Ask an admin to check the bot's View Channel permission, or try again later.")
                await interaction.followup.send(detail + " Your note wasn't saved.", ephemeral=True)
                return
            await db.create_poll_note(self.bot.db, poll_id=poll["id"], user_id=interaction.user.id,
                                      display_name=interaction.user.display_name, message=message)
            _set_note_fields(embed, await db.get_poll_notes(self.bot.db, poll["id"], public=True))
            try:
                await post.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as error:
                logger.warning("could not update notes on poll #%s (status=%s code=%s)", poll["id"], error.status, error.code)
                await interaction.followup.send("Note saved, but Discord couldn't update the poll. It will still appear on the public results page after closing. Admins can see the author.", ephemeral=True)
                return
            await interaction.followup.send("Note saved anonymously on the poll. Admins can see the author.", ephemeral=True)

    @app_commands.command(
        name="results", description="Show the server's cumulative tier list so far"
    )
    async def results(self, interaction: discord.Interaction) -> None:
        polled = await db.list_polled_characters(self.bot.db)
        if not polled:
            await interaction.response.send_message("No polls have finished yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Tier list so far",
            description=_format_cumulative_tier_list(polled),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{len(polled)} character{'s' if len(polled) != 1 else ''} polled")
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

    @app_commands.command(
        name="refresh-poll",
        description="Redraw the open poll's buttons (e.g. after adding/removing a tag) without touching votes",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def refresh_poll(self, interaction: discord.Interaction) -> None:
        # Vote buttons already rebuild themselves (fresh tags, fresh counts)
        # on every click — see TierButton/TagButton.callback — so a poll
        # that's still getting votes self-heals onto a newly added tag
        # naturally. This command is for the case where nobody's clicked
        # since the tag changed: it forces that same rebuild from the
        # outside, purely a redraw against current DB state, so existing
        # votes are untouched either way.
        await interaction.response.defer(ephemeral=True)

        open_poll = await db.get_open_poll(self.bot.db)
        if open_poll is None:
            await interaction.followup.send("No poll is currently open.", ephemeral=True)
            return
        if open_poll["message_id"] is None:
            await interaction.followup.send(
                "The open poll doesn't have a message yet — try again in a moment.",
                ephemeral=True,
            )
            return

        try:
            channel = self.bot.get_channel(
                open_poll["channel_id"]
            ) or await self.bot.fetch_channel(open_poll["channel_id"])
            message = await channel.fetch_message(open_poll["message_id"])
        except discord.HTTPException:
            logger.warning("could not fetch poll #%s's message to refresh it", open_poll["id"])
            await interaction.followup.send(
                "Couldn't reach the poll's message — check logs.", ephemeral=True
            )
            return

        updated_view = await views.rebuild_view(self.bot, open_poll["id"])
        await message.edit(view=updated_view)
        await interaction.followup.send("Refreshed the open poll's buttons.", ephemeral=True)
