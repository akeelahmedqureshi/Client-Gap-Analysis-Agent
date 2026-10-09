"""Web search provider abstraction (Tavily, Brave, or disabled)."""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass

import httpx

from cip.config import Settings, get_settings
from cip.core.net import api_verify

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class SearchProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...


class NullSearchProvider(SearchProvider):
    name = "none"

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return []


class TavilySearchProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self._transport = transport

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport, verify=api_verify()) as c:
            resp = await c.post("https://api.tavily.com/search",
                                headers={"Authorization": f"Bearer {self._key}"},
                                json={"query": query, "max_results": limit, "search_depth": "basic"})
        if resp.status_code >= 400:
            log.warning("Tavily search failed: %s", resp.status_code)
            return []
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", "")[:800])
                for r in resp.json().get("results", [])]


class BraveSearchProvider(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self._transport = transport

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport, verify=api_verify()) as c:
            resp = await c.get("https://api.search.brave.com/res/v1/web/search",
                               params={"q": query, "count": min(limit, 20)},
                               headers={"X-Subscription-Token": self._key, "Accept": "application/json"})
        if resp.status_code >= 400:
            log.warning("Brave search failed: %s", resp.status_code)
            return []
        return [SearchResult(r.get("title", ""), r.get("url", ""), r.get("description", "")[:800])
                for r in resp.json().get("web", {}).get("results", [])]


def get_search_provider(settings: Settings | None = None) -> SearchProvider:
    s = settings or get_settings()
    if s.search_provider == "tavily" and s.tavily_api_key:
        return TavilySearchProvider(s.tavily_api_key)
    if s.search_provider == "brave" and s.brave_api_key:
        return BraveSearchProvider(s.brave_api_key)
    return NullSearchProvider()


class MeteredSearch(SearchProvider):
    """Wraps any provider: counts queries against the run's usage meter and web budget."""

    def __init__(self, inner: SearchProvider) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "search")

    def __getattr__(self, item):  # e.g. a test fake's recorded queries
        return getattr(self.inner, item)

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        from cip.core import usage

        meter = usage.current()
        if meter:
            try:
                meter.record_web(search=True)
            except usage.BudgetExhausted:
                log.warning("Search skipped: web request budget exhausted")
                return []
        return await self.inner.search(query, limit)
