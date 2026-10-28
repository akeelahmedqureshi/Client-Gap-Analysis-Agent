"""The client's contact details and official online presence, validated.

Collected only from the company's own website (its pages, ``mailto:``/``tel:`` links, ``<address>`` blocks and
schema.org data), plus links supplied in the CSV or found by search, which are never trusted on their own.
Facebook, Instagram, X/Twitter and YouTube are excluded; personal LinkedIn profiles, share links and
third-party directories are never collected.

Every item gets a status (``core.schemas.ContactStatus``). **Verified** is strict:

* a page of the official website, an email on the company's own domain, or a phone number / address published
  on the official website — "published on the official website";
* a profile link must be published on the official website **and** open **and** identify the company at
  the destination (its name, or a link back to the company's domain). A link that only returns HTTP 200,
  or merely looks valid, is never verified.

A link the official site publishes but whose destination cannot be checked (sign-in walls, robots rules) is
``official_link``; links that redirect elsewhere, do not open, or name another organization are flagged.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from cip.connectors.research.web import Page, WebFetcher, parse_html, registrable_domain
from cip.core.schemas import Contact
from cip.core.urls import urlparse

EXCLUDED_HOSTS = ("facebook.com", "fb.com", "fb.me", "instagram.com", "twitter.com", "x.com", "t.co",
                  "youtube.com", "youtu.be")
PROFILE_HOSTS = {"linkedin.com": "linkedin", "github.com": "github", "gitlab.com": "gitlab",
                 "apps.apple.com": "app_store", "itunes.apple.com": "app_store", "play.google.com": "google_play"}
ROLE_EMAIL_LOCALPARTS = {
    "info", "contact", "hello", "sales", "support", "help", "press", "media", "partners", "partnerships",
    "careers", "jobs", "hr", "billing", "accounts", "office", "enquiries", "inquiries", "team", "admin",
    "marketing", "security", "privacy", "legal", "service", "customerservice", "customercare", "care",
}
LEGAL_WORDS = {"inc", "llc", "ltd", "limited", "gmbh", "corp", "corporation", "co", "company", "plc", "group",
               "the", "holdings", "sa", "ag", "bv", "pvt", "private", "llp", "pty"}
SIGN_IN = ("login", "log_in", "log-in", "signin", "sign-in", "sign_in", "authwall", "signup", "sign_up", "checkpoint", "uas/")
MAX_CHECKS = 12

LABELS = {"website": "Official website", "about_page": "About page", "contact_page": "Contact page",
          "email": "Email", "phone": "Phone", "address": "Address", "linkedin": "LinkedIn", "github": "GitHub",
          "gitlab": "GitLab", "app_store": "App Store", "google_play": "Google Play", "docs": "Documentation",
          "developer_portal": "Developer portal", "community": "Community"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def name_tokens(name: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    return [w for w in words if w not in LEGAL_WORDS and (len(w) > 1 or w.isdigit())]


def identifies(page: Page, company: str, domain: str | None) -> str | None:
    """How the page identifies the company (its name, or a link back to its domain); None if it does not."""
    tokens = name_tokens(company)
    head = " ".join([page.site_name, page.title, page.description, " ".join(page.headings[:5])]).lower()
    body = page.text[:6000].lower()
    if tokens and all(re.search(rf"\b{re.escape(t)}\b", head) for t in tokens):
        return f"the page names “{company}”"
    if tokens and len(tokens) > 1 and " ".join(tokens) in re.sub(r"[^a-z0-9]+", " ", body):
        return f"the page text names “{company}”"
    if domain:
        dom = domain.lower()
        if any(registrable_domain(u) == dom for u in page.external_links) or dom in body:
            return f"the page links back to {dom}"
    return None


def _account_name(page: Page) -> str:
    raw = page.site_name or page.title
    for sep in (" | ", " · ", " - ", " – ", " — ", ": "):
        if sep in raw:
            raw = raw.split(sep)[0]
    return raw.strip()[:120]


def _profile_type(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in EXCLUDED_HOSTS):
        return None
    path = urlparse(url).path.lower()
    for h, kind in PROFILE_HOSTS.items():
        if host == h or host.endswith("." + h):
            if kind == "linkedin":
                # Company, school and showcase pages only; never personal profiles, posts or share links.
                return kind if re.match(r"^/(company|school|showcase)/[^/]+", path) else None
            if kind in ("github", "gitlab"):
                return kind if re.match(r"^/[^/]+/?$", path) or re.match(r"^/[^/]+/[^/]+/?$", path) else None
            if kind == "app_store":
                return kind if "/app/" in path else None
            if kind == "google_play":
                return kind if "/store/apps/details" in path else None
    return None


def _same_destination(original: str, final: str) -> bool:
    a, b = urlparse(original), urlparse(final)
    host = lambda h: (h or "").lower().removeprefix("www.")  # noqa: E731
    return host(a.hostname) == host(b.hostname) and a.path.rstrip("/").lower() == b.path.rstrip("/").lower()


def _clean_phone(raw: str) -> str:
    return " ".join(raw.split())


def _postal_addresses(pages: list[Page]) -> list[tuple[str, str]]:
    """(address, page url) from schema.org PostalAddress objects and <address> blocks on official pages."""
    out: list[tuple[str, str]] = []

    def walk(obj, url: str) -> None:
        if isinstance(obj, list):
            for o in obj:
                walk(o, url)
        elif isinstance(obj, dict):
            if obj.get("@type") == "PostalAddress" or "streetAddress" in obj:
                country = obj.get("addressCountry")
                country = country.get("name") if isinstance(country, dict) else country
                parts = [obj.get(k) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")]
                text = ", ".join(str(p).strip() for p in [*parts, country] if isinstance(p, (str, int)) and str(p).strip())
                if text:
                    out.append((text, url))
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    walk(v, url)

    for p in pages:
        walk(p.structured_data, p.url)
        out += [(a, p.url) for a in p.addresses]
    seen, unique = set(), []
    for text, url in out:
        key = re.sub(r"\W+", "", text.lower())
        if key and key not in seen:
            seen.add(key)
            unique.append((text, url))
    return unique[:5]


def collect(pages: list[Page], company: str, domain: str | None, extra_links: list[tuple[str, str]] = ()
            ) -> tuple[list[Contact], int]:
    """Candidates from the official website (``pages``) and other sources (``extra_links``: (url, source)).

    Returns the candidates and how many personal email addresses were ignored."""
    found: dict[tuple[str, str], Contact] = {}
    ignored_personal = 0
    official = [p for p in pages if not domain or registrable_domain(p.url) == domain]

    def add(c: Contact) -> None:
        key = (c.type, c.value.lower().rstrip("/"))
        if key not in found or (c.official_source and not found[key].official_source):
            found[key] = c

    if official:
        home = official[0]
        add(Contact(type="website", value=home.url, source=home.url, confidence=0.95, official_source=True))
    for p in official:
        path = urlparse(p.url).path.lower()
        if re.search(r"/(contact|contact-us|get-in-touch)(/|$)", path):
            add(Contact(type="contact_page", value=p.url, source=p.url, confidence=0.95, official_source=True))
        elif re.search(r"/(about|about-us|company|who-we-are)(/|$)", path):
            add(Contact(type="about_page", value=p.url, source=p.url, confidence=0.95, official_source=True))
        for email in p.emails:
            local, _, dom = email.partition("@")
            if local.split("+")[0] not in ROLE_EMAIL_LOCALPARTS:
                ignored_personal += 1  # personal data is never collected
                continue
            add(Contact(type="email", value=email, source=p.url, confidence=0.9, official_source=True))
        contact_like = "contact" in path or p is home or "about" in path
        for phone in (p.phones if contact_like else [])[:3]:
            add(Contact(type="phone", value=_clean_phone(phone), source=p.url, confidence=0.85,
                        official_source=True))
        for link in p.external_links:
            kind = _profile_type(link)
            if kind:
                add(Contact(type=kind, value=link.rstrip("/"), source=p.url, confidence=0.8, official_source=True))
            else:
                host = (urlparse(link).hostname or "").lower()
                if domain and registrable_domain(link) == domain and host.startswith(("docs.", "developer.",
                                                                                         "developers.", "api.")):
                    add(Contact(type="docs" if host.startswith("docs.") else "developer_portal", value=link,
                                source=p.url, confidence=0.85, official_source=True))
        for obj in p.structured_data:  # schema.org sameAs: the company's own list of profiles
            same = obj.get("sameAs") if isinstance(obj, dict) else None
            for u in same if isinstance(same, list) else [same]:
                if isinstance(u, str) and (kind := _profile_type(u)):
                    add(Contact(type=kind, value=u.rstrip("/"), source=p.url, confidence=0.85,
                                official_source=True))
    for text, url in _postal_addresses(official):
        add(Contact(type="address", value=text, source=url, confidence=0.85, official_source=True))
    for link, source in extra_links:
        kind = _profile_type(link)
        if kind:
            add(Contact(type=kind, value=link.rstrip("/"), source=source, confidence=0.5, official_source=False))
    return list(found.values()), ignored_personal


async def _check_link(fetcher: WebFetcher, c: Contact, company: str, domain: str | None) -> Contact:
    c.verified_at = _now()
    raw = await fetcher.fetch_raw(c.value, evidence=False)
    if raw is None:
        failure = fetcher.last_failure(c.value) or {}
        reason = failure.get("reason", "no response").replace("_", " ")
        if failure.get("reason") == "robots_disallowed":
            detail = "the site does not allow automated checks"
        elif failure.get("reason") == "http_404" or failure.get("reason") == "http_410":
            c.status, c.status_detail = "inaccessible", "the page was not found"
            c.method = "opened the link"
            return c
        else:
            detail = f"could not be opened from this server ({reason})"
        c.status = "official_link" if c.official_source else "unverified"
        c.status_detail = detail
        c.method = "opened the link"
        return c
    status, _, final_url, body = raw
    c.method = "opened the link and checked who it belongs to"
    if final_url and not _same_destination(c.value, final_url):
        c.final_url = final_url
        if any(s in final_url.lower() for s in SIGN_IN):
            c.status = "official_link" if c.official_source else "unverified"
            c.status_detail = "the platform requires signing in to view it, so its owner could not be checked"
            return c
        if registrable_domain(final_url) != registrable_domain(c.value) or urlparse(final_url).path in ("", "/"):
            c.status, c.status_detail = "redirected", f"redirects to {final_url}"
            return c
    if status in (404, 410):
        c.status, c.status_detail = "inaccessible", "the page was not found"
        return c
    if status >= 400:
        c.status = "official_link" if c.official_source else "unverified"
        c.status_detail = f"the platform refused automated access (HTTP {status}), so its owner could not be checked"
        return c
    page = parse_html(final_url or c.value, status, body)
    c.account_name = _account_name(page)
    how = identifies(page, company, domain)
    if not how:
        c.status = "mismatch"
        c.status_detail = (f"the destination does not identify {company}"
                           + (f" (it shows “{c.account_name}”)" if c.account_name else "")
                           + "; it may belong to another organization")
        return c
    if c.official_source:
        c.status, c.status_detail = "verified", f"published on the official website; {how}"
    else:
        c.status = "unverified"
        c.status_detail = f"{how}, but the official website does not link to it"
    return c


async def verify(fetcher: WebFetcher, contacts: list[Contact], company: str, domain: str | None,
                 pages: list[Page]) -> list[Contact]:
    """Set each candidate's status; profile links are opened (at most ``MAX_CHECKS``)."""
    checked_at = _now()
    official_urls = {p.url.rstrip("/") for p in pages}
    links = []
    for c in contacts:
        if c.type in ("website", "contact_page", "about_page"):
            c.verified_at, c.method = checked_at, "fetched as part of the official website"
            home = pages[0] if pages and c.type == "website" else next(
                (p for p in pages if p.url.rstrip("/") == c.value.rstrip("/")), None)
            how = identifies(home, company, domain) if home else None
            if c.value.rstrip("/") in official_urls and (how or c.type != "website"):
                c.status = "verified"
                c.status_detail = ("the website the analysis researched" + (f"; {how}" if how else "")
                                   if c.type == "website" else "a page of the official website")
            else:
                c.status = "unverified"
                c.status_detail = f"the home page does not identify {company}"
        elif c.type == "email":
            c.verified_at, c.method = checked_at, "found on the official website"
            on_domain = domain and registrable_domain("https://" + c.value.split("@")[-1]) == domain
            c.status = "verified" if on_domain else "unverified"
            c.status_detail = ("published on the official website, on the company's own domain" if on_domain else
                               "published on the official website, but on another domain")
        elif c.type in ("phone", "address"):
            c.verified_at, c.method = checked_at, "found on the official website"
            c.status = "verified" if c.official_source else "unverified"
            c.status_detail = "published on the official website" if c.official_source else "not on the official website"
        elif c.type in ("docs", "developer_portal"):
            links.append(c)
        else:
            links.append(c)
    gate = asyncio.Semaphore(4)

    async def one(c: Contact) -> None:
        async with gate:
            await _check_link(fetcher, c, company, domain)

    for c in links[MAX_CHECKS:]:
        c.verified_at, c.method = checked_at, "not checked"
        c.status = "official_link" if c.official_source else "unverified"
        c.status_detail = "not checked (too many links)"
    await asyncio.gather(*(one(c) for c in links[:MAX_CHECKS]))
    order = list(LABELS)
    return sorted(contacts, key=lambda c: (order.index(c.type) if c.type in order else 99, c.value))
