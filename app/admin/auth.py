from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .. import db
from ..auth import hash_password, verify_password

_security = HTTPBasic()

# Computed once, used whenever the username doesn't exist, so a lookup miss
# still runs a PBKDF2 verification of comparable cost — without this, "no
# such user" would return measurably faster than "wrong password", which is
# a username-enumeration side channel via response timing.
_DUMMY_HASH = hash_password("not-a-real-password")


async def require_admin(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
) -> str:
    conn = request.app.state.db
    user = await db.get_user_by_username(conn, credentials.username)
    stored_hash = user["password_hash"] if user is not None else _DUMMY_HASH
    valid = verify_password(credentials.password, stored_hash)

    if user is None or not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
