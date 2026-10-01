"""Just-in-time token resolution with automatic OAuth refresh."""

import asyncio
from datetime import datetime, timedelta, timezone

from test_api import client, register  # noqa: F401  (fixture re-export)

from cip.connectors.source_control import SourceControlError
from cip.core.security.crypto import TokenCipher
from cip.db import session as db
from cip.db.models import AuditLog, SourceConnection
from cip.services import tokens


async def _connection(org_id, expires_in_s, refresh="refresh-1", token="access-1"):
    c = TokenCipher()
    async with db.sessionmaker()() as s:
        con = SourceConnection(org_id=org_id, provider="gitlab", host="gitlab.com", token_type="oauth",
                               encrypted_token=c.encrypt(token),
                               encrypted_refresh_token=c.encrypt(refresh) if refresh else None,
                               expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_s))
        s.add(con)
        await s.commit()
        return con.id


async def _org(client):  # noqa: F811
    h = await register(client)
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def test_valid_token_is_returned_without_refresh(client, monkeypatch):  # noqa: F811
    org = await _org(client)
    await _connection(org, expires_in_s=3600)
    called = []
    monkeypatch.setitem(tokens.REFRESHERS, "gitlab", lambda rt: called.append(rt))
    assert await tokens.make_token_resolver(org)("gitlab", "gitlab.com") == "access-1"
    assert called == []
    assert await tokens.make_token_resolver(org)("github", "github.com") is None


async def test_expiring_token_is_refreshed_once_even_when_requested_concurrently(client, monkeypatch):  # noqa: F811
    org = await _org(client)
    con_id = await _connection(org, expires_in_s=30)  # inside the 2-minute refresh margin
    calls = []

    async def fake_refresh(refresh_token):
        calls.append(refresh_token)
        await asyncio.sleep(0.05)
        return {"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 7200}

    monkeypatch.setitem(tokens.REFRESHERS, "gitlab", fake_refresh)
    resolve = tokens.make_token_resolver(org)
    results = await asyncio.gather(*(resolve("gitlab", "gitlab.com") for _ in range(5)))
    assert results == ["access-2"] * 5
    assert calls == ["refresh-1"]  # single-use refresh token consumed exactly once

    c = TokenCipher()
    async with db.sessionmaker()() as s:
        con = await s.get(SourceConnection, con_id)
        assert c.decrypt(con.encrypted_token) == "access-2"
        assert c.decrypt(con.encrypted_refresh_token) == "refresh-2"
        assert "access-2" not in con.encrypted_token
        expires = con.expires_at.replace(tzinfo=timezone.utc) if con.expires_at.tzinfo is None else con.expires_at
        assert expires > datetime.now(timezone.utc) + timedelta(hours=1)
        from sqlalchemy import select
        actions = (await s.execute(select(AuditLog.action).where(AuditLog.org_id == org))).scalars().all()
        assert "connection.refreshed" in actions


async def test_failed_or_impossible_refresh_returns_none(client, monkeypatch):  # noqa: F811
    org = await _org(client)
    await _connection(org, expires_in_s=-60)

    async def boom(refresh_token):
        raise SourceControlError("invalid_grant")

    monkeypatch.setitem(tokens.REFRESHERS, "gitlab", boom)
    assert await tokens.make_token_resolver(org)("gitlab", "gitlab.com") is None

    org2 = await _org_other(client)
    await _connection(org2, expires_in_s=-60, refresh=None)  # expired, no refresh token
    assert await tokens.make_token_resolver(org2)("gitlab", "gitlab.com") is None


async def _org_other(client):  # noqa: F811
    h = await register(client, org="Other Co", email="boss@other-co.com")
    return (await client.get("/api/auth/me", headers=h)).json()["org_id"]


async def test_oauth_callback_stores_encrypted_access_and_refresh_tokens(client, monkeypatch):  # noqa: F811
    from urllib.parse import parse_qs, urlparse

    from cip.config import get_settings
    from cip.connectors.source_control import GitLabProvider

    monkeypatch.setenv("CIP_GITLAB_CLIENT_ID", "gl-client")
    monkeypatch.setenv("CIP_GITLAB_CLIENT_SECRET", "gl-secret")
    get_settings.cache_clear()

    async def fake_exchange(code, redirect_uri):
        assert code == "auth-code" and redirect_uri.endswith("/api/connections/gitlab/callback")
        return {"access_token": "gl-access", "refresh_token": "gl-refresh", "expires_in": 7200,
                "scope": "read_api read_repository read_user"}

    monkeypatch.setattr(GitLabProvider, "exchange_code", staticmethod(fake_exchange))
    h = await register(client)
    url = (await client.get("/api/connections/gitlab/authorize", headers=h)).json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]

    r = await client.get(f"/api/connections/gitlab/callback?code=auth-code&state={state}")
    assert r.status_code in (302, 307)
    # State is single-use.
    assert (await client.get(f"/api/connections/gitlab/callback?code=x&state={state}")).status_code == 400

    listed = (await client.get("/api/connections", headers=h))
    assert "gl-access" not in listed.text and "gl-refresh" not in listed.text
    con = listed.json()[0]
    assert con["token_type"] == "oauth" and con["expires_at"]
    org = (await client.get("/api/auth/me", headers=h)).json()["org_id"]
    assert await tokens.make_token_resolver(org)("gitlab", "gitlab.com") == "gl-access"
    async with db.sessionmaker()() as s:
        row = await s.get(SourceConnection, con["id"])
        assert TokenCipher().decrypt(row.encrypted_refresh_token) == "gl-refresh"
    get_settings.cache_clear()
