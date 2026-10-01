"""GitHub/GitLab connections: OAuth flow or personal access token. Admin only.

Tokens are encrypted before storage and never returned by the API.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.config import get_settings
from cip.connectors.source_control import GitHubProvider, GitLabProvider, SourceControlError
from cip.core.security.crypto import TokenCipher
from cip.db.models import OAuthState, SourceConnection, User
from cip.db.session import get_session
from cip.services import audit

router = APIRouter(prefix="/api/connections", tags=["source control connections"])
Provider = Literal["github", "gitlab"]
PROVIDERS = {"github": GitHubProvider, "gitlab": GitLabProvider}
STATE_TTL = timedelta(minutes=10)


class ConnectionOut(BaseModel):
    id: str
    provider: str
    host: str
    token_type: str
    scopes: str
    account_login: str | None
    created_at: datetime
    expires_at: datetime | None


class PatIn(BaseModel):
    provider: Provider
    host: str = Field(default="", max_length=200)
    token: str = Field(min_length=10, max_length=500)


def _out(c: SourceConnection) -> ConnectionOut:
    return ConnectionOut(id=c.id, provider=c.provider, host=c.host, token_type=c.token_type, scopes=c.scopes,
                         account_login=c.account_login, created_at=c.created_at, expires_at=c.expires_at)


def _default_host(provider: str) -> str:
    return "github.com" if provider == "github" else get_settings().gitlab_url.split("://", 1)[-1].rstrip("/")


async def _upsert(session: AsyncSession, org_id: str, user_id: str, provider: str, host: str, token: str,
                  token_type: str, scopes: str = "", expires_in: int | None = None) -> SourceConnection:
    con = (await session.execute(select(SourceConnection).where(
        SourceConnection.org_id == org_id, SourceConnection.provider == provider,
        SourceConnection.host == host))).scalar_one_or_none()
    if con is None:
        con = SourceConnection(org_id=org_id, provider=provider, host=host)
        session.add(con)
    con.encrypted_token = TokenCipher().encrypt(token)
    con.token_type = token_type
    con.scopes = scopes
    con.created_by = user_id
    con.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
    await session.commit()
    return con


@router.get("", response_model=list[ConnectionOut])
async def list_connections(user: User = Depends(require_role("viewer")),
                           session: AsyncSession = Depends(get_session)) -> list[ConnectionOut]:
    rows = (await session.execute(select(SourceConnection).where(SourceConnection.org_id == user.org_id))).scalars()
    return [_out(c) for c in rows]


@router.post("/token", response_model=ConnectionOut, status_code=201)
async def add_token(body: PatIn, request: Request, user: User = Depends(require_role("admin")),
                    session: AsyncSession = Depends(get_session)) -> ConnectionOut:
    """Store a (preferably fine-grained, read-only) personal access token."""
    host = body.host or _default_host(body.provider)
    audit.record(session, request, user, "connection.added", "connection", None, provider=body.provider,
                 host=host, token_type="pat")
    con = await _upsert(session, user.org_id, user.id, body.provider, host, body.token, "pat")
    return _out(con)


@router.delete("/{connection_id}", status_code=204)
async def remove(connection_id: str, request: Request, user: User = Depends(require_role("admin")),
                 session: AsyncSession = Depends(get_session)) -> None:
    con = await session.get(SourceConnection, connection_id)
    if not con or con.org_id != user.org_id:
        raise not_found("Connection")
    audit.record(session, request, user, "connection.removed", "connection", con.id, provider=con.provider,
                 host=con.host)
    await session.delete(con)
    await session.commit()


@router.get("/{provider}/authorize")
async def authorize(provider: Provider, user: User = Depends(require_role("admin")),
                    session: AsyncSession = Depends(get_session)) -> dict:
    state = secrets.token_urlsafe(32)
    await session.execute(delete(OAuthState).where(
        OAuthState.created_at < datetime.now(timezone.utc) - STATE_TTL))
    session.add(OAuthState(state=state, org_id=user.org_id, user_id=user.id, provider=provider))
    await session.commit()
    redirect_uri = f"{get_settings().oauth_redirect_base}/api/connections/{provider}/callback"
    try:
        return {"authorize_url": PROVIDERS[provider].authorize_url(state, redirect_uri)}
    except SourceControlError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{provider}/callback")
async def callback(provider: Provider, code: str, state: str, request: Request,
                   session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    st = await session.get(OAuthState, state)
    if not st or st.provider != provider:
        raise HTTPException(400, "Invalid OAuth state")
    created = st.created_at if st.created_at.tzinfo else st.created_at.replace(tzinfo=timezone.utc)
    await session.delete(st)
    await session.commit()
    if datetime.now(timezone.utc) - created > STATE_TTL:
        raise HTTPException(400, "OAuth state expired")
    redirect_uri = f"{get_settings().oauth_redirect_base}/api/connections/{provider}/callback"
    try:
        data = await PROVIDERS[provider].exchange_code(code, redirect_uri)
    except SourceControlError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(session, request, None, "connection.added", "connection", None, org_id=st.org_id,
                 provider=provider, token_type="oauth", by_user=st.user_id)
    await _upsert(session, st.org_id, st.user_id, provider, _default_host(provider), data["access_token"], "oauth",
                  scopes=data.get("scope", ""), expires_in=data.get("expires_in"))
    front = get_settings().cors_origins[0] if get_settings().cors_origins else ""
    return RedirectResponse(f"{front}/settings?connected={provider}")
