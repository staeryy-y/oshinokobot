from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import db
from ..config import Config
from .routes import characters
from .templating import STATIC_DIR

logger = logging.getLogger("oshinokobot.admin")


def create_app(config: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = await db.connect(config.db_path)
        app.state.db = conn
        app.state.config = config
        try:
            yield
        finally:
            await conn.close()

    app = FastAPI(lifespan=lifespan)
    app.mount("/admin/static", StaticFiles(directory=str(STATIC_DIR)), name="admin-static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        # Deliberately unauthenticated and separate from /admin/* — watcher's
        # poller shouldn't ever hit a Basic Auth prompt.
        return {"status": "ok"}

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/admin/characters")

    @app.get("/admin")
    async def admin_root() -> RedirectResponse:
        return RedirectResponse(url="/admin/characters")

    app.include_router(characters.router)

    return app
