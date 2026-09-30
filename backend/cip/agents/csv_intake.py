"""CSV Intelligence Agent: validation, column detection, normalization, de-duplication."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from cip.agents.base import Agent, RunContext
from cip.connectors.source_control.base import parse_repo_url
from cip.core.schemas import (
    AgentResult,
    ClientRecord,
    Finding,
    NormalizedRecord,
    ProjectRecord,
    SourceLinks,
)

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "client_name": ("client name", "client", "company", "company name", "customer", "customer name",
                    "organization", "organisation", "account", "account name"),
    "client_email": ("client email", "email", "contact email", "client contact", "email address"),
    "project_name": ("project name", "project", "product", "product name", "application", "app name"),
    "project_url": ("project url", "url", "website", "site", "app url", "live url", "project link",
                    "website url", "domain"),
    "description": ("description", "project description", "summary", "details", "overview"),
    "industry": ("industry", "sector", "vertical", "domain area"),
    "technology": ("technology", "technologies", "tech stack", "stack", "tech"),
    "repository_url": ("repository url", "repository", "repo", "repo url", "source code", "git url",
                       "github", "gitlab", "github url", "gitlab url"),
    "project_status": ("project status", "status", "state"),
    "start_date": ("start date", "started", "start", "date started", "kickoff date"),
    "existing_features": ("existing features", "features", "feature list", "key features"),
    "notes": ("notes", "comments", "remarks", "additional notes"),
    "linkedin_url": ("linkedin", "linkedin url", "company linkedin"),
}
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com",
    "aol.com", "proton.me", "protonmail.com", "gmx.com", "yandex.com", "mail.com", "zoho.com",
}
SOCIAL_HOSTS = ("twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "tiktok.com",
                "medium.com", "threads.net")
URL_IN_TEXT = re.compile(r"https?://[^\s,;\"'<>)]+|(?:www\.)[^\s,;\"'<>)]+", re.IGNORECASE)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})$")
LIST_SPLIT = re.compile(r"\s*[,;|\n]\s*")
MAX_ROWS = 5_000


@dataclass
class CsvParseResult:
    records: list[NormalizedRecord] = field(default_factory=list)
    column_mapping: dict[str, str] = field(default_factory=dict)
    unmapped_columns: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors and bool(self.records)


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-]+", " ", h.strip().lower().replace("﻿", ""))


def detect_columns(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map canonical field -> original header. Exact alias matches win over fuzzy ones."""
    mapping: dict[str, str] = {}
    normalized = {h: _norm_header(h) for h in headers}
    for field_name, aliases in COLUMN_ALIASES.items():
        for h, n in normalized.items():
            if n in aliases and h not in mapping.values():
                mapping[field_name] = h
                break
    for field_name, aliases in COLUMN_ALIASES.items():
        if field_name in mapping:
            continue
        for h, n in normalized.items():
            if h in mapping.values():
                continue
            if any(re.search(rf"\b{re.escape(a)}\b", n) for a in aliases if len(a) > 3):
                mapping[field_name] = h
                break
    unmapped = [h for h in headers if h not in mapping.values()]
    return mapping, unmapped


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


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [p for p in (x.strip() for x in LIST_SPLIT.split(value)) if p]


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


def _name_from_domain(domain: str) -> str:
    stem = domain.split(".")[0]
    return " ".join(w.capitalize() for w in re.split(r"[-_]", stem))


def normalize_row(row_number: int, row: dict[str, str], mapping: dict[str, str]) -> NormalizedRecord:
    def get(f: str) -> str | None:
        h = mapping.get(f)
        v = (row.get(h) or "").strip() if h else ""
        return v or None

    issues: list[str] = []
    links = SourceLinks()

    project_url_raw = get("project_url")
    project_url = normalize_url(project_url_raw)
    if project_url_raw and not project_url:
        issues.append(f"Invalid project URL: {project_url_raw!r}")

    repo_raw = get("repository_url")
    for candidate in _split_list(repo_raw):
        url = normalize_url(candidate)
        if not url:
            issues.append(f"Invalid repository URL: {candidate!r}")
            continue
        if not parse_repo_url(url):
            issues.append(f"Repository URL is not a recognised GitHub/GitLab URL: {candidate!r}")
            links.websites.append(url)
            continue
        _classify_url(url, links)

    if project_url:
        _classify_url(project_url, links)
    linkedin = normalize_url(get("linkedin_url"))
    if linkedin:
        _classify_url(linkedin, links)
    for f in ("description", "notes", "existing_features"):
        for m in URL_IN_TEXT.findall(get(f) or ""):
            u = normalize_url(m.rstrip(".)"))
            if u:
                _classify_url(u, links)

    email = get("client_email")
    email_domain = None
    if email:
        m = EMAIL_RE.match(email)
        if not m:
            dm = normalize_url(email)
            if dm and "@" not in email:  # a bare domain in the email column
                email_domain = domain_of(dm)
                email = None
            else:
                issues.append(f"Invalid client email: {email!r}")
                email = None
        else:
            email_domain = m.group(1).lower()
            if email_domain in FREE_EMAIL_DOMAINS:
                email_domain = None

    # Client domain: prefer the corporate email domain, else the project website.
    project_domain = domain_of(project_url) if project_url and not parse_repo_url(project_url) else None
    client_domain = email_domain or project_domain

    client_name = get("client_name")
    if not client_name and client_domain:
        client_name = _name_from_domain(client_domain)
        issues.append(f"Client name inferred from domain {client_domain}")
    project_name = get("project_name")
    if not project_name:
        project_name = (f"{client_name} project" if client_name else
                        (_name_from_domain(project_domain) if project_domain else None))
        if project_name:
            issues.append("Project name inferred")
    if not client_name and not project_name:
        client_name = project_name = f"Unknown (row {row_number})"
        issues.append("Row has no client name, project name or usable URL")

    tech = _split_list(get("technology"))
    return NormalizedRecord(
        row_number=row_number,
        client=ClientRecord(name=client_name or project_name or "", domain=client_domain, email=email,
                            industry=get("industry")),
        project=ProjectRecord(
            name=project_name or client_name or "",
            url=project_url if project_url and not parse_repo_url(project_url) else None,
            description=get("description"), technology=tech, status=get("project_status"),
            start_date=get("start_date"), existing_features=_split_list(get("existing_features")),
            notes=get("notes"),
        ),
        sources=links,
        issues=issues,
    )


def _key(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def parse_csv(content: bytes | str) -> CsvParseResult:
    result = CsvParseResult()
    if isinstance(content, bytes):
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    else:
        text = content
    if not text.strip():
        result.errors.append("CSV file is empty")
        return result
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h is not None]
    if not headers:
        result.errors.append("CSV has no header row")
        return result
    mapping, unmapped = detect_columns(headers)
    result.column_mapping, result.unmapped_columns = mapping, unmapped
    if not ({"client_name", "project_name", "project_url", "client_email"} & set(mapping)):
        result.errors.append(
            "Could not detect any identifying column (client name, project name, project URL or client email). "
            f"Found columns: {headers}"
        )
        return result
    for f in ("client_name", "project_name", "project_url", "description"):
        if f not in mapping:
            result.warnings.append(f"Column for '{f}' not found; it will be inferred where possible")

    seen_projects: dict[tuple[str, str], int] = {}
    seen_urls: dict[str, int] = {}
    client_rows: dict[str, list[int]] = {}
    for i, row in enumerate(reader, start=2):  # header is line 1
        if i - 1 > MAX_ROWS:
            result.warnings.append(f"Only the first {MAX_ROWS} rows were processed")
            break
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue
        rec = normalize_row(i, row, mapping)
        ckey = _key(rec.client.domain) or _key(rec.client.name)
        client_rows.setdefault(ckey, []).append(i)
        pkey = (ckey, _key(rec.project.name))
        if pkey in seen_projects:
            rec.duplicate_of_row = seen_projects[pkey]
            rec.issues.append(f"Duplicate project of row {seen_projects[pkey]}")
        elif rec.project.url and rec.project.url.lower() in seen_urls:
            rec.duplicate_of_row = seen_urls[rec.project.url.lower()]
            rec.issues.append(f"Same project URL as row {rec.duplicate_of_row}")
        else:
            seen_projects[pkey] = i
            if rec.project.url:
                seen_urls[rec.project.url.lower()] = i
        result.records.append(rec)
    for rows in client_rows.values():
        if len(rows) > 1:
            result.warnings.append(f"Rows {rows} refer to the same client")
    if not result.records:
        result.errors.append("CSV contains no data rows")
    return result


class CsvIntakeAgent(Agent):
    """Records the normalized CSV row as evidence so later claims can cite it."""

    name = "csv_intake"
    description = "Validate and normalize the uploaded project record"
    max_attempts = 1

    async def run(self, ctx: RunContext) -> AgentResult:
        rec = ctx.record
        source = f"csv://row/{rec.row_number}"
        evidence = []
        findings = []
        ev = ctx.ledger.add(f"Client of record: {rec.client.name}", source, "csv", 1.0,
                            extracted_text=f"client={rec.client.name}; project={rec.project.name}")
        evidence.append(ev)
        if rec.project.description:
            evidence.append(ctx.ledger.add("Project description (from client records)", source, "csv", 0.9,
                                           extracted_text=rec.project.description))
        for feat in rec.project.existing_features:
            evidence.append(ctx.ledger.add(f"Existing feature listed in client records: {feat}", source, "csv",
                                           0.7, extracted_text=feat))
        for issue in rec.issues:
            findings.append(Finding(category="data_quality", title=issue, confidence=1.0))
        return AgentResult(
            findings=findings,
            evidence=evidence,
            confidence=1.0,
            data={"record": rec.model_dump()},
            next_actions=["client_research", "repository"],
        )
