from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

import aiosqlite
import discord
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import db
from ..bot.client import OshinokoBot
from ..config import Config
from .auth import NotAuthenticated
from .routes import (
    auth as auth_routes,
    characters,
    config as config_routes,
    export,
    polls,
    public_results,
    tags,
)
from .templating import STATIC_DIR

logger = logging.getLogger("oshinokobot.admin")

RECONNECT_BACKOFF_INITIAL = 15  # seconds
RECONNECT_BACKOFF_MAX = 300  # seconds


async def _run_bot(app: FastAPI, config: Config, conn: aiosqlite.Connection, token: str) -> None:
    """Keeps a Discord connection alive for the app's lifetime.

    discord.py's own Client.connect() already retries forever on ordinary
    network blips (OSError, ConnectionClosed, etc. — with backoff), but it
    only catches that specific set of exception types. Anything else —
    we've seen a uvloop RuntimeError ("File descriptor N is used by
    transport ...") escape a reconnect attempt in production — comes
    straight out of bot.start() uncaught. Without a wrapper here, that
    silently kills the gateway connection (so voting buttons and
    /force-poll stop working) for the rest of the process's life, while the
    admin UI and REST-only actions like the manual poll-trigger button keep
    working fine and give no sign anything's wrong.

    A discord.py Client can't be restarted after start() exits, so every
    retry constructs a fresh OshinokoBot and republishes it to
    app.state.bot, which admin routes read to reach "the current bot".
    """
    backoff = RECONNECT_BACKOFF_INITIAL
    while True:
        bot = OshinokoBot(config, conn)
        app.state.bot = bot
        try:
            await bot.start(token)
            return  # start() only returns after a clean close(), i.e. shutdown
        except asyncio.CancelledError:
            raise
        except (discord.LoginFailure, discord.PrivilegedIntentsRequired):
            # Not transient: retrying can't fix a bad token or a Developer
            # Portal intents toggle. Needs a human + a restart either way.
            logger.exception("Discord bot misconfigured — admin UI keeps running without it")
            app.state.bot = None
            return
        except Exception:
            logger.exception(
                "Discord bot's gateway connection died unexpectedly — reconnecting in %ds", backoff
            )
        finally:
            with contextlib.suppress(Exception):
                await bot.close()

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)


def create_app(config: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = await db.connect(config.db_path)
        app.state.db = conn
        app.state.config = config

        bot_task: asyncio.Task | None = None
        app.state.bot = None
        if config.discord_token is not None:
            bot_task = asyncio.create_task(_run_bot(app, config, conn, config.discord_token))
        else:
            logger.info("DISCORD_BOT_TOKEN not set — running admin-only, Discord bot disabled")

        try:
            yield
        finally:
            # Cancelling the task is enough — _run_bot's own finally closes
            # whichever bot instance (initial or a reconnect) is current.
            if bot_task is not None:
                bot_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bot_task
            await conn.close()

    app = FastAPI(lifespan=lifespan)
    app.mount("/admin/static", StaticFiles(directory=str(STATIC_DIR)), name="admin-static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        # Deliberately unauthenticated and separate from /admin/* — watcher's
        # poller shouldn't ever get bounced through a login redirect.
        return {"status": "ok"}

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/admin/characters")

    @app.get("/admin")
    async def admin_root() -> RedirectResponse:
        return RedirectResponse(url="/admin/characters")

    @app.exception_handler(NotAuthenticated)
    async def not_authenticated_handler(request: Request, exc: NotAuthenticated) -> RedirectResponse:
        return RedirectResponse(url=f"/admin/login?next={quote(request.url.path)}", status_code=303)

    app.include_router(auth_routes.router)
    app.include_router(characters.router)
    app.include_router(tags.router)
    app.include_router(config_routes.router)
    app.include_router(polls.router)
    app.include_router(export.router)
    app.include_router(public_results.router)

    return app
