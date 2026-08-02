from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

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
    polls,
    public_results,
    tags,
)
from .templating import STATIC_DIR

logger = logging.getLogger("oshinokobot.admin")


async def _run_bot(bot: OshinokoBot, token: str) -> None:
    try:
        await bot.start(token)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Logged loudly (stdout, which watcher captures) rather than crashing
        # the whole process — the admin UI staying up is still useful even if
        # Discord connectivity is broken (e.g. a bad DISCORD_BOT_TOKEN), and
        # fixing that needs a host-level .env edit + restart either way.
        logger.exception("Discord bot crashed — admin UI keeps running without it")


def create_app(config: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = await db.connect(config.db_path)
        app.state.db = conn
        app.state.config = config

        bot: OshinokoBot | None = None
        bot_task: asyncio.Task | None = None
        if config.discord_token is not None:
            bot = OshinokoBot(config, conn)
            app.state.bot = bot
            bot_task = asyncio.create_task(_run_bot(bot, config.discord_token))
        else:
            app.state.bot = None
            logger.info("DISCORD_BOT_TOKEN not set — running admin-only, Discord bot disabled")

        try:
            yield
        finally:
            if bot is not None:
                await bot.close()
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
    app.include_router(public_results.router)

    return app
