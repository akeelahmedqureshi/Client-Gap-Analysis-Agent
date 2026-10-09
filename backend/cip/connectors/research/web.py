"""Polite web fetching and same-site crawling.

* Only http/https URLs that resolve to public IP addresses are fetched (SSRF guard:
  CSV rows and search results are untrusted input).
* robots.txt is honoured.
* Priority pages (about, products, pricing, …) are crawled first, then links
  discovered on the site are followed up to ``max_pages``.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import logging
import re
import socket
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from cip.core import usage
from cip.core.urls import is_valid_http_url, urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from cip.config import Settings, get_settings
from cip.connectors.research.browser import BrowserRenderer, looks_script_rendered
from cip.connectors.research import tls
from cip.connectors.research.cache import CachedPage, PageCache
from cip.core.language import detect as detect_language

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
RETRY_STATUS = {429, 500, 502, 503, 504}
QUIET_STATUS = {404, 410}  # a page that doesn't exist is an answer, not a fetch failure
# PDFs worth reading when found on a site (brochures, datasheets, pricing sheets, case studies…).
PDF_HINTS = ("pric", "brochure", "datasheet", "data-sheet", "product", "feature", "overview", "case", "whitepaper",
             "white-paper", "security", "compliance", "solution", "catalog", "spec")


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
    content_type: str = "html"  # html | pdf
    lang: str | None = None  # declared <html lang> or detected (core/language.py)
    alternates: dict[str, str] = field(default_factory=dict)  # hreflang -> URL of the page in that language
    cached: bool = False  # served from the research cache
    fetched_at: datetime | None = None  # set for cached pages: when the page was really fetched


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


def uses_proxy(url: str) -> bool:
    """True when an HTTP(S) proxy from the environment applies to ``url`` (httpx honours the same variables)."""
    from urllib.request import getproxies, proxy_bypass

    parsed = urlparse(url)
    return bool(getproxies().get(parsed.scheme)) and not proxy_bypass(parsed.hostname or "")


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
            # Behind an egress proxy the local resolver may not know public names; the proxy resolves them.
            # Opt-in (CIP_CRAWLER_PROXY_RESOLVES_DNS), because the address can then not be checked here.
            if get_settings().crawler_proxy_resolves_dns and uses_proxy(url):
                return
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
    declared = soup.html.get("lang") if soup.html else None
    alternates: dict[str, str] = {}
    for link in soup.find_all("link", hreflang=True, href=True):
        rel = link.get("rel") or []
        if "alternate" in (rel if isinstance(rel, list) else [rel]) and link["hreflang"].lower() != "x-default":
            href = normalize_url(urljoin(url, link["href"]))
            if is_valid_http_url(href):
                alternates[link["hreflang"].lower()] = href
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
        lang=detect_language(f"{title} {text}", declared), alternates=dict(list(alternates.items())[:30]),
    )


async def _meter_request(request: httpx.Request) -> None:
    """Count every research request against the run's usage meter and web budget (core/usage.py)."""
    meter = usage.current()
    if meter is None:
        return
    try:
        meter.record_web()
    except usage.BudgetExhausted as exc:
        raise BudgetRequestError(str(exc), request=request) from exc


class BudgetRequestError(httpx.RequestError):
    """The run's web request budget is spent: never retried."""


class FetchFailed(Exception):
    def __init__(self, reason: str, attempts: int = 1, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.attempts = attempts
        self.detail = detail


# Network failures by cause, so "connection error" says what to fix (firewall, certificates, proxy…).
NETWORK_CAUSES = (
    ("tls_certificate", ("certificate_verify_failed", "certificate verify failed", "self signed certificate",
                         "unable to get local issuer")),
    ("tls_error", ("ssl", "tls", "wrong_version_number", "handshake")),
    ("dns_error", ("name or service not known", "temporary failure in name resolution", "nodename nor servname",
                   "getaddrinfo failed", "no address associated")),
    ("connection_refused", ("connection refused", "errno 111", "errno 61")),
    ("network_unreachable", ("network is unreachable", "errno 101", "errno 51")),
    ("no_route_to_host", ("no route to host", "errno 113", "errno 65")),
    ("connection_reset", ("connection reset", "errno 104", "errno 54")),
    ("proxy_error", ("proxy",)),
)


def network_cause(exc: BaseException) -> tuple[str, str]:
    """(reason, short detail) for a transport error, looking through the exception chain."""
    parts, seen, cur = [], set(), exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(f"{type(cur).__name__}: {cur}")
        cur = cur.__cause__ or cur.__context__
    text = " | ".join(parts)
    low = text.lower()
    if isinstance(exc, httpx.ProxyError):
        reason = "proxy_error"
    else:
        reason = next((r for r, cues in NETWORK_CAUSES if any(c in low for c in cues)), "connection_error")
    detail = next((str(p) for p in (exc.__cause__, exc.__context__, exc) if p is not None and str(p)), type(exc).__name__)
    return reason, " ".join(detail.split())[:200]


# Bot-protection services answer automated clients with a challenge page (a script to run, a CAPTCHA) instead of
# the site. Header signals are definitive; page markers count only on a page with almost no readable text, since
# some services also add their scripts to normal pages.
CHALLENGE_HEADERS = (("x-amzn-waf-action", "", "AWS WAF"), ("cf-mitigated", "challenge", "Cloudflare"),
                     ("x-datadome", "", "DataDome"))
CHALLENGE_MARKERS = (("awswaf", "AWS WAF"), ("aws-waf-token", "AWS WAF"), ("challenge-platform", "Cloudflare"),
                     ("cf-browser-verification", "Cloudflare"), ("_incapsula_resource", "Imperva"),
                     ("incapsula incident", "Imperva"), ("sucuri website firewall", "Sucuri"),
                     ("captcha-delivery.com", "DataDome"), ("px-captcha", "HUMAN (PerimeterX)"),
                     ("/_sec/cp_challenge", "Akamai"), ("sgcaptcha", "SiteGround"),
                     ("robot challenge screen", "SiteGround"), ("just a moment...", "Cloudflare"),
                     ("checking your browser", "a browser check"), ("verify you are human", "a CAPTCHA"),
                     ("are you a robot", "a CAPTCHA"), ("captcha", "a CAPTCHA"))
CHALLENGE_MAX_TEXT = 300


def bot_challenge(status: int, headers, body: str, text: str | None = None) -> str | None:
    """The bot-protection service behind a challenge response, or None for a normal page."""
    for name, value, vendor in CHALLENGE_HEADERS:
        got = (headers.get(name) or "").lower() if headers else ""
        if got and (not value or value in got):
            return vendor
    if text is None:
        text = BeautifulSoup(body or "", "html.parser").get_text(" ", strip=True)
    if len(text.strip()) > CHALLENGE_MAX_TEXT:
        return None
    low = (body or "").lower()
    vendor = next((v for marker, v in CHALLENGE_MARKERS if marker in low), None)
    if vendor:
        return vendor
    # 202 ("accepted, not done") instead of a page is how several services answer while a check is pending.
    return "unidentified bot protection (HTTP 202 without page content)" if status == 202 else None


def unsafe_reason(exc: UnsafeURL) -> str:
    text = str(exc)
    return "unresolvable" if text.startswith("Cannot resolve") else \
        "unsupported_url" if text.startswith("Unsupported") else "blocked_address"


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after", "")
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def extract_pdf(data: bytes, max_pages: int, max_chars: int = 200_000) -> tuple[str, str]:
    """(title, text) of a PDF. Encrypted or malformed files yield empty text."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            return "", ""
    title = ""
    try:
        title = str((reader.metadata or {}).get("/Title") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    parts: list[str] = []
    size = 0
    for page in reader.pages[:max_pages]:
        text = page.extract_text() or ""
        parts.append(text)
        size += len(text)
        if size >= max_chars:
            break
    return title[:300], " ".join(" ".join(parts).split())[:max_chars]


def _with_attempts(cause: tuple[str, str], attempts: int) -> tuple[str, int, str]:
    return cause[0], attempts, cause[1]


def is_pdf(url: str, content_type: str) -> bool:
    return "pdf" in content_type.lower() or (urlparse(url).path.lower().endswith(".pdf")
                                             and "octet-stream" in content_type.lower())


class WebFetcher:
    """Fetches public pages for the agents of one run.

    Politeness and resilience: at most ``crawler_domain_concurrency`` requests per domain at once, spaced by
    ``crawler_domain_delay_seconds`` on the live network, and timeouts, connection errors, 429 and 5xx are
    retried (``crawler_retries``, exponential backoff, ``Retry-After`` honoured up to 10 s). Every fetch that
    still fails is recorded with its reason (``failures`` and the run's usage meter); a missing page (404/410)
    is not a failure.

    With a ``cache`` (services/runner.py passes the organization's research cache) pages are reused across
    agents and runs; ``refresh=True`` (partial re-runs) skips cache reads but still stores what it fetches.
    ``cached_at`` maps each URL served from the cache to when it was really fetched.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, check_public: bool = True,
                 rendering: str | None = None, renderer: BrowserRenderer | None = None,
                 cache: PageCache | None = None, refresh: bool = False, settings: Settings | None = None) -> None:
        s = self._settings = settings or get_settings()
        self._transport = transport
        self._check_public = check_public and transport is None
        self._timeout = s.crawler_timeout_seconds
        self._ua = s.crawler_user_agent
        self._robots: dict[str, RobotFileParser | None] = {}
        # A mocked transport can't be seen by a real browser, so rendering defaults off for it.
        self.rendering = rendering or ("never" if transport is not None else s.browser_rendering)
        self.renderer = renderer or (BrowserRenderer(s, self._check_public) if self.rendering != "never" else None)
        self.cache = cache
        self.refresh = refresh
        self.cached_at: dict[str, datetime] = {}
        # Per agent (core/usage.py tags the running agent): URLs served from the cache, and fetched live.
        self._cached_by: dict[str, dict[str, datetime]] = {}
        self._live: dict[str, set[str]] = {}
        # Run-wide: the cache date of a URL any agent read from the cache, else None (only seen live).
        self._first: dict[str, datetime | None] = {}
        self.failures: list[dict] = []
        self._slots: dict[str, asyncio.Semaphore] = {}
        self._next_at: dict[str, float] = {}
        self._concurrency = max(1, s.crawler_domain_concurrency)
        # Spacing and backoff only matter against real servers; mocked transports run at full speed.
        self._delay = s.crawler_domain_delay_seconds if transport is None else 0.0
        self._backoff = s.crawler_retry_backoff_seconds if transport is None else 0.0
        self._retries = max(0, s.crawler_retries)
        self._max_pdfs = s.crawler_max_pdfs
        self._pdf_bytes = s.pdf_max_bytes
        self._pdf_pages = s.pdf_max_pages

    @property
    def live(self) -> bool:
        """True when requests go to the real network (no injected transport) — a real browser can follow."""
        return self._transport is None

    def _client(self, follow_redirects: bool = True, accept: str = "text/html,application/xhtml+xml") \
            -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, follow_redirects=follow_redirects, transport=self._transport,
                                 verify=tls.context() if self._transport is None else True,
                                 headers={"User-Agent": self._ua, "Accept": accept},
                                 event_hooks={"request": [_meter_request]})

    # --- politeness, retries, failure states ------------------------------------------------------
    def record_failure(self, url: str, reason: str, attempts: int = 1, detail: str = "") -> None:
        if len(self.failures) < 200:
            self.failures.append({"url": url, "domain": registrable_domain(url), "reason": reason,
                                  "attempts": attempts, "detail": detail})
        meter = usage.current()
        if meter is not None:
            meter.record_web_failure(url, reason, attempts, detail)
        # Warning (not info) so an unreachable site is visible in the server log without debug logging.
        log.warning("fetch failed for %s: %s%s (attempts: %s)", url, reason, f" — {detail}" if detail else "", attempts)

    def cache_date(self, url: str, agent: str | None = None) -> datetime | None:
        """When ``url``'s content was really fetched, if ``agent`` (default: the running agent) read it only
        from the cache."""
        agent = usage.current_agent() if agent is None else agent
        key = url.rstrip("/")
        if key in self._live.get(agent, ()):
            return None
        if key in self._cached_by.get(agent, {}):
            return self._cached_by[agent][key]
        # Evidence derived from an earlier agent's pages: dated by the cached copy if the run used one.
        return self._first.get(key)

    def _mark_live(self, *urls: str) -> None:
        keys = {u.rstrip("/") for u in urls}
        self._live.setdefault(usage.current_agent(), set()).update(keys)
        for k in keys:
            self._first.setdefault(k, None)

    def last_failure(self, url: str) -> dict | None:
        return next((f for f in reversed(self.failures) if f["url"] == url), None)

    @asynccontextmanager
    async def _slot(self, url: str):
        domain = registrable_domain(url)
        sem = self._slots.setdefault(domain, asyncio.Semaphore(self._concurrency))
        async with sem:
            if self._delay:
                now = asyncio.get_running_loop().time()
                start = max(now, self._next_at.get(domain, 0.0))
                self._next_at[domain] = start + self._delay
                if start > now:
                    await asyncio.sleep(start - now)
            yield

    async def _request(self, client: httpx.AsyncClient, method: str, url: str, **kw) -> tuple[httpx.Response, int]:
        attempt = 0
        repaired = False
        while True:
            attempt += 1
            try:
                async with self._slot(url):
                    resp = await client.request(method, url, **kw)
            except BudgetRequestError as exc:
                raise FetchFailed("budget_exhausted", attempt) from exc
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                cause = network_cause(exc) if not isinstance(exc, httpx.TimeoutException) else ("timeout", "")
                # A site that doesn't send its intermediate certificate: fetch it as browsers do, then retry.
                if (cause[0] == "tls_certificate" and "local issuer" in cause[1] and not repaired
                        and self._transport is None and await tls.repair(url, timeout=self._timeout)):
                    repaired = True
                    continue
                if attempt > self._retries:
                    if isinstance(exc, httpx.TimeoutException):
                        raise FetchFailed("timeout", attempt, type(exc).__name__) from exc
                    raise FetchFailed(*_with_attempts(network_cause(exc), attempt)) from exc
                await asyncio.sleep(self._backoff * 2 ** (attempt - 1))
                continue
            except httpx.ProxyError as exc:
                raise FetchFailed(*_with_attempts(network_cause(exc), attempt)) from exc
            except httpx.HTTPError as exc:
                raise FetchFailed(type(exc).__name__, attempt, str(exc)[:200]) from exc
            if resp.status_code in RETRY_STATUS and attempt <= self._retries:
                wait = _retry_after(resp) if self._backoff else None
                await asyncio.sleep(min(wait, 10.0) if wait is not None else self._backoff * 2 ** (attempt - 1))
                continue
            return resp, attempt

    async def _allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                resp, _ = await self._request(client, "GET", origin + "/robots.txt")
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None
            except FetchFailed as exc:
                if exc.reason == "budget_exhausted":
                    raise
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(self._ua, url)

    # --- cache ------------------------------------------------------------------------------------
    async def _from_cache(self, url: str) -> tuple[Page, str] | None:
        if self.cache is None or self.refresh:
            return None
        hit = await self.cache.get(url)
        if hit is None:
            return None
        if hit.kind == "pdf":
            page, html = Page(url=hit.final_url, status=hit.status, title=hit.title, text=hit.body,
                              content_type="pdf"), ""
        else:
            page, html = parse_html(hit.final_url, hit.status, hit.body), hit.body
            page.rendered = hit.rendered
        page.cached, page.fetched_at = True, hit.fetched_at
        self.cached_at[url] = self.cached_at[hit.final_url] = hit.fetched_at
        mine = self._cached_by.setdefault(usage.current_agent(), {})
        mine[url.rstrip("/")] = mine[hit.final_url.rstrip("/")] = hit.fetched_at
        for k in (url.rstrip("/"), hit.final_url.rstrip("/")):
            if self._first.get(k) is None:  # when unsure, the older (cached) date wins: never overstate freshness
                self._first[k] = hit.fetched_at
        meter = usage.current()
        if meter is not None:
            meter.record_cache_hit()
        return page, html

    async def _store(self, url: str, page: Page, body: str) -> None:
        if self.cache is not None:
            await self.cache.put(CachedPage(url=url, final_url=page.url, status=page.status, kind=page.content_type,
                                            body=body, title=page.title, rendered=page.rendered))

    # --- fetching ---------------------------------------------------------------------------------
    async def fetch(self, url: str, client: httpx.AsyncClient | None = None) -> Page | None:
        got = await self.fetch_with_html(url, client)
        return got[0] if got else None

    async def fetch_with_html(self, url: str, client: httpx.AsyncClient | None = None) -> tuple[Page, str] | None:
        """Like ``fetch`` but also returns the HTML the page was parsed from (rendered HTML if rendered).
        PDFs come back as a text-only ``Page`` (``content_type="pdf"``) with empty HTML."""
        url = normalize_url(url)
        cached = await self._from_cache(url)
        if cached is not None:
            return cached
        own = client is None
        client = client or self._client()
        try:
            if self._check_public:
                await assert_public_url(url)
            if not await self._allowed(client, url):
                self.record_failure(url, "robots_disallowed")
                return None
            resp, attempts = await self._request(client, "GET", url)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code >= 400:
                if resp.status_code not in QUIET_STATUS:
                    self.record_failure(url, f"http_{resp.status_code}", attempts)
                return None
            if self._check_public and str(resp.url) != url:
                await assert_public_url(str(resp.url))
            if is_pdf(str(resp.url), ctype):
                page = await self._pdf_page(url, resp)
                return (page, "") if page else None
            if "html" not in ctype:
                return None
            body = resp.text[:MAX_BODY_BYTES]
            page = parse_html(str(resp.url), resp.status_code, body)
            challenge = bot_challenge(resp.status_code, resp.headers, body, page.text)
            page, html = await self._maybe_render(page, body, force=challenge is not None)
            if challenge and (not page.rendered or bot_challenge(page.status, None, html, page.text)):
                # Only the challenge came back (no browser, or it didn't pass): not a page of the site.
                self.record_failure(url, "bot_challenge", attempts, challenge)
                return None
            self._mark_live(url, page.url)
            await self._store(url, page, html)
            return page, html
        except FetchFailed as exc:
            self.record_failure(url, exc.reason, exc.attempts, exc.detail)
            return None
        except UnsafeURL as exc:
            self.record_failure(url, unsafe_reason(exc))
            return None
        except httpx.HTTPError as exc:
            self.record_failure(url, type(exc).__name__)
            return None
        finally:
            if own:
                await client.aclose()

    async def _pdf_page(self, url: str, resp: httpx.Response) -> Page | None:
        if not self._max_pdfs:
            return None
        declared = resp.headers.get("content-length", "")
        if (declared.isdigit() and int(declared) > self._pdf_bytes) or len(resp.content) > self._pdf_bytes:
            self.record_failure(url, "too_large")
            return None
        try:
            title, text = await asyncio.wait_for(
                asyncio.to_thread(extract_pdf, resp.content, self._pdf_pages), timeout=30)
        except Exception:  # noqa: BLE001 - malformed or hostile PDFs are skipped, never crash research
            self.record_failure(url, "unreadable_pdf")
            return None
        if not text:
            self.record_failure(url, "unreadable_pdf")
            return None
        name = urlparse(str(resp.url)).path.rsplit("/", 1)[-1]
        page = Page(url=str(resp.url), status=resp.status_code, title=title or name, text=text, content_type="pdf")
        self._mark_live(url, page.url)
        await self._store(url, page, text)
        return page

    async def _simple(self, method: str, url: str, accept: str, **kw) -> httpx.Response | None:
        try:
            if self._check_public:
                await assert_public_url(url)
            async with self._client(accept=accept) as client:
                resp, attempts = await self._request(client, method, url, **kw)
            if resp.status_code >= 400 and resp.status_code not in QUIET_STATUS:
                self.record_failure(url, f"http_{resp.status_code}", attempts)
            return resp
        except FetchFailed as exc:
            self.record_failure(url, exc.reason, exc.attempts, exc.detail)
        except UnsafeURL as exc:
            self.record_failure(url, unsafe_reason(exc))
        return None

    async def get_json(self, url: str, headers: dict | None = None) -> dict | list | None:
        """GET a public JSON API (job boards, GitHub search). Same SSRF guard; returns None on any failure."""
        resp = await self._simple("GET", url, "application/json", headers=headers or {})
        try:
            return resp.json() if resp is not None and resp.status_code < 400 else None
        except ValueError:
            return None

    async def fetch_raw(self, url: str, follow_redirects: bool = True, evidence: bool = True) \
            -> tuple[int, httpx.Headers, str, str] | None:
        """(status, headers, final_url, body) for passive security checks; SSRF-guarded, robots-aware.
        ``evidence=False`` for probes whose response is never cited (domain checks)."""
        try:
            if self._check_public:
                await assert_public_url(url)
            async with self._client(follow_redirects=follow_redirects, accept="*/*") as client:
                if not await self._allowed(client, url):
                    self.record_failure(url, "robots_disallowed")
                    return None
                resp, _ = await self._request(client, "GET", url)
            if self._check_public and follow_redirects and str(resp.url) != url:
                await assert_public_url(str(resp.url))
            if evidence:
                self._mark_live(url, str(resp.url))
            return resp.status_code, resp.headers, str(resp.url), resp.text[:20_000]
        except FetchFailed as exc:
            self.record_failure(url, exc.reason, exc.attempts, exc.detail)
        except UnsafeURL as exc:
            self.record_failure(url, unsafe_reason(exc))
        return None

    async def post_json(self, url: str, payload: dict) -> dict | list | None:
        """POST to a public JSON API (e.g. OSV.dev). Same SSRF guard; None on failure."""
        resp = await self._simple("POST", url, "application/json", json=payload)
        try:
            return resp.json() if resp is not None and resp.status_code < 400 else None
        except ValueError:
            return None

    async def get_text(self, url: str, headers: dict | None = None, max_chars: int = 200_000) -> str | None:
        """GET a public text resource (e.g. a README via the GitHub API). Same SSRF guard."""
        resp = await self._simple("GET", url, "*/*", headers=headers or {})
        return resp.text[:max_chars] if resp is not None and resp.status_code < 400 else None

    async def _maybe_render(self, page: Page, raw_html: str, force: bool = False) -> tuple[Page, str]:
        if self.renderer is None or not (
                force or self.rendering == "always" or looks_script_rendered(raw_html, page.text)):
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
        max_pages = max_pages or self._settings.crawler_max_pages
        start_url = normalize_url(start_url)
        p = urlparse(start_url)
        origin = f"{p.scheme}://{p.netloc}"
        queue = [start_url] + [origin + path for path in prefer] + \
            [origin + path for path in PRIORITY_PATHS if path != "/" and path not in prefer]
        seen: set[str] = set()
        pages: list[Page] = []
        pdfs = 0

        def skip(u: str) -> bool:
            low = u.lower()
            if low.endswith(".pdf"):  # only a few PDFs that look like product or pricing material
                return pdfs >= self._max_pdfs or not any(h in urlparse(low).path for h in PDF_HINTS)
            return low.endswith(SKIP_EXTENSIONS)

        async with self._client() as client:
            while queue and len(pages) < max_pages:
                batch = []
                while queue and len(batch) < 4:
                    u = queue.pop(0).rstrip("/") or origin
                    if u in seen or skip(u):
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
                    pdfs += page.content_type == "pdf"
                    # Discovered links: priority-looking ones jump the queue.
                    for link in page.links:
                        key = link.rstrip("/")
                        if key in seen or skip(key):
                            continue
                        path = urlparse(link).path.lower()
                        if path.endswith(".pdf"):
                            queue.append(link)  # after the HTML pages
                        elif any(h in path for h in PRIORITY_HINTS):
                            queue.insert(0, link)
                        elif path.count("/") <= 2:
                            queue.append(link)
        return pages
