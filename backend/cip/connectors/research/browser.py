"""Headless-browser rendering for JavaScript-heavy sites (optional, via Playwright).

Many SaaS marketing sites are single-page apps whose HTML is an empty shell
until scripts run. ``WebFetcher`` fetches with plain HTTP first and only asks
this renderer when a page looks script-rendered (or always/never, per
``CIP_BROWSER_RENDERING``).

Safety inside the browser:
* every request the page makes (documents, scripts, XHR/fetch, frames) is
  intercepted; non-http(s) schemes and non-public addresses are aborted, so a
  hostile page can't use the browser to reach internal services (SSRF);
* images, media and fonts are not downloaded; downloads and service workers
  are disabled; each page gets a fresh context (no shared cookies/storage).

Playwright is an optional dependency (``pip install .[browser]``); without it
the crawler silently falls back to plain HTTP.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from cip.core.urls import urlparse

from cip.config import Settings, get_settings

log = logging.getLogger(__name__)

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
_SPA_MARKERS = re.compile(
    r'id=["\'](?:root|app|__next|__nuxt|svelte|main-app)["\']|ng-app|ng-version|data-reactroot|'
    r'(?:enable|requires?)\s+javascript',
    re.IGNORECASE,
)
_SCRIPT = re.compile(r"<script\b", re.IGNORECASE)
MIN_VISIBLE_TEXT = 400


def looks_script_rendered(html: str, visible_text: str) -> bool:
    """Heuristic: little visible text, plus scripts or a known SPA mount point."""
    text_len = len(visible_text.strip())
    if text_len >= MIN_VISIBLE_TEXT:
        return False
    scripts = len(_SCRIPT.findall(html))
    return bool(_SPA_MARKERS.search(html)) or (scripts >= 2 and text_len < 200)


@dataclass
class RenderedPage:
    url: str
    status: int
    html: str


class BrowserRenderer:
    def __init__(self, settings: Settings | None = None, check_public: bool = True) -> None:
        self.settings = settings or get_settings()
        self.check_public = check_public
        self._pw = None
        self._browser = None
        self._users = 0
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(max(1, self.settings.browser_max_pages))
        self._host_ok: dict[str, bool] = {}
        self._unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        if self._unavailable_reason:
            return False
        try:
            import playwright.async_api  # noqa: F401
        except ImportError:
            self._unavailable_reason = "playwright not installed (pip install '.[browser]')"
            return False
        return True

    @asynccontextmanager
    async def session(self):
        """Keep one browser alive while any crawl is using it; close it when the last one ends."""
        async with self._lock:
            self._users += 1
        try:
            yield self
        finally:
            async with self._lock:
                self._users -= 1
                if self._users == 0:
                    await self._close()

    async def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        async with self._lock:
            if self._browser is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                kwargs = {"headless": True, "args": ["--disable-dev-shm-usage"]}
                if self.settings.browser_executable:
                    kwargs["executable_path"] = self.settings.browser_executable
                try:
                    self._browser = await self._pw.chromium.launch(**kwargs)
                except Exception as exc:
                    await self._pw.stop()
                    self._pw = None
                    self._unavailable_reason = f"browser failed to launch: {exc}"
                    log.warning("Browser rendering disabled: %s", self._unavailable_reason)
                    raise
        return self._browser

    async def _close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None

    async def _allowed(self, url: str) -> bool:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        if not self.check_public:
            return True
        host = p.hostname or ""
        if host not in self._host_ok:
            from cip.connectors.research.web import UnsafeURL, assert_public_url
            try:
                await assert_public_url(url)
                self._host_ok[host] = True
            except UnsafeURL:
                self._host_ok[host] = False
        return self._host_ok[host]

    async def render(self, url: str) -> RenderedPage | None:
        if not self.available:
            return None
        try:
            browser = await self._ensure_browser()
        except Exception:  # noqa: BLE001 - launch failure already logged; fall back to HTTP
            return None
        s = self.settings
        async with self._sem:
            context = await browser.new_context(
                user_agent=s.crawler_user_agent, java_script_enabled=True, accept_downloads=False,
                service_workers="block", ignore_https_errors=not s.crawler_verify_tls,
            )
            try:
                async def guard(route):
                    req = route.request
                    if req.resource_type in BLOCKED_RESOURCE_TYPES or not await self._allowed(req.url):
                        await route.abort()
                    else:
                        await route.continue_()

                await context.route("**/*", guard)
                page = await context.new_page()
                timeout_ms = int(s.crawler_timeout_seconds * 1000)
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if response is None:
                    return None
                try:  # give client-side rendering a moment to settle, but never wait forever
                    await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
                except Exception:  # noqa: BLE001
                    pass
                final_url = page.url
                if not await self._allowed(final_url):
                    return None
                html = await page.content()
                return RenderedPage(url=final_url, status=response.status, html=html)
            except Exception as exc:  # noqa: BLE001 - rendering is best-effort
                log.info("render failed for %s: %s", url, exc)
                return None
            finally:
                await context.close()
