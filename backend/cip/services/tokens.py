"""Just-in-time resolution of source-control tokens, with automatic OAuth refresh.

Tokens are decrypted only when an agent is about to call GitHub/GitLab. If an
OAuth access token is expired (or within ``REFRESH_MARGIN`` of expiring) and a
refresh token is stored, it is renewed first. GitLab refresh tokens are
single-use, so refreshes are serialized per connection: concurrent agents
wait for the first refresh and reuse its result instead of burning the token.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from cip.connectors.source_control import GitHubProvider, GitLabProvider, SourceControlError
from cip.core.security.crypto import TokenCipher
from cip.db import session as db
from cip.db.models import AuditLog, SourceConnection

log = logging.getLogger(__name__)

REFRESH_MARGIN = timedelta(minutes=2)
REFRESHERS = {"github": GitHubProvider.refresh_token, "gitlab": GitLabProvider.refresh_token}
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


async def _load(org_id: str, provider: str, host: str):
    async with db.sessionmaker()() as s:
        return (await s.execute(select(SourceConnection).where(
            SourceConnection.org_id == org_id, SourceConnection.provider == provider,
            SourceConnection.host == host))).scalar_one_or_none()


def _usable(con: SourceConnection) -> bool:
    expires = _aware(con.expires_at)
    return expires is None or expires - REFRESH_MARGIN > _now()


async def _refresh(con: SourceConnection) -> str | None:
    cipher = TokenCipher()
    refresher = REFRESHERS.get(con.provider)
    if not con.encrypted_refresh_token or refresher is None:
        log.warning("Stored %s token for %s has expired and cannot be refreshed; reconnect it in Settings",
                    con.provider, con.host)
        return None
    try:
        data = await refresher(cipher.decrypt(con.encrypted_refresh_token))
    except (SourceControlError, ValueError, OSError) as exc:
        log.warning("Refreshing %s token for %s failed: %s", con.provider, con.host, exc)
        async with db.sessionmaker()() as s:
            s.add(AuditLog(org_id=con.org_id, action="connection.refresh_failed", target_type="connection",
                           target_id=con.id, details={"provider": con.provider, "host": con.host,
                                                      "error": str(exc)[:300]}))
            await s.commit()
        return None
    async with db.sessionmaker()() as s:
        row = await s.get(SourceConnection, con.id)
        row.encrypted_token = cipher.encrypt(data["access_token"])
        if data.get("refresh_token"):
            row.encrypted_refresh_token = cipher.encrypt(data["refresh_token"])
        row.expires_at = _now() + timedelta(seconds=int(data["expires_in"])) if data.get("expires_in") else None
        s.add(AuditLog(org_id=con.org_id, action="connection.refreshed", target_type="connection",
                       target_id=con.id, details={"provider": con.provider, "host": con.host}))
        await s.commit()
    log.info("Refreshed %s token for %s", con.provider, con.host)
    return data["access_token"]


def make_token_resolver(org_id: str):
    async def resolve(provider: str, host: str) -> str | None:
        con = await _load(org_id, provider, host)
        if con is None:
            return None
        if _usable(con):
            return TokenCipher().decrypt(con.encrypted_token)
        async with _locks[con.id]:
            # Another agent may have refreshed while we waited for the lock.
            con = await _load(org_id, provider, host)
            if con is None:
                return None
            if _usable(con):
                return TokenCipher().decrypt(con.encrypted_token)
            return await _refresh(con)
    return resolve
