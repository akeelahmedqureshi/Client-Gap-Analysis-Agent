from __future__ import annotations

from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from cip.core.security.auth import decode_access_token, role_at_least
from cip.db.models import User
from cip.db.session import get_session

bearer = HTTPBearer(auto_error=False)


async def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    user = await session.get(User, payload.get("sub"))
    if user is None or user.org_id != payload.get("org"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    return user


def require_role(role: str) -> Callable:
    async def dep(user: User = Depends(current_user)) -> User:
        if not role_at_least(user.role, role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role '{role}'")
        return user
    return dep


def not_found(what: str = "Resource") -> HTTPException:
    # Same response for "missing" and "belongs to another organization" to avoid leaking existence.
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")
