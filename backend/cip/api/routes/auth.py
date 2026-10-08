from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import current_user, require_role
from cip.config import get_settings
from cip.core.security.auth import ROLES, create_access_token, hash_password, verify_password
from cip.db.models import Organization, User
from cip.db.session import get_session
from cip.services import audit
from cip.services.governance import JOB_FUNCTIONS
from cip.services.ratelimit import limiter

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    organization: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    name: str = ""


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    org_id: str
    is_active: bool = True
    locked: bool = False
    created_at: datetime | None = None
    job_function: str | None = None


class InviteIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    name: str = ""
    role: str = "analyst"
    job_function: str | None = None


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


# Verifying against a dummy hash when the email is unknown keeps response timing uniform,
# so login responses don't reveal which emails are registered.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def is_locked(user: User) -> bool:
    until = _aware(user.locked_until)
    return bool(until and until > _now())


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role, org_id=u.org_id, is_active=u.is_active,
                   locked=is_locked(u), created_at=u.created_at, job_function=u.job_function)


def token_for(u: User) -> TokenOut:
    return TokenOut(access_token=create_access_token(u.id, u.org_id, u.role, u.token_version))


async def _throttle(request: Request) -> None:
    s = get_settings()
    ip = audit.client_ip(request) or "unknown"
    if not await limiter.hit(f"auth:{ip}", s.login_ip_limit, s.login_ip_window_seconds):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts; please wait and try again")


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, request: Request, session: AsyncSession = Depends(get_session)) -> TokenOut:
    if not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    await _throttle(request)
    if (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    org = Organization(name=body.organization)
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email=body.email.lower(), name=body.name, role="admin",
                password_hash=hash_password(body.password), token_version=0)
    session.add(user)
    await session.flush()
    audit.record(session, request, user, "org.register", "organization", org.id, organization=org.name)
    await session.commit()
    return token_for(user)


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, request: Request, session: AsyncSession = Depends(get_session)) -> TokenOut:
    s = get_settings()
    await _throttle(request)
    email = body.email.lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user and is_locked(user):
        audit.record(session, request, user, "auth.login_blocked", "user", user.id)
        await session.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Account temporarily locked after repeated failed sign-ins; try again later")
    password_ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not user.is_active or not password_ok:
        if user:
            user.failed_logins += 1
            if user.failed_logins >= s.login_max_failures:
                user.locked_until = _now() + timedelta(minutes=s.login_lockout_minutes)
                user.failed_logins = 0
                audit.record(session, request, user, "auth.account_locked", "user", user.id,
                             minutes=s.login_lockout_minutes)
        audit.record(session, request, user, "auth.login_failed", "user", user.id if user else None, email=email)
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    user.failed_logins = 0
    user.locked_until = None
    audit.record(session, request, user, "auth.login", "user", user.id)
    await session.commit()
    return token_for(user)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> UserOut:
    return user_out(user)


@router.post("/change-password", response_model=TokenOut)
async def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> TokenOut:
    """Change your own password. Signs out all other sessions; returns a fresh token."""
    user = await session.get(User, user.id)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    audit.record(session, request, user, "user.password_changed", "user", user.id)
    await session.commit()
    return token_for(user)


@router.post("/users", response_model=UserOut, status_code=201)
async def add_user(body: InviteIn, request: Request, admin: User = Depends(require_role("admin")),
                   session: AsyncSession = Depends(get_session)) -> UserOut:
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if body.job_function and body.job_function not in JOB_FUNCTIONS:
        raise HTTPException(422, f"job_function must be one of {JOB_FUNCTIONS}")
    if (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(org_id=admin.org_id, email=body.email.lower(), name=body.name, role=body.role,
                job_function=body.job_function or None,
                password_hash=hash_password(body.password), is_active=True, failed_logins=0, token_version=0)
    session.add(user)
    await session.flush()
    audit.record(session, request, admin, "user.created", "user", user.id, email=None, new_user=user.email,
                 role=user.role)
    await session.commit()
    return user_out(user)
