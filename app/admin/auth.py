from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request

from .. import db
from ..auth import hash_password, verify_password

SESSION_COOKIE_NAME = "oshinoko_session"
SESSION_TTL = timedelta(days=7)

# Computed once, used whenever the username doesn't exist, so a lookup miss
# still runs a PBKDF2 verification of comparable cost — without this, "no
# such user" would return measurably faster than "wrong password", which is
# a username-enumeration side channel via response timing.
_DUMMY_HASH = hash_password("not-a-real-password")


class NotAuthenticated(Exception):
    """Raised by require_admin; caught by an app-level handler that redirects
    to the login page (see server.py) rather than returning a bare 401."""


async def authenticate(conn, *, username: str, password: str) -> int | None:
    """Verifies credentials, timing-safe against a lookup miss. Returns the
    user id on success, None otherwise."""
    user = await db.get_user_by_username(conn, username)
    stored_hash = user["password_hash"] if user is not None else _DUMMY_HASH
    valid = verify_password(password, stored_hash)
    if user is None or not valid:
        return None
    return user["id"]


async def create_session(conn, *, user_id: int) -> str:
    session_id = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + SESSION_TTL).isoformat()
    await db.create_session(conn, session_id=session_id, user_id=user_id, expires_at=expires_at)
    return session_id


async def require_admin(request: Request) -> str:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id is None:
        raise NotAuthenticated()

    conn = request.app.state.db
    session = await db.get_session_with_user(conn, session_id)
    if session is None:
        raise NotAuthenticated()

    expires_at = datetime.fromisoformat(session["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        await db.delete_session(conn, session_id)
        raise NotAuthenticated()

    return session["username"]
