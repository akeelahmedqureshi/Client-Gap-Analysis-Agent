from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import current_user, require_role
from cip.config import get_settings
from cip.core.security.auth import ROLES, create_access_token, hash_password, verify_password
from cip.db.models import Organization, User
from cip.db.session import get_session

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


class InviteIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    name: str = ""
    role: str = "analyst"


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, name=u.name, role=u.role, org_id=u.org_id)


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    if not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    if (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    org = Organization(name=body.organization)
    session.add(org)
    await session.flush()
    user = User(org_id=org.id, email=body.email.lower(), name=body.name, role="admin",
                password_hash=hash_password(body.password))
    session.add(user)
    await session.commit()
    return TokenOut(access_token=create_access_token(user.id, org.id, user.role))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    user = (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return TokenOut(access_token=create_access_token(user.id, user.org_id, user.role))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)) -> UserOut:
    return _user_out(user)


@router.post("/users", response_model=UserOut, status_code=201)
async def add_user(body: InviteIn, admin: User = Depends(require_role("admin")),
                   session: AsyncSession = Depends(get_session)) -> UserOut:
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(org_id=admin.org_id, email=body.email.lower(), name=body.name, role=body.role,
                password_hash=hash_password(body.password))
    session.add(user)
    await session.commit()
    return _user_out(user)
