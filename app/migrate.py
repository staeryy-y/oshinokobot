from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .config import db_path_from_env

logger = logging.getLogger("oshinokobot.migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


async def run_migrations(db_path: str) -> None:
    conn = await aiosqlite.connect(db_path)
    try:
        # Off during migrations specifically: a table-rebuild migration (the
        # standard SQLite way to change a CHECK constraint or drop a column)
        # can need to drop a still-referenced parent table mid-script, which
        # FK enforcement would otherwise block. Runtime enforcement is
        # separate and unaffected — db.connect() turns it back on.
        await conn.execute("PRAGMA foreign_keys = OFF")
        await conn.execute(TRACKING_TABLE)
        await conn.commit()

        cursor = await conn.execute("SELECT name FROM schema_migrations")
        applied = {row[0] for row in await cursor.fetchall()}

        pending = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in applied)
        for path in pending:
            logger.info("applying migration %s", path.name)
            await conn.executescript(path.read_text())
            await conn.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                (path.name, datetime.now(timezone.utc).isoformat()),
            )
            await conn.commit()

        if not pending:
            logger.info("schema already up to date")
    finally:
        await conn.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(run_migrations(db_path_from_env()))


if __name__ == "__main__":
    main()
