from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    discord_token: str | None
    guild_id: int | None
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

    guild_id_raw = os.environ.get("DISCORD_GUILD_ID", "").strip()
    guild_id = int(guild_id_raw) if guild_id_raw else None

    return Config(
        discord_token=token,
        guild_id=guild_id,
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
