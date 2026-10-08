"""Organization user management and audit log (admin only)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.api.routes.auth import UserOut, user_out
from cip.core.security.auth import ROLES, hash_password
from cip.db.models import AuditLog, User
from cip.db.session import get_session
from cip.services import audit
from cip.services.governance import JOB_FUNCTIONS

router = APIRouter(prefix="/api", tags=["users & audit"])


class UserPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    role: str | None = None
    is_active: bool | None = None
    job_function: str | None = None  # "" clears it


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=10, max_length=200)


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    user_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict
    ip: str | None


async def _org_user(session: AsyncSession, admin: User, user_id: str) -> User:
    user = await session.get(User, user_id)
    if not user or user.org_id != admin.org_id:
        raise not_found("User")
    return user


async def _active_admins(session: AsyncSession, org_id: str) -> int:
    return (await session.execute(select(func.count()).select_from(User).where(
        User.org_id == org_id, User.role == "admin", User.is_active.is_(True)))).scalar_one()


@router.get("/users", response_model=list[UserOut])
async def list_users(admin: User = Depends(require_role("admin")),
                     session: AsyncSession = Depends(get_session)) -> list[UserOut]:
    users = (await session.execute(select(User).where(User.org_id == admin.org_id)
                                   .order_by(User.created_at))).scalars().all()
    return [user_out(u) for u in users]


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserPatch, request: Request, admin: User = Depends(require_role("admin")),
                      session: AsyncSession = Depends(get_session)) -> UserOut:
    user = await _org_user(session, admin, user_id)
    changes: dict = {}
    if body.role is not None and body.role != user.role:
        if body.role not in ROLES:
            raise HTTPException(422, f"role must be one of {ROLES}")
        changes["role"] = [user.role, body.role]
    if body.is_active is not None and body.is_active != user.is_active:
        changes["is_active"] = [user.is_active, body.is_active]
    losing_admin = user.role == "admin" and user.is_active and (
        (body.role is not None and body.role != "admin") or body.is_active is False)
    if losing_admin and await _active_admins(session, admin.org_id) <= 1:
        raise HTTPException(409, "An organization must keep at least one active admin")
    if body.job_function is not None:
        new = body.job_function or None
        if new is not None and new not in JOB_FUNCTIONS:
            raise HTTPException(422, f"job_function must be one of {JOB_FUNCTIONS}")
        if new != user.job_function:
            audit.record(session, request, admin, "user.updated", "user", user.id, target=user.email,
                         changes={"job_function": [user.job_function, new]})
            user.job_function = new
    if body.name is not None:
        user.name = body.name
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if changes:
        # Role or status changes invalidate existing sessions so new permissions apply immediately.
        user.token_version += 1
        audit.record(session, request, admin, "user.updated", "user", user.id, target=user.email, changes=changes)
    await session.commit()
    return user_out(user)


@router.post("/users/{user_id}/reset-password", response_model=UserOut)
async def reset_password(user_id: str, body: ResetPasswordIn, request: Request,
                         admin: User = Depends(require_role("admin")),
                         session: AsyncSession = Depends(get_session)) -> UserOut:
    """Set a temporary password (share it out of band); signs the user out everywhere and unlocks them."""
    user = await _org_user(session, admin, user_id)
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    user.failed_logins = 0
    user.locked_until = None
    audit.record(session, request, admin, "user.password_reset", "user", user.id, target=user.email)
    await session.commit()
    return user_out(user)


@router.post("/users/{user_id}/unlock", response_model=UserOut)
async def unlock(user_id: str, request: Request, admin: User = Depends(require_role("admin")),
                 session: AsyncSession = Depends(get_session)) -> UserOut:
    user = await _org_user(session, admin, user_id)
    user.failed_logins = 0
    user.locked_until = None
    audit.record(session, request, admin, "user.unlocked", "user", user.id, target=user.email)
    await session.commit()
    return user_out(user)


@router.get("/audit", response_model=list[AuditOut])
async def audit_log(action: str | None = Query(None, max_length=60), limit: int = Query(100, ge=1, le=500),
                    offset: int = Query(0, ge=0), admin: User = Depends(require_role("admin")),
                    session: AsyncSession = Depends(get_session)) -> list[AuditOut]:
    q = select(AuditLog).where(AuditLog.org_id == admin.org_id)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    rows = (await session.execute(q.order_by(AuditLog.id.desc()).limit(limit).offset(offset))).scalars().all()
    return [AuditOut(id=r.id, created_at=r.created_at, user_email=r.user_email, action=r.action,
                     target_type=r.target_type, target_id=r.target_id, details=r.details, ip=r.ip) for r in rows]
