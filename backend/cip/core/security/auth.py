"""Password hashing and JWT issuance/verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from cip.config import get_settings

ROLES = ("viewer", "analyst", "admin")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: str, org_id: str, role: str, version: int = 0) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "org": org_id, "role": role, "ver": version, "iat": now,
               "exp": now + timedelta(minutes=s.jwt_expiry_minutes)}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])


def role_at_least(role: str, required: str) -> bool:
    return ROLES.index(role) >= ROLES.index(required)
