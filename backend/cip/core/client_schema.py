"""The client data schema: one definition for the CSV upload and the manual client form.

Every field has a label, an input kind, whether it is mandatory, the CSV column names it is recognised by, and
validation rules. ``clean`` validates a row of raw values (from a CSV row or the form) and ``to_record`` turns the
cleaned values into the ``NormalizedRecord`` the analysis runs on. Both input methods therefore accept and reject
exactly the same data.

Mandatory fields are never guessed: a row without a client name or a website stays invalid until someone fills
it in (or skips the row). The only derived value is an optional one, stated in the schema: a project without
a name is named after the client.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from cip.connectors.source_control.base import parse_repo_url
from cip.core.schemas import ClientRecord, NormalizedRecord, ProjectRecord, SourceLinks
from cip.core.urls import urlparse


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str  # text | email | url | urls | list | textarea | date
    group: str  # client | project
    required: bool = False
    aliases: tuple[str, ...] = ()
    help: str = ""
    max_length: int = 300

    def to_dict(self) -> dict:
        return asdict(self)


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("client_name", "Client name", "text", "client", required=True,
              aliases=("client name", "client", "company", "company name", "customer", "customer name",
                       "organization", "organisation", "account", "account name"),
              help="The company's name as you know it."),
    FieldSpec("external_id", "Client ID", "text", "client", max_length=100,
              aliases=("client id", "customer id", "account id", "crm id", "external id", "client number",
                       "customer number", "client code"),
              help="Your own identifier for the client (CRM or accounting id). Used to recognise the client."),
    FieldSpec("client_email", "Client email", "email", "client", max_length=320,
              aliases=("client email", "email", "contact email", "client contact", "email address"),
              help="A business contact. A company email domain also identifies the client."),
    FieldSpec("industry", "Industry", "text", "client", max_length=200,
              aliases=("industry", "sector", "vertical", "domain area")),
    FieldSpec("project_url", "Website URL", "url", "project", required=True, max_length=1000,
              aliases=("project url", "url", "website", "site", "app url", "live url", "project link",
                       "website url", "domain"),
              help="The product or company website the analysis researches (https://…)."),
    FieldSpec("project_name", "Project name", "text", "project",
              aliases=("project name", "project", "product", "product name", "application", "app name"),
              help="When empty, the project is named after the client."),
    FieldSpec("description", "Description", "textarea", "project", max_length=5000,
              aliases=("description", "project description", "summary", "details", "overview")),
    FieldSpec("technology", "Technology", "list", "project", max_length=1000,
              aliases=("technology", "technologies", "tech stack", "stack", "tech"),
              help="Separate items with ; or ,"),
    FieldSpec("repository_url", "Repository URL", "urls", "project", max_length=2000,
              aliases=("repository url", "repository", "repo", "repo url", "source code", "git url", "github",
                       "gitlab", "github url", "gitlab url"),
              help="GitHub or GitLab repositories; separate several with ;"),
    FieldSpec("project_status", "Project status", "text", "project", max_length=100,
              aliases=("project status", "status", "state")),
    FieldSpec("start_date", "Start date", "date", "project", max_length=10,
              aliases=("start date", "started", "start", "date started", "kickoff date"),
              help="YYYY-MM-DD (or YYYY-MM, YYYY)."),
    FieldSpec("existing_features", "Existing features", "list", "project", max_length=5000,
              aliases=("existing features", "features", "feature list", "key features"),
              help="Separate items with ; or ,"),
    FieldSpec("linkedin_url", "LinkedIn URL", "url", "client", max_length=1000,
              aliases=("linkedin", "linkedin url", "company linkedin")),
    FieldSpec("notes", "Notes", "textarea", "project", max_length=5000,
              aliases=("notes", "comments", "remarks", "additional notes")),
)
BY_KEY = {f.key: f for f in FIELDS}
REQUIRED = tuple(f.key for f in FIELDS if f.required)
CLIENT_FIELDS = ("client_name", "external_id", "industry")  # stored on the client itself

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com",
    "aol.com", "proton.me", "protonmail.com", "gmx.com", "yandex.com", "mail.com", "zoho.com",
}
SOCIAL_HOSTS = ("twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "tiktok.com",
                "medium.com", "threads.net")
URL_IN_TEXT = re.compile(r"https?://[^\s,;\"'<>)]+|(?:www\.)[^\s,;\"'<>)]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})$")
LIST_SPLIT = re.compile(r"\s*[,;|\n]\s*")
DATE_RE = re.compile(r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$")
LEGAL_SUFFIXES = {"inc", "incorporated", "ltd", "limited", "llc", "llp", "lp", "pvt", "private", "plc", "gmbh",
                  "corp", "corporation", "co", "company", "sa", "ag", "bv", "nv", "srl", "sarl", "spa", "pty",
                  "oy", "ab", "as", "kg", "the"}


# --------------------------------------------------------------------------- value helpers

def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().strip("<>")
    if not v or " " in v:
        return None
    if not re.match(r"^[a-z]+://", v, re.I):
        v = "https://" + v
    p = urlparse(v)
    if p.scheme not in ("http", "https") or not p.hostname or "." not in p.hostname:
        return None
    return v.rstrip("/")


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or None


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [p for p in (x.strip() for x in LIST_SPLIT.split(value)) if p]


def name_key(name: str | None) -> str:
    """A client name without case, punctuation or legal-form words: 'ABC Technologies Pvt. Ltd.' -> 'abc technologies'."""
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    core = [w for w in words if w not in LEGAL_SUFFIXES]
    return " ".join(core or words)


def site_key(url: str | None) -> str:
    """A website address without scheme, 'www.', case or trailing slash."""
    u = normalize_url(url)
    if not u:
        return ""
    p = urlparse(u)
    host = (p.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host + (p.path or "").rstrip("/").lower()


def client_key(domain: str | None, name: str | None) -> str:
    """The stored de-duplication key of a client (``Client.key``): its domain, else its name."""
    return re.sub(r"[^a-z0-9.]", "", (domain or name or "").lower())


def email_domain(email: str | None) -> str | None:
    m = EMAIL_RE.match(email or "")
    if not m:
        return None
    d = m.group(1).lower()
    return None if d in FREE_EMAIL_DOMAINS else d


def client_domain(values: dict[str, str]) -> str | None:
    """The client's domain: its corporate email domain, else its website's."""
    url = normalize_url(values.get("project_url"))
    site = domain_of(url) if url and not parse_repo_url(url) else None
    return email_domain(values.get("client_email")) or site


# --------------------------------------------------------------------------- validation

def clean(raw: dict[str, str | None]) -> tuple[dict[str, str], dict[str, str]]:
    """Validate one row. Returns (values, errors by field): values are trimmed and normalized (URLs with a
    scheme, lists joined with '; '); a field with an error keeps what was entered so it can be corrected."""
    values: dict[str, str] = {}
    errors: dict[str, str] = {}
    for f in FIELDS:
        v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(raw.get(f.key) or "")).strip()
        if not v:
            if f.required:
                errors[f.key] = f"{f.label} is required."
            continue
        if len(v) > f.max_length:
            errors[f.key] = f"{f.label} is longer than {f.max_length} characters."
            values[f.key] = v
            continue
        if f.kind == "email":
            if not EMAIL_RE.match(v):
                errors[f.key] = f"“{v[:80]}” is not an email address."
        elif f.kind == "url":
            url = normalize_url(v)
            if not url:
                errors[f.key] = f"“{v[:80]}” is not a valid web address."
            elif f.key == "project_url" and parse_repo_url(url):
                errors[f.key] = "This is a code repository; put it under Repository URL and give the website here."
            elif f.key == "linkedin_url" and "linkedin.com" not in (domain_of(url) or ""):
                errors[f.key] = "Not a LinkedIn address."
            else:
                v = url
        elif f.kind == "urls":
            bad, urls = [], []
            for item in split_list(v):
                url = normalize_url(item)
                if not url or not parse_repo_url(url):
                    bad.append(item[:80])
                else:
                    urls.append(url)
            if bad:
                errors[f.key] = "Not a GitHub or GitLab repository: " + ", ".join(f"“{b}”" for b in bad)
            else:
                v = "; ".join(urls)
        elif f.kind == "list":
            v = "; ".join(split_list(v))
        elif f.kind == "date":
            if not DATE_RE.match(v):
                errors[f.key] = f"“{v[:40]}” is not a date; use YYYY-MM-DD, YYYY-MM or YYYY."
        values[f.key] = v
    return values, errors


def _classify_url(url: str, links: SourceLinks) -> None:
    host = domain_of(url) or ""
    ref = parse_repo_url(url)
    if ref and ref.provider == "github":
        if ref.url not in links.github:
            links.github.append(ref.url)
    elif ref and ref.provider == "gitlab":
        if ref.url not in links.gitlab:
            links.gitlab.append(ref.url)
    elif "linkedin.com" in host:
        if url not in links.linkedin:
            links.linkedin.append(url)
    elif any(host == s or host.endswith("." + s) for s in SOCIAL_HOSTS):
        if url not in links.social:
            links.social.append(url)
    elif url not in links.websites:
        links.websites.append(url)


def to_record(row_number: int, values: dict[str, str], errors: dict[str, str] | None = None) -> NormalizedRecord:
    """The analysis record of a cleaned row. Fields with errors are left out (and listed as issues)."""
    errors = errors or {}
    ok = {k: v for k, v in values.items() if k not in errors}
    links = SourceLinks()
    website = ok.get("project_url")
    for repo in split_list(ok.get("repository_url")):
        _classify_url(repo, links)
    if website:
        _classify_url(website, links)
    if ok.get("linkedin_url"):
        _classify_url(ok["linkedin_url"], links)
    for f in ("description", "notes", "existing_features"):
        for m in URL_IN_TEXT.findall(ok.get(f) or ""):
            u = normalize_url(m.rstrip(".)"))
            if u:
                _classify_url(u, links)
    name = ok.get("client_name") or ""
    return NormalizedRecord(
        row_number=row_number,
        client=ClientRecord(name=name, domain=client_domain(ok), email=ok.get("client_email"),
                            industry=ok.get("industry"), external_id=ok.get("external_id")),
        project=ProjectRecord(
            name=ok.get("project_name") or name, url=website, description=ok.get("description"),
            technology=split_list(ok.get("technology")), status=ok.get("project_status"),
            start_date=ok.get("start_date"), existing_features=split_list(ok.get("existing_features")),
            notes=ok.get("notes"),
        ),
        sources=links,
        issues=list(errors.values()),
    )


def record_values(record: dict | None, client=None) -> dict[str, str]:
    """The schema values of a stored record (``Project.record``), with the client's own fields on top."""
    rec = record or {}
    c, p, s = rec.get("client") or {}, rec.get("project") or {}, rec.get("sources") or {}
    out = {
        "client_name": c.get("name"), "external_id": c.get("external_id"), "client_email": c.get("email"),
        "industry": c.get("industry"), "project_url": p.get("url"), "project_name": p.get("name"),
        "description": p.get("description"), "technology": "; ".join(p.get("technology") or []),
        "repository_url": "; ".join((s.get("github") or []) + (s.get("gitlab") or [])),
        "project_status": p.get("status"), "start_date": p.get("start_date"),
        "existing_features": "; ".join(p.get("existing_features") or []),
        "linkedin_url": (s.get("linkedin") or [None])[0], "notes": p.get("notes"),
    }
    if client is not None:
        out.update(client_name=client.name, industry=client.industry or out["industry"],
                   external_id=getattr(client, "external_id", None) or out["external_id"])
    return {k: str(v) for k, v in out.items() if v}


def same_value(field: str, a: str | None, b: str | None) -> bool:
    """Values equal for comparison: case-insensitive for addresses, list order and spacing ignored."""
    a, b = (a or "").strip(), (b or "").strip()
    kind = BY_KEY[field].kind if field in BY_KEY else "text"
    if kind in ("url", "email"):
        return a.lower().rstrip("/") == b.lower().rstrip("/")
    if kind in ("list", "urls"):
        return sorted(x.lower() for x in split_list(a)) == sorted(x.lower() for x in split_list(b))
    return re.sub(r"\s+", " ", a) == re.sub(r"\s+", " ", b)


def schema() -> list[dict]:
    return [f.to_dict() for f in FIELDS]
