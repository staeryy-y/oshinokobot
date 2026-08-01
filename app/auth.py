from __future__ import annotations

import hashlib
import hmac
import os

# Deliberately stdlib-only (no bcrypt/argon2): those need a C extension, and
# watcher's host doesn't guarantee a compiler (see PLAN.md / watcher spec).
# PBKDF2-HMAC-SHA256 needs nothing beyond hashlib.
_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_hex, hash_hex = stored_hash.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(hash_hex)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations_raw))
    return hmac.compare_digest(derived, expected)
