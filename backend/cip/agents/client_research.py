"""Client / Website Research Agent (includes contact & social discovery)."""

from __future__ import annotations

import logging
import re
from typing import get_args
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from cip.agents.base import Agent, ApprovalRequest, RunContext
from cip.connectors.research.company import (
    CompositeCompanyResearch,
    LinkedInCompanyProvider,
    SearchCompanyProvider,
    WebsiteCompanyProvider,
)
from cip.connectors.research.search import NullSearchProvider
from cip.connectors.research.web import Page, registrable_domain
from cip.core.evidence import snippet
from cip.core.grounding import Grounder, SourceDoc, SourcedValue, pages_to_prompt
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.schemas import AgentResult, CompanyProfile, Contact, Finding, ProductDiscovery

log = logging.getLogger(__name__)

PAGE_TEXT_LIMIT = 8_000
VALID_KINDS = set(get_args(ProductDiscovery.model_fields["kind"].annotation))
ROLE_EMAIL_LOCALPARTS = {
    "info", "contact", "hello", "sales", "support", "help", "press", "media", "partners", "partnerships",
    "careers", "jobs", "hr", "billing", "accounts", "office", "enquiries", "inquiries", "team", "admin",
    "marketing", "security", "privacy", "legal", "service", "customerservice", "customercare", "care",
}
SOCIAL_TYPES = {
    "linkedin.com": "linkedin", "twitter.com": "twitter", "x.com": "twitter", "facebook.com": "facebook",
    "instagram.com": "instagram", "youtube.com": "youtube", "github.com": "github", "gitlab.com": "gitlab",
    "apps.apple.com": "app_store", "itunes.apple.com": "app_store", "play.google.com": "google_play",
}


class _LLMProduct(BaseModel):
    name: str
    kind: str = "product"
    description: str = ""
    source_url: str
    quote: str = ""


class _LLMCompany(BaseModel):
    description: SourcedValue | None = None
    industry: SourcedValue | None = None
    headquarters: SourcedValue | None = None
    founded_year: SourcedValue | None = None
    company_size: SourcedValue | None = None
    business_model: SourcedValue | None = None
    locations: list[SourcedValue] = Field(default_factory=list)
    target_customers: list[SourcedValue] = Field(default_factory=list)
    products: list[_LLMProduct] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a meticulous company research analyst. Extract ONLY facts that are explicitly
stated in the provided website sources. For every value give the exact SOURCE url it came from and a short
verbatim quote (copied exactly from that source) that supports it. If a fact is not stated, omit it.
Products/services must be offerings of this company (not partners' or customers' products).
Allowed product kinds: product, service, platform, mobile_app, saas, api, marketplace, other."""


def page_doc(p: Page) -> SourceDoc:
    header = " | ".join(x for x in (p.title, p.description) if x)
    return SourceDoc(p.url, f"{header}\n{' / '.join(p.headings[:25])}\n{p.text}"[: PAGE_TEXT_LIMIT * 2], "website")


def page_summary(p: Page) -> dict:
    return {"url": p.url, "title": p.title, "description": p.description, "headings": p.headings[:40],
            "text": p.text[:PAGE_TEXT_LIMIT], "scripts": p.scripts[:40], "generator": p.generator}


def discover_contacts(pages: list[Page], company_domain: str | None) -> tuple[list[Contact], int]:
    contacts: dict[tuple[str, str], Contact] = {}
    ignored_personal = 0

    def add(c: Contact) -> None:
        key = (c.type, c.value.lower().rstrip("/"))
        if key not in contacts or contacts[key].confidence < c.confidence:
            contacts[key] = c

    for p in pages:
        path = urlparse(p.url).path.lower()
        if "contact" in path:
            add(Contact(type="contact_page", value=p.url, source=p.url, confidence=0.95))
        for email in p.emails:
            local, _, dom = email.partition("@")
            if local.split("+")[0] not in ROLE_EMAIL_LOCALPARTS:
                ignored_personal += 1  # avoid collecting personal data
                continue
            same = company_domain and registrable_domain(dom) == company_domain
            add(Contact(type="email", value=email, source=p.url, confidence=0.95 if same else 0.6))
        if "contact" in path or p.url.rstrip("/") == pages[0].url.rstrip("/"):
            for phone in p.phones[:3]:
                add(Contact(type="phone", value=phone, source=p.url, confidence=0.7))
        for link in p.external_links:
            host = registrable_domain(link)
            ctype = next((t for h, t in SOCIAL_TYPES.items() if host == h or host.endswith("." + h)), None)
            if ctype:
                if ctype == "linkedin" and "/in/" in link:
                    continue  # personal profile
                add(Contact(type=ctype, value=link, source=p.url, confidence=0.85))
            elif host.startswith(("docs.", "developer.", "developers.", "api.")):
                add(Contact(type="docs" if host.startswith("docs.") else "developer_portal",
                            value=link, source=p.url, confidence=0.8))
            elif host.startswith(("community.", "forum.")):
                add(Contact(type="community", value=link, source=p.url, confidence=0.75))
        for link in p.links:
            lp = urlparse(link).path.lower()
            if re.search(r"^/(docs|documentation|developers?|api)(/|$)", lp):
                add(Contact(type="docs", value=link, source=p.url, confidence=0.75))
    return list(contacts.values()), ignored_personal


class ClientResearchAgent(Agent):
    name = "client_research"
    description = "Research the client company and its public website"
    after = ("csv_intake",)

    def approval_needed(self, ctx: RunContext) -> ApprovalRequest | None:
        if "external_research" in ctx.approvals:
            return None
        rec = ctx.record
        targets = [u for u in [rec.project.url, rec.client.domain] if u]
        return ApprovalRequest(
            gate="external_research",
            title="Start external research",
            what="Public web pages of the client (and later, competitor websites and web search results).",
            why="To identify the client's products, services and contacts and to discover comparable products.",
            target=", ".join(targets) or rec.client.name,
            data_analyzed="Publicly available website content only; role-based contact details, no personal data.",
        )

    async def run(self, ctx: RunContext) -> AgentResult:
        rec = ctx.record
        domain = rec.client.domain
        providers = [WebsiteCompanyProvider(ctx.fetcher)]
        if not isinstance(ctx.search, NullSearchProvider):
            providers.append(SearchCompanyProvider(ctx.search))
        providers.append(LinkedInCompanyProvider())  # no-op unless a licensed adapter is configured

        if not domain and not isinstance(ctx.search, NullSearchProvider):
            found = await SearchCompanyProvider(ctx.search).search_company(rec.client.name)
            domain = found.domain if found else None

        composite = CompositeCompanyResearch(providers)
        companies, product_candidates, people, errors = await composite.research(rec.client.name, domain)
        pages: list[Page] = [p for c in companies for p in c.pages]

        # The project may live on its own domain (e.g. app.product.io) — crawl it too.
        project_pages: list[Page] = []
        if rec.project.url and registrable_domain(rec.project.url) != (domain or ""):
            project_pages = await ctx.fetcher.crawl(rec.project.url, max_pages=8)

        all_pages = pages + project_pages
        if not all_pages and not companies:
            return AgentResult(
                status="completed", confidence=0.1, errors=errors,
                findings=[Finding(category="research", title="No public website could be retrieved",
                                  detail="Client domain unknown or unreachable; downstream analysis will rely "
                                         "on CSV and repository data.", confidence=0.9)],
                data={"profile": CompanyProfile(name=rec.client.name, domain=domain).model_dump(),
                      "pages": [], "project_pages": []},
            )

        ledger = ctx.ledger
        profile = CompanyProfile(name=rec.client.name, domain=domain or (companies[0].domain if companies else None))
        findings: list[Finding] = []
        evidence_ids: list[str] = []

        home = all_pages[0] if all_pages else None
        if home and home.description:
            ev = ledger.add(f"{rec.client.name} describes itself: {home.description[:200]}", home.url, "website",
                            0.9, extracted_text=home.description)
            profile.description = home.description
            profile.evidence_ids.append(ev.id)

        # Products from providers (website structure, search) ---------------
        products: dict[str, ProductDiscovery] = {}
        for pc in product_candidates:
            ev = ledger.add(f"{rec.client.name} offers '{pc.name}'", pc.source_url, pc.source_type, pc.confidence,
                            extracted_text=pc.extracted_text)
            products.setdefault(pc.name.lower(), ProductDiscovery(
                name=pc.name, description=pc.description[:300], evidence_ids=[ev.id], source_url=pc.source_url,
                source_type=pc.source_type, confidence=pc.confidence))

        # Leadership (business-relevant public roles only) -------------------
        leadership = []
        for person in people:
            ev = ledger.add(f"{person.name} — {person.title} at {rec.client.name}", person.source_url,
                            person.source_type, 0.6, extracted_text=f"{person.name}, {person.title}")
            leadership.append({"name": person.name, "title": person.title, "evidence_ids": [ev.id]})

        # Contacts & social --------------------------------------------------
        contacts, ignored = discover_contacts(all_pages, profile.domain)
        for c in contacts:
            ev = ledger.add(f"{c.type.replace('_', ' ').title()}: {c.value}", c.source, "website", c.confidence,
                            extracted_text=c.value)
            evidence_ids.append(ev.id)
        profile.contacts = contacts
        if ignored:
            findings.append(Finding(category="privacy", title=f"Ignored {ignored} personal email address(es)",
                                    detail="Only role-based business contacts are retained.", confidence=1.0))

        # LLM structured extraction, grounded against the crawled pages -------
        docs = [page_doc(p) for p in all_pages]
        grounder = Grounder(ledger, docs)
        try:
            extracted = await ctx.llm.complete_json(
                SYSTEM_PROMPT,
                f"Company: {rec.client.name}\nDomain: {profile.domain}\n"
                f"Known project: {rec.project.name} — {rec.project.description or ''}\n\n"
                f"{pages_to_prompt(docs, 30_000)}",
                _LLMCompany,
            )
            for label, attr in (("Description", "description"), ("Industry", "industry"),
                                ("Headquarters", "headquarters"), ("Company size", "company_size"),
                                ("Business model", "business_model")):
                sv = getattr(extracted, attr)
                ev = grounder.ground_value(f"{rec.client.name} {label.lower()}", sv)
                if ev:
                    setattr(profile, attr, sv.value)
                    profile.evidence_ids.append(ev.id)
            if extracted.founded_year:
                ev = grounder.ground_value(f"{rec.client.name} founded", extracted.founded_year)
                m = re.search(r"(18|19|20)\d{2}", extracted.founded_year.value)
                if ev and m:
                    profile.founded_year = int(m.group(0))
                    profile.evidence_ids.append(ev.id)
            for sv in extracted.locations:
                ev = grounder.ground_value(f"{rec.client.name} location", sv, 0.8)
                if ev:
                    profile.locations.append(sv.value)
                    profile.evidence_ids.append(ev.id)
            for sv in extracted.target_customers:
                ev = grounder.ground_value(f"{rec.client.name} targets", sv, 0.75)
                if ev:
                    profile.target_customers.append(sv.value)
            for lp in extracted.products:
                ev = grounder.ground(f"{rec.client.name} offers '{lp.name}'", lp.source_url, lp.quote, 0.85)
                if not ev:
                    continue
                kind = lp.kind if lp.kind in VALID_KINDS else "other"
                key = lp.name.lower()
                if key in products:
                    products[key].evidence_ids.append(ev.id)
                    products[key].confidence = max(products[key].confidence, ev.confidence)
                else:
                    products[key] = ProductDiscovery(name=lp.name, kind=kind, description=lp.description[:300],
                                                     evidence_ids=[ev.id], source_url=ev.source_url,
                                                     confidence=ev.confidence)
        except LLMUnavailable:
            findings.append(Finding(category="research", title="LLM not configured — deterministic extraction only",
                                    confidence=1.0))
        except LLMError as exc:
            errors.append(f"LLM extraction failed: {exc}")

        if not profile.industry and rec.client.industry:
            profile.industry = rec.client.industry
        profile.products = list(products.values())
        for p in profile.products:
            findings.append(Finding(category="product", title=f"{p.kind.replace('_', ' ')}: {p.name}",
                                    detail=p.description, evidence_ids=p.evidence_ids, confidence=p.confidence))

        linkedin = next((c.value for c in contacts if c.type == "linkedin"), None) or \
            next((c.linkedin_url for c in companies if c.linkedin_url), None)
        return AgentResult(
            findings=findings,
            evidence=[ledger.get(i) for i in {*profile.evidence_ids, *evidence_ids,
                                               *(e for p in profile.products for e in p.evidence_ids),
                                               *(e for le in leadership for e in le["evidence_ids"])}
                      if ledger.get(i)],
            confidence=0.8 if pages else 0.5,
            errors=errors,
            data={
                "profile": profile.model_dump(),
                "leadership": leadership,
                "linkedin_url": linkedin,
                "pages": [page_summary(p) for p in pages],
                "project_pages": [page_summary(p) for p in project_pages],
                "home_snippet": snippet(home.text if home else None),
            },
        )
