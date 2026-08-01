from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required deployment configuration is missing."""


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int | None
    host: str
    port: int
    db_path: str
    log_path: str
    media_dir: str


def load_config() -> Config:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise ConfigError("DISCORD_BOT_TOKEN is not set in the environment")

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
