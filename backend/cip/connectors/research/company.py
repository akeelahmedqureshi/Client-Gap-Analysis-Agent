"""Company research provider abstraction.

The platform must not depend on any single source (LinkedIn access in
particular changes often), so research goes through ``CompanyResearchProvider``
implementations that are combined by ``CompositeCompanyResearch``:

* ``WebsiteCompanyProvider``  – the company's own website (always available)
* ``SearchCompanyProvider``   – a web-search API (Tavily / Brave)
* ``LinkedInCompanyProvider`` – only via an officially licensed API/connector;
                                disabled unless an adapter is configured.
"""

from __future__ import annotations

import abc
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from cip.connectors.research.search import SearchProvider
from cip.connectors.research.web import Page, WebFetcher, registrable_domain
from cip.core.schemas import SourceType

log = logging.getLogger(__name__)


@dataclass
class CompanyResult:
    name: str
    domain: str | None = None
    website: str | None = None
    description: str | None = None
    linkedin_url: str | None = None
    source_url: str | None = None
    source_type: SourceType = "website"
    pages: list[Page] = field(default_factory=list)


@dataclass
class ProductCandidate:
    name: str
    description: str
    source_url: str
    source_type: SourceType
    extracted_text: str | None = None
    confidence: float = 0.6


@dataclass
class Person:
    name: str
    title: str
    source_url: str
    source_type: SourceType


class CompanyResearchProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    async def search_company(self, company_name: str, domain: str | None = None) -> CompanyResult | None: ...

    @abc.abstractmethod
    async def get_products(self, company: CompanyResult) -> list[ProductCandidate]: ...

    @abc.abstractmethod
    async def get_people(self, company: CompanyResult) -> list[Person]: ...


PRODUCT_PATH_HINTS = ("product", "solution", "platform", "service", "feature", "offering")
LEADERSHIP_TITLES = re.compile(
    r"\b(Chief [A-Z][a-z]+ Officer|CEO|CTO|CFO|COO|CMO|CPO|Founder|Co-Founder|President|"
    r"Managing Director|VP of [A-Z][a-z]+|Head of [A-Z][a-z]+)\b"
)
NAME_BEFORE_TITLE = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z'\-]+){1,2})\s*[,\-–|]?\s*$")


class WebsiteCompanyProvider(CompanyResearchProvider):
    name = "website"

    def __init__(self, fetcher: WebFetcher | None = None, max_pages: int | None = None) -> None:
        self.fetcher = fetcher or WebFetcher()
        self.max_pages = max_pages

    async def search_company(self, company_name: str, domain: str | None = None) -> CompanyResult | None:
        if not domain:
            return None
        start = domain if "://" in domain else f"https://{domain}"
        pages = await self.fetcher.crawl(start, self.max_pages)
        if not pages:
            return None
        home = pages[0]
        linkedin = next((u for p in pages for u in p.external_links if "linkedin.com/company" in u), None)
        return CompanyResult(
            name=company_name, domain=registrable_domain(home.url), website=home.url,
            description=home.description or None, linkedin_url=linkedin,
            source_url=home.url, source_type="website", pages=pages,
        )

    async def get_products(self, company: CompanyResult) -> list[ProductCandidate]:
        out: list[ProductCandidate] = []
        seen: set[str] = set()
        for page in company.pages:
            path = urlparse(page.url).path.lower()
            if not any(h in path for h in PRODUCT_PATH_HINTS):
                continue
            segments = [s for s in path.split("/") if s]
            # A dedicated page such as /products/scheduler is itself a product.
            if len(segments) >= 2:
                name = (page.headings[0] if page.headings else segments[-1].replace("-", " ").title())[:120]
                key = name.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(ProductCandidate(name, page.description or page.text[:200], page.url, "website",
                                                page.description or page.text[:300], 0.7))
        return out

    async def get_people(self, company: CompanyResult) -> list[Person]:
        people: list[Person] = []
        seen: set[str] = set()
        for page in company.pages:
            path = urlparse(page.url).path.lower()
            if not any(h in path for h in ("team", "about", "leadership", "management", "company")):
                continue
            for m in LEADERSHIP_TITLES.finditer(page.text):
                before = page.text[max(0, m.start() - 60) : m.start()]
                nm = NAME_BEFORE_TITLE.search(before)
                if nm and nm.group(1) not in seen:
                    seen.add(nm.group(1))
                    people.append(Person(nm.group(1), m.group(1), page.url, "website"))
        return people[:15]


class SearchCompanyProvider(CompanyResearchProvider):
    name = "search"

    def __init__(self, search: SearchProvider) -> None:
        self.search = search

    async def search_company(self, company_name: str, domain: str | None = None) -> CompanyResult | None:
        results = await self.search.search(f"{company_name} official website", limit=5)
        if not results:
            return None
        linkedin = next((r.url for r in results if "linkedin.com/company" in r.url), None)
        if domain:
            # Known domain: only accept results from it — never adopt another company's site.
            pick = next((r for r in results if registrable_domain(r.url) == registrable_domain(domain)), None)
            if pick is None:
                return None
        else:
            pick = next((r for r in results if "linkedin.com" not in r.url and "wikipedia" not in r.url), results[0])
        return CompanyResult(name=company_name, domain=registrable_domain(pick.url), website=pick.url,
                             description=pick.snippet or None, linkedin_url=linkedin,
                             source_url=pick.url, source_type="search")

    async def get_products(self, company: CompanyResult) -> list[ProductCandidate]:
        results = await self.search.search(f"{company.name} products services platform", limit=8)
        return [ProductCandidate(r.title[:120], r.snippet, r.url, "search", r.snippet, 0.4)
                for r in results if company.domain and registrable_domain(r.url) == company.domain]

    async def get_people(self, company: CompanyResult) -> list[Person]:
        return []


class LinkedInAdapter(Protocol):
    """Implemented by an officially licensed LinkedIn data connector/partner API."""

    async def company(self, name: str, linkedin_url: str | None) -> dict | None: ...


class LinkedInCompanyProvider(CompanyResearchProvider):
    """LinkedIn research through a licensed adapter only — never direct scraping."""

    name = "linkedin"

    def __init__(self, adapter: LinkedInAdapter | None = None) -> None:
        self.adapter = adapter

    async def search_company(self, company_name: str, domain: str | None = None) -> CompanyResult | None:
        if not self.adapter:
            return None
        data = await self.adapter.company(company_name, None)
        if not data:
            return None
        return CompanyResult(name=data.get("name", company_name), description=data.get("description"),
                             linkedin_url=data.get("url"), source_url=data.get("url"), source_type="linkedin")

    async def get_products(self, company: CompanyResult) -> list[ProductCandidate]:
        return []

    async def get_people(self, company: CompanyResult) -> list[Person]:
        return []


class CompositeCompanyResearch:
    """Queries providers in order; failures in one provider never break research."""

    def __init__(self, providers: list[CompanyResearchProvider]) -> None:
        self.providers = providers

    async def research(self, company_name: str, domain: str | None) -> tuple[list[CompanyResult], list[ProductCandidate], list[Person], list[str]]:
        companies: list[CompanyResult] = []
        products: list[ProductCandidate] = []
        people: list[Person] = []
        errors: list[str] = []
        for provider in self.providers:
            try:
                company = await provider.search_company(company_name, domain)
                if not company:
                    continue
                companies.append(company)
                domain = domain or company.domain
                products.extend(await provider.get_products(company))
                people.extend(await provider.get_people(company))
            except Exception as exc:  # noqa: BLE001 - one provider must not break the others
                log.warning("Company provider %s failed: %s", provider.name, exc)
                errors.append(f"{provider.name}: {exc}")
        return companies, products, people, errors
