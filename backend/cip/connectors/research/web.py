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
import json
import logging
import re
import socket
from contextlib import nullcontext
from dataclasses import dataclass, field
from cip.core.urls import is_valid_http_url, urldefrag, urljoin, urlparse
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
    structured_data: list[dict] = field(default_factory=list)  # schema.org JSON-LD objects
    link_texts: list[tuple[str, str]] = field(default_factory=list)  # (same-site url, anchor text)


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


MAX_JSONLD_OBJECTS = 40


def _flatten_jsonld(node, out: list[dict]) -> None:
    if len(out) >= MAX_JSONLD_OBJECTS:
        return
    if isinstance(node, list):
        for n in node:
            _flatten_jsonld(n, out)
    elif isinstance(node, dict):
        if "@graph" in node:
            _flatten_jsonld(node["@graph"], out)
        if "@type" in node:
            out.append(node)


def extract_jsonld(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            _flatten_jsonld(json.loads(raw), out)
        except ValueError:
            continue
    return out


def parse_html(url: str, status: int, html: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    scripts = [s.get("src") for s in soup.find_all("script") if s.get("src")]
    structured = extract_jsonld(soup)
    gen = soup.find("meta", attrs={"name": "generator"})
    desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    title = soup.title.get_text(strip=True) if soup.title else ""
    headings = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"])][:60]

    links: list[str] = []
    link_texts: list[tuple[str, str]] = []
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
        if not is_valid_http_url(absolute):
            continue  # malformed links (bad host/port) are ignored, never crash the crawl
        if registrable_domain(absolute) == base_domain:
            links.append(absolute)
            anchor = " ".join(a.get_text(" ", strip=True).split())[:200]
            if anchor and len(link_texts) < 300:
                link_texts.append((absolute, anchor))
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
        generator=gen.get("content") if gen else None, structured_data=structured, link_texts=link_texts,
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

    @property
    def live(self) -> bool:
        """True when requests go to the real network (no injected transport) — a real browser can follow."""
        return self._transport is None

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
        got = await self.fetch_with_html(url, client)
        return got[0] if got else None

    async def fetch_with_html(self, url: str, client: httpx.AsyncClient | None = None) -> tuple[Page, str] | None:
        """Like ``fetch`` but also returns the HTML the page was parsed from (rendered HTML if rendered)."""
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

    async def get_json(self, url: str, headers: dict | None = None) -> dict | list | None:
        """GET a public JSON API (job boards, GitHub search). Same SSRF guard; returns None on any failure."""
        try:
            if self._check_public:
                await assert_public_url(url)
            async with self._client() as client:
                resp = await client.get(url, headers={"Accept": "application/json", **(headers or {})})
            if resp.status_code >= 400:
                return None
            return resp.json()
        except (httpx.HTTPError, UnsafeURL, ValueError) as exc:
            log.info("JSON fetch failed for %s: %s", url, exc)
            return None

    async def fetch_raw(self, url: str, follow_redirects: bool = True) -> tuple[int, httpx.Headers, str, str] | None:
        """(status, headers, final_url, body) for passive security checks; SSRF-guarded, robots-aware."""
        try:
            if self._check_public:
                await assert_public_url(url)
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=follow_redirects,
                                         transport=self._transport, headers={"User-Agent": self._ua}) as client:
                if not await self._allowed(client, url):
                    return None
                resp = await client.get(url)
            if self._check_public and follow_redirects and str(resp.url) != url:
                await assert_public_url(str(resp.url))
            return resp.status_code, resp.headers, str(resp.url), resp.text[:20_000]
        except (httpx.HTTPError, UnsafeURL) as exc:
            log.info("raw fetch failed for %s: %s", url, exc)
            return None

    async def post_json(self, url: str, payload: dict) -> dict | list | None:
        """POST to a public JSON API (e.g. OSV.dev). Same SSRF guard; None on failure."""
        try:
            if self._check_public:
                await assert_public_url(url)
            async with self._client() as client:
                resp = await client.post(url, json=payload, headers={"Accept": "application/json"})
            return resp.json() if resp.status_code < 400 else None
        except (httpx.HTTPError, UnsafeURL, ValueError) as exc:
            log.info("JSON POST failed for %s: %s", url, exc)
            return None

    async def get_text(self, url: str, headers: dict | None = None, max_chars: int = 200_000) -> str | None:
        """GET a public text resource (e.g. a README via the GitHub API). Same SSRF guard."""
        try:
            if self._check_public:
                await assert_public_url(url)
            async with self._client() as client:
                resp = await client.get(url, headers=headers or {})
            return resp.text[:max_chars] if resp.status_code < 400 else None
        except (httpx.HTTPError, UnsafeURL) as exc:
            log.info("text fetch failed for %s: %s", url, exc)
            return None

    async def _maybe_render(self, page: Page, raw_html: str) -> tuple[Page, str]:
        if self.renderer is None or not (
                self.rendering == "always" or looks_script_rendered(raw_html, page.text)):
            return page, raw_html
        rendered = await self.renderer.render(page.url)
        if rendered is None or rendered.status >= 400:
            return page, raw_html
        if registrable_domain(rendered.url) != registrable_domain(page.url):
            return page, raw_html  # client-side redirect off-site: keep the original
        html = rendered.html[:MAX_BODY_BYTES]
        better = parse_html(rendered.url, rendered.status, html)
        better.rendered = True
        return (better, html) if len(better.text) >= len(page.text) else (page, raw_html)

    def _render_session(self):
        return self.renderer.session() if self.renderer is not None else nullcontext()

    async def crawl(self, start_url: str, max_pages: int | None = None, prefer: list[str] | None = None) -> list[Page]:
        """Breadth-first same-site crawl, priority pages first (``prefer`` paths before everything else)."""
        async with self._render_session():
            return await self._crawl(start_url, max_pages, prefer or [])

    async def _crawl(self, start_url: str, max_pages: int | None = None, prefer: list[str] = ()) -> list[Page]:
        max_pages = max_pages or get_settings().crawler_max_pages
        start_url = normalize_url(start_url)
        p = urlparse(start_url)
        origin = f"{p.scheme}://{p.netloc}"
        queue = [start_url] + [origin + path for path in prefer] + \
            [origin + path for path in PRIORITY_PATHS if path != "/" and path not in prefer]
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
