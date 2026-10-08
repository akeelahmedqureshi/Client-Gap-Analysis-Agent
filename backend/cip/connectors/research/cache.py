"""Shared research cache (PRD 21): fetched pages reused across agents and runs.

``WebFetcher`` looks pages up here before going to the network. Entries are scoped to an organization,
keyed by the normalized URL, and expire after ``CIP_RESEARCH_CACHE_TTL_HOURS``. Only successful HTML and
PDF responses are stored (bodies capped at ``MAX_BODY``). The cache never hides age: a hit carries the
original ``fetched_at``, which the orchestrator uses as the collection date of evidence from that page.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import delete, select

log = logging.getLogger(__name__)
MAX_BODY = 1_500_000


@dataclass
class CachedPage:
    url: str
    final_url: str
    status: int
    kind: str  # html | pdf
    body: str
    title: str = ""
    rendered: bool = False
    fetched_at: datetime | None = None


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class PageCache(Protocol):
    async def get(self, url: str) -> CachedPage | None: ...
    async def put(self, page: CachedPage) -> None: ...


class MemoryPageCache:
    """Process-local cache (CLI, tests)."""

    def __init__(self, ttl_hours: float = 24.0) -> None:
        self.ttl = timedelta(hours=ttl_hours)
        self.items: dict[str, CachedPage] = {}

    async def get(self, url: str) -> CachedPage | None:
        hit = self.items.get(url)
        if hit and datetime.now(timezone.utc) - _aware(hit.fetched_at) < self.ttl:
            return hit
        return None

    async def put(self, page: CachedPage) -> None:
        page.fetched_at = page.fetched_at or datetime.now(timezone.utc)
        self.items[page.url] = page


class DbPageCache:
    """Database-backed cache shared by every run (and worker process) of one organization."""

    def __init__(self, org_id: str, ttl_hours: float) -> None:
        self.org_id = org_id
        self.ttl = timedelta(hours=ttl_hours)

    async def get(self, url: str) -> CachedPage | None:
        from cip.db import session as db
        from cip.db.models import ResearchCacheEntry as E

        try:
            async with db.sessionmaker()() as s:
                e = (await s.execute(select(E).where(E.org_id == self.org_id, E.url_hash == url_hash(url)))) \
                    .scalar_one_or_none()
        except Exception:  # noqa: BLE001 - a cache problem must never fail research
            log.exception("Research cache read failed")
            return None
        if e is None or datetime.now(timezone.utc) - _aware(e.fetched_at) >= self.ttl:
            return None
        return CachedPage(url=e.url, final_url=e.final_url, status=e.status, kind=e.kind, body=e.body, title=e.title,
                          rendered=e.rendered, fetched_at=_aware(e.fetched_at))

    async def put(self, page: CachedPage) -> None:
        from cip.db import session as db
        from cip.db.models import ResearchCacheEntry as E

        page.fetched_at = page.fetched_at or datetime.now(timezone.utc)
        try:
            async with db.sessionmaker()() as s:
                await s.execute(delete(E).where(E.org_id == self.org_id, E.url_hash == url_hash(page.url)))
                s.add(E(org_id=self.org_id, url_hash=url_hash(page.url), url=page.url[:2000],
                        final_url=page.final_url[:2000], status=page.status, kind=page.kind, title=page.title[:500],
                        body=page.body[:MAX_BODY], rendered=page.rendered, fetched_at=page.fetched_at))
                await s.commit()
        except Exception:  # noqa: BLE001 - e.g. two agents storing the same page at once
            log.info("Research cache write skipped for %s", page.url)


async def purge_expired(ttl_hours: float, now: datetime | None = None) -> int:
    from cip.db import session as db
    from cip.db.models import ResearchCacheEntry as E

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=ttl_hours)
    async with db.sessionmaker()() as s:
        n = (await s.execute(delete(E).where(E.fetched_at < cutoff))).rowcount or 0
        await s.commit()
    return n
