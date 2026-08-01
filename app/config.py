from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    discord_token: str | None
    guild_ids: list[int]
    host: str
    port: int
    db_path: str
    log_path: str
    media_dir: str


def load_config() -> Config:
    # Deliberately optional: the admin site is useful on its own (managing
    # characters/tags ahead of time, reviewing past poll results), so it
    # shouldn't be impossible to start without a Discord bot token. When
    # unset, server.py just skips starting the bot and logs that it's
    # running admin-only.
    token = os.environ.get("DISCORD_BOT_TOKEN") or None

    # Comma-separated, e.g. "123,456" — same convention as scheduler-bot.
    # Not read anywhere yet (this bot has no slash commands to sync), but
    # kept as a list rather than a single id since that's the shape any
    # future guild-scoped command sync would actually need.
    guild_ids_raw = os.environ.get("DISCORD_GUILD_ID", "")
    guild_ids = [int(g.strip()) for g in guild_ids_raw.split(",") if g.strip()]

    return Config(
        discord_token=token,
        guild_ids=guild_ids,
        # HOST/PORT are injected by server-watcher; the defaults only matter for local runs.
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        db_path=db_path_from_env(),
        log_path=os.environ.get("LOG_PATH", "oshinokobot.log"),
        media_dir=os.environ.get("MEDIA_DIR", "media"),
    )


def db_path_from_env() -> str:
    """Split out from load_config() so tools that only touch the database
    (the CLI, the migration runner) don't need DISCORD_BOT_TOKEN set."""
    return os.environ.get("OSHINOKO_DB_PATH", "oshinoko.db")
