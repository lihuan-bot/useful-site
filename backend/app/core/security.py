"""Password hashing (bcrypt) and JWT signing/verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: str, *, secret: str, expire_minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, *, secret: str) -> str | None:
    """Return the ``sub`` claim (user id) or None if invalid/expired."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"]).get("sub")
    except jwt.PyJWTError:
        return None
