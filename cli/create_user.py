from __future__ import annotations

import asyncio
import getpass
import sys

from app import db
from app.auth import hash_password
from app.config import db_path_from_env

# Deliberately the only way to create an admin account — no signup route,
# no seed data. Whoever has shell access to the host controls who can log
# into the admin UI. Never accepts a password as a CLI argument (shell
# history / process list would leak it); always prompts interactively.


async def _create(username: str, password: str) -> None:
    conn = await db.connect(db_path_from_env())
    try:
        existing = await db.get_user_by_username(conn, username)
        if existing is not None:
            print(f"User {username!r} already exists.", file=sys.stderr)
            raise SystemExit(1)
        await db.create_user(conn, username=username, password_hash=hash_password(password))
        print(f"Created user {username!r}.")
    finally:
        await conn.close()


def main() -> None:
    username = input("Username: ").strip()
    if not username:
        print("Username cannot be empty.", file=sys.stderr)
        raise SystemExit(1)

    password = getpass.getpass("Password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        raise SystemExit(1)
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(1)

    asyncio.run(_create(username, password))


if __name__ == "__main__":
    main()
