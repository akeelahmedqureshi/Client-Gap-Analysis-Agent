"""Deterministic company enrichment from crawled pages.

* schema.org JSON-LD (Organization / Corporation / LocalBusiness …): legal name, founding date,
  address, employee count, social profiles, brands, sub-organizations, areas served — the company's
  own machine-readable statements, so high-confidence evidence;
* legal name from the footer copyright line;
* careers: job-board (ATS) detection with their *official public JSON APIs* (Greenhouse, Lever,
  Ashby — no scraping), JSON-LD JobPosting, and grouping of open roles into technology hiring signals;
* announcements: recent blog / news / press / changelog posts, with product launches flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from cip.connectors.research.web import Page, WebFetcher

ORG_TYPES = {"organization", "corporation", "localbusiness", "softwarecompany", "onlinebusiness",
             "medicalorganization", "educationalorganization", "ngo", "store", "professionalservice"}


@dataclass
class Fact:
    label: str
    value: str
    source_url: str
    extracted_text: str
    confidence: float


def _types(obj: dict) -> set[str]:
    t = obj.get("@type")
    return {x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}


def _names(v) -> list[str]:
    items = v if isinstance(v, list) else [v]
    out = []
    for i in items:
        if isinstance(i, str) and i.strip():
            out.append(i.strip())
        elif isinstance(i, dict) and isinstance(i.get("name"), str):
            out.append(i["name"].strip())
    return out


def _address(a) -> str | None:
    if isinstance(a, list):
        a = a[0] if a else None
    if isinstance(a, str):
        return a.strip() or None
    if isinstance(a, dict):
        country = a.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts = [a.get("addressLocality"), a.get("addressRegion"), country]
        joined = ", ".join(p for p in parts if isinstance(p, str) and p.strip())
        return joined or None
    return None


def _employees(v) -> str | None:
    if isinstance(v, (int, str)) and str(v).strip():
        return str(v).strip()
    if isinstance(v, dict):
        if v.get("value"):
            return str(v["value"])
        lo, hi = v.get("minValue"), v.get("maxValue")
        if lo or hi:
            return f"{lo or '?'}-{hi or '?'}"
    return None


def organization_facts(pages: list[Page]) -> list[Fact]:
    facts: list[Fact] = []
    for page in pages:
        for obj in page.structured_data:
            if not (_types(obj) & ORG_TYPES):
                continue
            src = page.url

            def add(label: str, value: str | None, raw: str | None = None) -> None:
                if value:
                    facts.append(Fact(label, value, src, f"schema.org {label}: {raw or value}"[:300], 0.92))

            add("legal_name", obj.get("legalName") if isinstance(obj.get("legalName"), str) else None)
            founded = obj.get("foundingDate")
            if isinstance(founded, str) and re.match(r"^(18|19|20)\d{2}", founded):
                add("founded_year", founded[:4], founded)
            add("headquarters", _address(obj.get("address")))
            add("company_size", _employees(obj.get("numberOfEmployees")))
            if isinstance(obj.get("description"), str):
                add("description", obj["description"].strip()[:500])
            for b in _names(obj.get("brand")):
                add("brand", b)
            for s in _names(obj.get("subOrganization")):
                add("subsidiary", s)
            for a in _names(obj.get("areaServed")):
                add("geographic_market", a)
            same_as = obj.get("sameAs")
            for u in same_as if isinstance(same_as, list) else [same_as]:
                if isinstance(u, str) and u.startswith("http"):
                    add("social_profile", u)
    # De-duplicate (label, value), keeping the first source.
    seen: set[tuple[str, str]] = set()
    unique = []
    for f in facts:
        key = (f.label, f.value.lower())
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


_COPYRIGHT = re.compile(
    r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*(?:[-–]\s*\d{4})?\s*)?,?\s*"
    r"([A-Z][A-Za-z0-9&.,' \-]{1,80}?\s(?:Inc|LLC|L\.L\.C|Ltd|Limited|GmbH|AG|S\.A|SAS|SRL|B\.V|BV|N\.V|Corp|"
    r"Corporation|PLC|Pty Ltd|Co|LLP|Oy|AB|ApS|KK|S\.p\.A)\.?)(?![A-Za-z])",
    re.IGNORECASE,
)


def legal_name_from_footer(pages: list[Page]) -> Fact | None:
    for page in pages[:5]:
        tail = page.text[-1500:]  # footers are at the end of the visible text
        m = _COPYRIGHT.search(tail)
        if m:
            name = re.sub(r"^(?:all rights reserved\.?\s*)", "", m.group(1).strip(), flags=re.I)
            # Keep the dot only where it abbreviates ("Inc.", "Ltd.", "Corp.", "Co."); otherwise it ends the sentence.
            if name.endswith(".") and not re.search(r"\b(?:Inc|Ltd|Corp|Co|S\.A|B\.V|N\.V|S\.p\.A|L\.L\.C)\.$", name, re.I):
                name = name[:-1]
            return Fact("legal_name", name, page.url, m.group(0).strip()[:200], 0.8)
    return None


# --------------------------------------------------------------------------- careers

@dataclass
class AtsBoard:
    provider: str  # greenhouse | lever | ashby
    token: str
    board_url: str

    @property
    def api_url(self) -> str:
        if self.provider == "greenhouse":
            return f"https://boards-api.greenhouse.io/v1/boards/{self.token}/jobs"
        if self.provider == "lever":
            return f"https://api.lever.co/v0/postings/{self.token}?mode=json"
        return f"https://api.ashbyhq.com/posting-api/job-board/{self.token}"


_ATS_PATTERNS = [
    ("greenhouse", re.compile(r"^https?://(?:boards|job-boards)\.(?:eu\.)?greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"^https?://jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"^https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
]
CAREERS_HINTS = ("career", "jobs", "join-us", "join", "work-with-us", "hiring", "vacanc", "openings")


def find_ats_boards(pages: list[Page]) -> list[AtsBoard]:
    boards: dict[tuple[str, str], AtsBoard] = {}
    for page in pages:
        for link in page.external_links + page.scripts:
            for provider, pat in _ATS_PATTERNS:
                m = pat.match(link or "")
                if m and m.group(1).lower() not in {"embed", "v1"}:
                    boards.setdefault((provider, m.group(1).lower()), AtsBoard(provider, m.group(1), link))
    return list(boards.values())


@dataclass
class Job:
    title: str
    location: str = ""
    department: str = ""
    url: str = ""
    source_url: str = ""


def _parse_ats(provider: str, data) -> list[Job]:
    jobs: list[Job] = []
    if provider == "greenhouse" and isinstance(data, dict):
        for j in data.get("jobs", []):
            jobs.append(Job(j.get("title", ""), (j.get("location") or {}).get("name", ""),
                            ", ".join(d.get("name", "") for d in j.get("departments", []) if isinstance(d, dict)),
                            j.get("absolute_url", "")))
    elif provider == "lever" and isinstance(data, list):
        for j in data:
            cats = j.get("categories") or {}
            jobs.append(Job(j.get("text", ""), cats.get("location", ""), cats.get("team", ""), j.get("hostedUrl", "")))
    elif provider == "ashby" and isinstance(data, dict):
        for j in data.get("jobs", []):
            jobs.append(Job(j.get("title", ""), j.get("location", ""), j.get("department", ""), j.get("jobUrl", "")))
    return [j for j in jobs if j.title]


def jobs_from_jsonld(pages: list[Page]) -> list[Job]:
    jobs = []
    for page in pages:
        for obj in page.structured_data:
            if "jobposting" in _types(obj) and isinstance(obj.get("title"), str):
                loc = obj.get("jobLocation")
                jobs.append(Job(obj["title"], _address((loc or {}).get("address") if isinstance(loc, dict) else None) or "",
                                "", obj.get("url") or page.url, page.url))
    return jobs


async def fetch_jobs(fetcher: WebFetcher, pages: list[Page], limit: int = 200) -> tuple[list[Job], list[str]]:
    """Open roles from linked job boards (official public APIs) and JSON-LD; returns (jobs, sources)."""
    jobs: list[Job] = []
    sources: list[str] = []
    for board in find_ats_boards(pages)[:2]:
        data = await fetcher.get_json(board.api_url)
        parsed = _parse_ats(board.provider, data)
        for j in parsed:
            j.source_url = board.board_url
        if parsed:
            sources.append(f"{board.provider}:{board.board_url}")
            jobs.extend(parsed)
    ld = jobs_from_jsonld(pages)
    if ld:
        sources.append("schema.org JobPosting")
        jobs.extend(ld)
    seen: set[str] = set()
    unique = []
    for j in jobs:
        key = j.title.lower().strip() + "|" + j.location.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique[:limit], sources


HIRING_AREAS: list[tuple[str, re.Pattern[str]]] = [
    ("AI / Machine learning", re.compile(r"\b(machine learning|ml|ai|artificial intelligence|llm|nlp|computer vision|"
                                         r"deep learning|genai|generative|data scientist|applied scientist)\b", re.I)),
    ("Data & analytics", re.compile(r"\b(data engineer|analytics|analyst|bi|business intelligence|data platform)\b", re.I)),
    ("Mobile", re.compile(r"\b(ios|android|mobile|react native|flutter|swift|kotlin)\b", re.I)),
    ("Cloud / DevOps / SRE", re.compile(r"\b(devops|sre|site reliability|platform engineer|cloud|infrastructure|"
                                        r"kubernetes)\b", re.I)),
    ("Security", re.compile(r"\b(security|appsec|infosec|compliance|soc ?2|grc)\b", re.I)),
    ("Frontend", re.compile(r"\b(front[- ]?end|ui engineer|react|angular|vue|web engineer)\b", re.I)),
    ("Backend / Platform", re.compile(r"\b(back[- ]?end|api|python|java|golang|go engineer|node|ruby|\.net|"
                                      r"distributed systems)\b", re.I)),
    ("Product & design", re.compile(r"\b(product manager|product designer|ux|ui/ux|design)\b", re.I)),
    ("Sales & customer", re.compile(r"\b(sales|account executive|customer success|support|solutions engineer|"
                                    r"sdr|bdr)\b", re.I)),
]


def hiring_signals(jobs: list[Job]) -> list[dict]:
    out = []
    for area, pat in HIRING_AREAS:
        matched = [j for j in jobs if pat.search(j.title) or pat.search(j.department)]
        if matched:
            out.append({"area": area, "count": len(matched), "examples": [j.title for j in matched[:5]]})
    return sorted(out, key=lambda s: -s["count"])


# --------------------------------------------------------------------------- announcements

NEWS_SECTIONS = ("blog", "news", "press", "newsroom", "changelog", "updates", "releases", "announcements",
                 "whats-new", "what-s-new", "release-notes")
_PRODUCT_WORDS = re.compile(r"\b(launch|launches|launched|introduc|announc|new|release|now available|beta|"
                            r"generally available|ga\b|unveil|v\d)", re.I)
_NAV_TEXT = re.compile(r"^(read more|learn more|more|next|previous|older|newer|all posts|blog|news|press|"
                       r"\d+|page \d+|view all|see all)$", re.I)


@dataclass
class Announcement:
    title: str
    url: str
    source_url: str
    is_product: bool
    date: str | None = None
    section: str = ""
    extra: dict = field(default_factory=dict)


def find_announcements(pages: list[Page], limit: int = 12) -> list[Announcement]:
    found: dict[str, Announcement] = {}
    for page in pages:
        for obj in page.structured_data:  # article pages with dates
            if _types(obj) & {"blogposting", "newsarticle", "article", "pressrelease"} and isinstance(obj.get("headline"), str):
                url = obj.get("url") or page.url
                found.setdefault(url, Announcement(obj["headline"].strip()[:200], url, page.url,
                                                   bool(_PRODUCT_WORDS.search(obj["headline"])),
                                                   str(obj.get("datePublished") or "")[:10] or None, "article"))
        for url, text in page.link_texts:
            path = [p for p in urlparse(url).path.lower().split("/") if p]
            if len(path) < 2 or path[0] not in NEWS_SECTIONS:
                continue  # need a post under a news-like section, not the section index itself
            if len(text) < 12 or _NAV_TEXT.match(text.strip()):
                continue
            found.setdefault(url, Announcement(text[:200], url, page.url, bool(_PRODUCT_WORDS.search(text)),
                                               None, path[0]))
    items = list(found.values())
    items.sort(key=lambda a: (a.date or "", a.is_product), reverse=True)
    return items[:limit]
