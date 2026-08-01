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
        logger.info("bot setup complete")
