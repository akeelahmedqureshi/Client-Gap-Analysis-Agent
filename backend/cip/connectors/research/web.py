"""Polite web fetching and same-site crawling.

* Only http/https URLs that resolve to public IP addresses are fetched (SSRF guard:
  CSV rows and search results are untrusted input).
* robots.txt is honoured.
* Priority pages (about, products, pricing, …) are crawled first, then links
  discovered on the site are followed up to ``max_pages``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from contextlib import nullcontext
from dataclasses import dataclass, field
from urllib.parse import urljoin, urldefrag, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from cip.config import get_settings
from cip.connectors.research.browser import BrowserRenderer, looks_script_rendered

log = logging.getLogger(__name__)

PRIORITY_PATHS = [
    "/", "/about", "/about-us", "/products", "/solutions", "/services", "/platform", "/features",
    "/pricing", "/contact", "/team", "/careers", "/blog", "/resources", "/case-studies", "/customers",
    "/partners", "/integrations", "/documentation", "/docs", "/api", "/security",
]
PRIORITY_HINTS = tuple(p.strip("/") for p in PRIORITY_PATHS if p != "/")
SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4", ".mp3",
                   ".css", ".js", ".ico", ".xml", ".json", ".woff", ".woff2", ".dmg", ".exe")
MAX_BODY_BYTES = 3_000_000


class UnsafeURL(ValueError):
    pass


@dataclass
class Page:
    url: str
    status: int
    title: str = ""
    description: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    generator: str | None = None
    rendered: bool = False  # True when the content came from the headless browser


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if "://" not in url:
        url = "https://" + url
    url, _ = urldefrag(url)
    return url


def registrable_domain(url_or_host: str) -> str:
    host = urlparse(url_or_host).hostname if "://" in url_or_host else url_or_host
    host = (host or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


async def assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURL(f"Unsupported URL: {url}")
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
        addrs = [ip]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeURL(f"Cannot resolve {host}") from exc
        addrs = [ipaddress.ip_address(i[4][0]) for i in infos]
    for ip in addrs:
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeURL(f"Refusing to fetch non-public address for {host}")


_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?\d{1,3}[\s.\-]?)?(?:\(\d{2,4}\)[\s.\-]?)?\d{3,4}[\s.\-]\d{3,4}(?:[\s.\-]\d{2,4})?")


def parse_html(url: str, status: int, html: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    scripts = [s.get("src") for s in soup.find_all("script") if s.get("src")]
    gen = soup.find("meta", attrs={"name": "generator"})
    desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    title = soup.title.get_text(strip=True) if soup.title else ""
    headings = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"])][:60]

    links: list[str] = []
    external: list[str] = []
    emails: set[str] = set()
    phones: set[str] = set()
    base_domain = registrable_domain(url)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if _EMAIL.fullmatch(addr):
                emails.add(addr.lower())
            continue
        if href.startswith("tel:"):
            phones.add(href[4:].strip())
            continue
        if href.startswith(("javascript:", "#")):
            continue
        absolute = normalize_url(urljoin(url, href))
        if not absolute.startswith(("http://", "https://")):
            continue
        if registrable_domain(absolute) == base_domain:
            links.append(absolute)
        else:
            external.append(absolute)

    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    for m in _EMAIL.findall(text):
        if not m.lower().endswith((".png", ".jpg", ".svg", ".webp")):
            emails.add(m.lower())
    for m in _PHONE.findall(text):
        digits = re.sub(r"\D", "", m)
        if 8 <= len(digits) <= 15:
            phones.add(m.strip())

    return Page(
        url=url, status=status, title=title,
        description=(desc.get("content") or "").strip() if desc else "",
        text=text, links=list(dict.fromkeys(links)), external_links=list(dict.fromkeys(external)),
        emails=sorted(emails), phones=sorted(phones)[:10], headings=headings, scripts=scripts,
        generator=gen.get("content") if gen else None,
    )


class WebFetcher:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, check_public: bool = True,
                 rendering: str | None = None, renderer: BrowserRenderer | None = None) -> None:
        s = get_settings()
        self._transport = transport
        self._check_public = check_public and transport is None
        self._timeout = s.crawler_timeout_seconds
        self._ua = s.crawler_user_agent
        self._robots: dict[str, RobotFileParser | None] = {}
        # A mocked transport can't be seen by a real browser, so rendering defaults off for it.
        self.rendering = rendering or ("never" if transport is not None else s.browser_rendering)
        self.renderer = renderer or (BrowserRenderer(s, self._check_public) if self.rendering != "never" else None)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, follow_redirects=True, transport=self._transport,
                                 headers={"User-Agent": self._ua, "Accept": "text/html,application/xhtml+xml"})

    async def _allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                resp = await client.get(origin + "/robots.txt")
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(self._ua, url)

    async def fetch(self, url: str, client: httpx.AsyncClient | None = None) -> Page | None:
        url = normalize_url(url)
        own = client is None
        client = client or self._client()
        try:
            if self._check_public:
                await assert_public_url(url)
            if not await self._allowed(client, url):
                log.info("robots.txt disallows %s", url)
                return None
            resp = await client.get(url)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code >= 400 or "html" not in ctype:
                return None
            if self._check_public and str(resp.url) != url:
                await assert_public_url(str(resp.url))
            body = resp.text[:MAX_BODY_BYTES]
            page = parse_html(str(resp.url), resp.status_code, body)
            return await self._maybe_render(page, body)
        except (httpx.HTTPError, UnsafeURL) as exc:
            log.info("fetch failed for %s: %s", url, exc)
            return None
        finally:
            if own:
                await client.aclose()

    async def _maybe_render(self, page: Page, raw_html: str) -> Page:
        if self.renderer is None or not (
                self.rendering == "always" or looks_script_rendered(raw_html, page.text)):
            return page
        rendered = await self.renderer.render(page.url)
        if rendered is None or rendered.status >= 400:
            return page
        if registrable_domain(rendered.url) != registrable_domain(page.url):
            return page  # client-side redirect off-site: keep the original
        better = parse_html(rendered.url, rendered.status, rendered.html[:MAX_BODY_BYTES])
        better.rendered = True
        return better if len(better.text) >= len(page.text) else page

    def _render_session(self):
        return self.renderer.session() if self.renderer is not None else nullcontext()

    async def crawl(self, start_url: str, max_pages: int | None = None) -> list[Page]:
        """Breadth-first same-site crawl, priority pages first."""
        async with self._render_session():
            return await self._crawl(start_url, max_pages)

    async def _crawl(self, start_url: str, max_pages: int | None = None) -> list[Page]:
        max_pages = max_pages or get_settings().crawler_max_pages
        start_url = normalize_url(start_url)
        p = urlparse(start_url)
        origin = f"{p.scheme}://{p.netloc}"
        queue = [start_url] + [origin + path for path in PRIORITY_PATHS if path != "/"]
        seen: set[str] = set()
        pages: list[Page] = []
        async with self._client() as client:
            while queue and len(pages) < max_pages:
                batch = []
                while queue and len(batch) < 4:
                    u = queue.pop(0).rstrip("/") or origin
                    if u in seen or u.lower().endswith(SKIP_EXTENSIONS):
                        continue
                    seen.add(u)
                    batch.append(u)
                results = await asyncio.gather(*(self.fetch(u, client) for u in batch))
                for page in results:
                    if not page or len(pages) >= max_pages:
                        continue
                    if page.url.rstrip("/") in {pg.url.rstrip("/") for pg in pages}:
                        continue
                    pages.append(page)
                    # Discovered links: priority-looking ones jump the queue.
                    for link in page.links:
                        key = link.rstrip("/")
                        if key in seen or key.lower().endswith(SKIP_EXTENSIONS):
                            continue
                        path = urlparse(link).path.lower()
                        if any(h in path for h in PRIORITY_HINTS):
                            queue.insert(0, link)
                        elif path.count("/") <= 2:
                            queue.append(link)
        return pages
