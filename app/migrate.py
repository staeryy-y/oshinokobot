from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .config import db_path_from_env
from .majority import pick_majority

logger = logging.getLogger("oshinokobot.migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


async def _backfill_poll_results(conn: aiosqlite.Connection) -> None:
    """result_tier (migration 0004) and result_tag_id (0006) are computed
    once, at the moment a poll closes — but that means any poll that
    closed *before* those columns/that code existed has NULL there
    forever, even though its votes are still sitting right there in
    tier_votes/appeal_votes untouched. That showed up as a real bug: the
    public results page trusts the stored column, so those characters
    looked like they had no result at all despite having real votes.

    This computes both fields for any closed poll where they're still
    NULL, using the same pick_majority rule the live code uses at close
    time, and only writes the column that's actually missing (COALESCE)
    so a poll with one field already set never has that one recomputed —
    reapplying pick_majority to the same tied vote counts could otherwise
    reassign a different random winner on a later run.
    """
    cursor = await conn.execute(
        "SELECT id FROM polls WHERE status = 'closed' AND (result_tier IS NULL OR result_tag_id IS NULL)"
    )
    poll_ids = [row[0] for row in await cursor.fetchall()]
    if not poll_ids:
        return

    backfilled = 0
    for poll_id in poll_ids:
        tier_cursor = await conn.execute(
            "SELECT tier, COUNT(*) AS c FROM tier_votes WHERE poll_id = ? GROUP BY tier", (poll_id,)
        )
        tier_counts = {row[0]: row[1] for row in await tier_cursor.fetchall()}

        appeal_cursor = await conn.execute(
            "SELECT tag_id, COUNT(*) AS c FROM appeal_votes WHERE poll_id = ? GROUP BY tag_id",
            (poll_id,),
        )
        appeal_counts = {row[0]: row[1] for row in await appeal_cursor.fetchall()}

        result_tier = pick_majority(tier_counts)
        result_tag_id = pick_majority(appeal_counts)
        if result_tier is None and result_tag_id is None:
            continue  # genuinely no votes on either question — NULL is correct, not missing

        await conn.execute(
            """
            UPDATE polls
            SET result_tier = COALESCE(result_tier, ?), result_tag_id = COALESCE(result_tag_id, ?)
            WHERE id = ?
            """,
            (result_tier, result_tag_id, poll_id),
        )
        backfilled += 1

    if backfilled:
        await conn.commit()
        logger.info("backfilled result_tier/result_tag_id for %d closed poll(s)", backfilled)


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

        await _backfill_poll_results(conn)
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
