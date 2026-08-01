from __future__ import annotations

import logging

import aiosqlite
import discord
from discord.ext import commands

from ..config import Config
from .cogs.polls import Polls

logger = logging.getLogger("oshinokobot.bot")


class OshinokoBot(commands.Bot):
    def __init__(self, config: Config, conn: aiosqlite.Connection):
        # No message content / member list / presence needed — voting is
        # entirely button/select interactions on messages the bot itself sent.
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.db = conn

    async def setup_hook(self) -> None:
        await self.add_cog(Polls(self))

        # Global sync always runs, so /results works in any guild the bot's
        # in — known ahead of time or not — within Discord's usual
        # propagation delay (up to ~1hr). Guild ids listed in
        # DISCORD_GUILD_ID additionally get an instant copy, for whichever
        # server that wait is annoying in.
        await self.tree.sync()
        for guild_id in self.config.guild_ids:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)

        logger.info("bot setup complete")
