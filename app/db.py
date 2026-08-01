from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite


async def connect(path: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Users (admin accounts — created only via cli/create_user.py)
# ---------------------------------------------------------------------------


async def get_user_by_username(conn: aiosqlite.Connection, username: str) -> aiosqlite.Row | None:
    cursor = await conn.execute("SELECT * FROM users WHERE username = ?", (username,))
    return await cursor.fetchone()


async def create_user(conn: aiosqlite.Connection, *, username: str, password_hash: str) -> int:
    cursor = await conn.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, password_hash, _now()),
    )
    await conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Sessions (admin login — server-side, cookie only carries the opaque id)
# ---------------------------------------------------------------------------


async def create_session(
    conn: aiosqlite.Connection, *, session_id: str, user_id: int, expires_at: str
) -> None:
    await conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (session_id, user_id, _now(), expires_at),
    )
    await conn.commit()


async def get_session_with_user(
    conn: aiosqlite.Connection, session_id: str
) -> aiosqlite.Row | None:
    cursor = await conn.execute(
        """
        SELECT sessions.id AS session_id, sessions.expires_at,
               users.id AS user_id, users.username
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.id = ?
        """,
        (session_id,),
    )
    return await cursor.fetchone()


async def delete_session(conn: aiosqlite.Connection, session_id: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    await conn.commit()


async def delete_expired_sessions(conn: aiosqlite.Connection) -> None:
    await conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
    await conn.commit()


# ---------------------------------------------------------------------------
# Archetype tags
# ---------------------------------------------------------------------------


async def create_tag(conn: aiosqlite.Connection, *, name: str) -> int:
    cursor = await conn.execute(
        "INSERT INTO archetype_tags (name, created_at) VALUES (?, ?)", (name, _now())
    )
    await conn.commit()
    return cursor.lastrowid


async def list_tags(conn: aiosqlite.Connection) -> list[aiosqlite.Row]:
    cursor = await conn.execute("SELECT * FROM archetype_tags ORDER BY name")
    return await cursor.fetchall()


async def get_tag_by_name(conn: aiosqlite.Connection, name: str) -> aiosqlite.Row | None:
    cursor = await conn.execute(
        "SELECT * FROM archetype_tags WHERE lower(name) = lower(?)", (name,)
    )
    return await cursor.fetchone()


async def delete_tag(conn: aiosqlite.Connection, tag_id: int) -> None:
    await conn.execute("DELETE FROM archetype_tags WHERE id = ?", (tag_id,))
    await conn.commit()


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------


async def create_character(
    conn: aiosqlite.Connection,
    *,
    name: str,
    series: str | None,
    image_path: str,
    source_url: str | None,
    uploaded_by: int | None,
) -> int:
    cursor = await conn.execute(
        """
        INSERT INTO characters (name, series, image_path, source_url, uploaded_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, series, image_path, source_url, uploaded_by, _now()),
    )
    await conn.commit()
    return cursor.lastrowid


async def list_characters(
    conn: aiosqlite.Connection, *, unused_only: bool = False
) -> list[aiosqlite.Row]:
    if unused_only:
        query = """
            SELECT characters.* FROM characters
            LEFT JOIN polls ON polls.character_id = characters.id
            WHERE polls.id IS NULL
            ORDER BY characters.created_at DESC
        """
    else:
        query = "SELECT * FROM characters ORDER BY created_at DESC"
    cursor = await conn.execute(query)
    return await cursor.fetchall()


async def get_character(conn: aiosqlite.Connection, character_id: int) -> aiosqlite.Row | None:
    cursor = await conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,))
    return await cursor.fetchone()


async def find_character_by_name_series(
    conn: aiosqlite.Connection, *, name: str, series: str | None
) -> aiosqlite.Row | None:
    cursor = await conn.execute(
        """
        SELECT * FROM characters
        WHERE lower(name) = lower(?) AND lower(COALESCE(series, '')) = lower(COALESCE(?, ''))
        """,
        (name, series),
    )
    return await cursor.fetchone()


async def delete_character(conn: aiosqlite.Connection, character_id: int) -> None:
    await conn.execute("DELETE FROM characters WHERE id = ?", (character_id,))
    await conn.commit()


async def pick_random_unused_character(conn: aiosqlite.Connection) -> aiosqlite.Row | None:
    cursor = await conn.execute(
        """
        SELECT characters.* FROM characters
        LEFT JOIN polls ON polls.character_id = characters.id
        WHERE polls.id IS NULL
        ORDER BY RANDOM()
        LIMIT 1
        """
    )
    return await cursor.fetchone()


# ---------------------------------------------------------------------------
# Guild config (singleton row, id = 1)
# ---------------------------------------------------------------------------


async def get_guild_config(conn: aiosqlite.Connection) -> aiosqlite.Row:
    cursor = await conn.execute("SELECT * FROM guild_config WHERE id = 1")
    row = await cursor.fetchone()
    assert row is not None, "guild_config singleton row missing — migration 0001 seeds it"
    return row


async def update_guild_config(
    conn: aiosqlite.Connection,
    *,
    channel_id: int | None,
    poll_post_time: str,
    poll_timezone: str,
) -> None:
    await conn.execute(
        "UPDATE guild_config SET channel_id = ?, poll_post_time = ?, poll_timezone = ? WHERE id = 1",
        (channel_id, poll_post_time, poll_timezone),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------


async def create_poll(
    conn: aiosqlite.Connection,
    *,
    character_id: int,
    channel_id: int,
    message_id: int | None,
    posted_at: str,
) -> int:
    cursor = await conn.execute(
        """
        INSERT INTO polls (character_id, channel_id, message_id, status, posted_at)
        VALUES (?, ?, ?, 'open', ?)
        """,
        (character_id, channel_id, message_id, posted_at),
    )
    await conn.commit()
    return cursor.lastrowid


async def set_poll_message_id(conn: aiosqlite.Connection, poll_id: int, message_id: int) -> None:
    await conn.execute("UPDATE polls SET message_id = ? WHERE id = ?", (message_id, poll_id))
    await conn.commit()


async def get_open_poll(conn: aiosqlite.Connection) -> aiosqlite.Row | None:
    cursor = await conn.execute("SELECT * FROM polls WHERE status = 'open' LIMIT 1")
    return await cursor.fetchone()


async def get_poll(conn: aiosqlite.Connection, poll_id: int) -> aiosqlite.Row | None:
    cursor = await conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,))
    return await cursor.fetchone()


async def close_poll(
    conn: aiosqlite.Connection, poll_id: int, *, closed_at: str, result_tier: str | None
) -> None:
    await conn.execute(
        "UPDATE polls SET status = 'closed', closed_at = ?, result_tier = ? WHERE id = ?",
        (closed_at, result_tier, poll_id),
    )
    await conn.commit()


async def list_polls(conn: aiosqlite.Connection, *, limit: int = 20) -> list[aiosqlite.Row]:
    cursor = await conn.execute(
        """
        SELECT polls.*, characters.name AS character_name, characters.series AS character_series
        FROM polls
        JOIN characters ON characters.id = polls.character_id
        ORDER BY polls.posted_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return await cursor.fetchall()


async def get_most_recent_closed_poll(conn: aiosqlite.Connection) -> aiosqlite.Row | None:
    cursor = await conn.execute(
        "SELECT * FROM polls WHERE status = 'closed' ORDER BY closed_at DESC LIMIT 1"
    )
    return await cursor.fetchone()


# ---------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------


async def upsert_appeal_vote(
    conn: aiosqlite.Connection,
    *,
    poll_id: int,
    user_id: int,
    tag_id: int,
    display_name: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO appeal_votes (poll_id, user_id, tag_id, display_name) VALUES (?, ?, ?, ?)
        ON CONFLICT (poll_id, user_id)
        DO UPDATE SET tag_id = excluded.tag_id, display_name = excluded.display_name
        """,
        (poll_id, user_id, tag_id, display_name),
    )
    await conn.commit()


async def upsert_tier_vote(
    conn: aiosqlite.Connection,
    *,
    poll_id: int,
    user_id: int,
    tier: str,
    display_name: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO tier_votes (poll_id, user_id, tier, display_name) VALUES (?, ?, ?, ?)
        ON CONFLICT (poll_id, user_id)
        DO UPDATE SET tier = excluded.tier, display_name = excluded.display_name
        """,
        (poll_id, user_id, tier, display_name),
    )
    await conn.commit()


async def get_appeal_vote_counts(conn: aiosqlite.Connection, poll_id: int) -> dict[int, int]:
    cursor = await conn.execute(
        "SELECT tag_id, COUNT(*) AS c FROM appeal_votes WHERE poll_id = ? GROUP BY tag_id",
        (poll_id,),
    )
    return {row["tag_id"]: row["c"] for row in await cursor.fetchall()}


async def get_tier_vote_counts(conn: aiosqlite.Connection, poll_id: int) -> dict[str, int]:
    cursor = await conn.execute(
        "SELECT tier, COUNT(*) AS c FROM tier_votes WHERE poll_id = ? GROUP BY tier", (poll_id,)
    )
    return {row["tier"]: row["c"] for row in await cursor.fetchall()}


async def get_appeal_votes(conn: aiosqlite.Connection, poll_id: int) -> list[aiosqlite.Row]:
    """Per-voter detail (user_id, tag_id, display_name) for a poll — not just
    the aggregate counts above. Used by the admin poll-detail page."""
    cursor = await conn.execute(
        "SELECT user_id, tag_id, display_name FROM appeal_votes WHERE poll_id = ?", (poll_id,)
    )
    return await cursor.fetchall()


async def get_tier_votes(conn: aiosqlite.Connection, poll_id: int) -> list[aiosqlite.Row]:
    """Per-voter detail (user_id, tier, display_name) for a poll — not just
    the aggregate counts above. Used by the admin poll-detail page."""
    cursor = await conn.execute(
        "SELECT user_id, tier, display_name FROM tier_votes WHERE poll_id = ?", (poll_id,)
    )
    return await cursor.fetchall()
