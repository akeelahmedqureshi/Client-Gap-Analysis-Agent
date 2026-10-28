"""Deep industry and market research from pages that were actually read (not search snippets).

1. Targeted searches built from the client's verified profile (industry, market segment, customer segments,
   geography): industry overview, trends, customer needs and buying criteria, AI and automation adoption,
   regulation, market figures, opportunities and risks.
2. The best results are **read** (official statistics, research bodies and publications first; directories,
   social networks, video sites and the client's own site never). A search snippet alone is not a source.
3. Grounded extraction into sections A-E: every statement needs a verbatim quote found in the page it cites.
   **Figures** (market size, growth, share…) additionally need the number inside the quote, and record the
   metric, period, geography, definition and the original source the page cites; a figure without them is
   still shown, with "not stated" — never filled in.
4. When no reliable figure is found, the assessment is qualitative; the analyst's synthesis is labelled
   ``estimate`` and never mixed with sourced statements.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from cip.connectors.research.web import registrable_domain
from cip.core.evidence import EvidenceLedger
from cip.core.grounding import Grounder, SourceDoc, pages_to_prompt
from cip.core.llm import LLMClient, LLMError, LLMUnavailable

SECTIONS: dict[str, list[tuple[str, str]]] = {
    "A": [("industry_definition", "Industry definition and market segment"), ("business_models", "Common business models"),
          ("customer_segments", "Key customer segments"), ("use_cases", "Major use cases"),
          ("value_chain", "Industry structure and value chain")],
    "B": [("trends", "Emerging industry trends"), ("customer_expectations", "Changing customer expectations"),
          ("technology_trends", "Product and technology trends"), ("ai_automation", "AI and automation adoption"),
          ("regulation", "Regulatory and compliance considerations"), ("emerging_models", "Emerging business models")],
    "C": [("customer_needs", "Common customer problems and needs"), ("buying_motivations", "Buying motivations"),
          ("selection_criteria", "Product selection criteria")],
    "D": [("market_participants", "Major market participants named in sources"),
          ("differentiation", "How products differentiate"), ("underserved_segments", "Potentially underserved segments")],
    "E": [("opportunities", "Growth opportunities"), ("product_extensions", "Potential product extensions"),
          ("threats", "Competitive and technology risks"), ("barriers", "Barriers to adoption")],
}
SECTION_TITLES = {"A": "Industry overview", "B": "Market trends", "C": "Customers and demand",
                  "D": "Competitive landscape", "E": "Opportunities and risks"}
FIELDS = [k for items in SECTIONS.values() for k, _ in items]
SKIP_HOSTS = ("g2.com", "capterra.com", "getapp.com", "softwareadvice.com", "trustradius.com", "producthunt.com",
              "alternativeto.net", "saasworthy.com", "linkedin.com", "facebook.com", "instagram.com", "twitter.com",
              "x.com", "youtube.com", "tiktok.com", "reddit.com", "quora.com", "pinterest.com", "medium.com")
PREFERRED = (".gov", ".edu", ".int", ".europa.eu", "oecd.org", "worldbank.org", "statista.com", "gartner.com",
             "forrester.com", "idc.com", "mckinsey.com", "deloitte.com", "pwc.com", "kpmg.com", "ey.com", "bcg.com",
             "accenture.com", "grandviewresearch.com", "marketsandmarkets.com", "fortunebusinessinsights.com",
             "precedenceresearch.com", "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "forbes.com", "hbr.org",
             "techcrunch.com", "nature.com", "sciencedirect.com", "pubmed.ncbi.nlm.nih.gov", "who.int")


class _Statement(BaseModel):
    statement: str
    source_url: str = ""
    quote: str = ""


class _Figure(BaseModel):
    metric: str                   # e.g. "Global market size"
    value: str                    # as stated, e.g. "USD 4.2 billion" or "11.3% CAGR"
    period: str = ""              # e.g. "2024", "2024-2030"
    geography: str = ""           # e.g. "Global", "United States"
    definition: str = ""          # what the figure covers, if stated
    original_source: str = ""     # the report or body the page attributes the figure to, if any
    source_url: str = ""
    quote: str = ""


class _LLMMarket(BaseModel):
    figures: list[_Figure] = Field(default_factory=list)
    qualitative_assessment: str = ""
    industry_definition: list[_Statement] = Field(default_factory=list)
    business_models: list[_Statement] = Field(default_factory=list)
    customer_segments: list[_Statement] = Field(default_factory=list)
    use_cases: list[_Statement] = Field(default_factory=list)
    value_chain: list[_Statement] = Field(default_factory=list)
    trends: list[_Statement] = Field(default_factory=list)
    customer_expectations: list[_Statement] = Field(default_factory=list)
    technology_trends: list[_Statement] = Field(default_factory=list)
    ai_automation: list[_Statement] = Field(default_factory=list)
    regulation: list[_Statement] = Field(default_factory=list)
    emerging_models: list[_Statement] = Field(default_factory=list)
    customer_needs: list[_Statement] = Field(default_factory=list)
    buying_motivations: list[_Statement] = Field(default_factory=list)
    selection_criteria: list[_Statement] = Field(default_factory=list)
    market_participants: list[_Statement] = Field(default_factory=list)
    differentiation: list[_Statement] = Field(default_factory=list)
    underserved_segments: list[_Statement] = Field(default_factory=list)
    opportunities: list[_Statement] = Field(default_factory=list)
    product_extensions: list[_Statement] = Field(default_factory=list)
    threats: list[_Statement] = Field(default_factory=list)
    barriers: list[_Statement] = Field(default_factory=list)


PROMPT = """You are an industry analyst. From the source pages (industry reports, statistics, research,
publications), describe the market the client competes in. Fill the lists — industry_definition,
business_models, customer_segments, use_cases, value_chain, trends, customer_expectations, technology_trends,
ai_automation, regulation, emerging_models, customer_needs, buying_motivations, selection_criteria,
market_participants, differentiation, underserved_segments, opportunities, product_extensions, threats and
barriers — each item a specific statement with the source_url and a short verbatim quote copied exactly from
that page. List market figures (market size, growth rate, forecast, market share, adoption rate) under
figures: the value exactly as stated, the metric, the period, the geography, what it covers (definition) and
the original source the page attributes it to; the quote must contain the number. Never invent or estimate a
figure; leave a field empty when the page does not state it. Only include statements relevant to the client's
market. Finally, in qualitative_assessment, write 3-5 sentences of your own analysis of the market for this
client based only on these sources (it is shown labelled as analysis). Sources may be in any language: copy
quotes verbatim and write values in English."""


def queries(profile: dict, year: int) -> list[str]:
    segment = (profile.get("market_segment") or profile.get("industry") or profile.get("product") or "")[:100]
    industry = profile.get("industry") or segment
    geo = (profile.get("geography") or [""])[0] if profile.get("geography") else ""
    customers = ", ".join((profile.get("customer_segment") or [])[:2])
    qs = [f"{segment} market size {geo}".strip(), f"{industry} industry trends {year}",
          f"{segment} customer challenges", f"{industry} AI automation adoption",
          f"{segment} regulation compliance {geo}".strip(), f"how {customers or industry} choose {segment} software",
          f"{industry} market report {year}", f"{segment} market opportunities risks"]
    return [q for q in dict.fromkeys(" ".join(q.split()) for q in qs) if q]


def _rank(url: str) -> int:
    host = registrable_domain(url)
    full = (url.split("/")[2] if "://" in url else url).lower()
    return 0 if any(full.endswith(p) or host == p for p in PREFERRED) else 1


def choose_sources(results: list, exclude_domains: set[str], limit: int) -> list:
    seen, out = set(), []
    for r in sorted(results, key=lambda r: _rank(r.url)):
        host = registrable_domain(r.url)
        if not r.url or r.url in seen or host in exclude_domains or any(host == h or host.endswith("." + h)
                                                                        for h in SKIP_HOSTS):
            continue
        seen.add(r.url)
        out.append(r)
    per_host: dict[str, int] = {}
    capped = []
    for r in out:  # at most two pages per site, so one publisher cannot dominate
        h = registrable_domain(r.url)
        if per_host.get(h, 0) < 2:
            per_host[h] = per_host.get(h, 0) + 1
            capped.append(r)
    return capped[:limit]


def _digits_in(value: str, quote: str) -> bool:
    nums = re.findall(r"\d+(?:[.,]\d+)?", value)
    q = quote.replace(",", "")
    return bool(nums) and all(n.replace(",", "") in q for n in nums)


async def research(search, fetcher, llm: LLMClient, ledger: EvidenceLedger, profile: dict, *, client_domain: str | None,
                   max_sources: int = 8) -> dict:
    """Market research for the Market tab. Never raises; ``available`` says whether it produced anything."""
    year = datetime.now(timezone.utc).year
    results, errors = [], []
    for q in queries(profile, year):
        try:
            results += await search.search(q, limit=6)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"market search failed: {exc}")
    chosen = choose_sources(results, {client_domain} if client_domain else set(), max_sources)
    pages = await asyncio.gather(*(fetcher.fetch(r.url) for r in chosen))
    read = [(r, p) for r, p in zip(chosen, pages) if p and len(p.text) > 300]
    unread = [r.url for r, p in zip(chosen, pages) if not (p and len(p.text) > 300)]
    base = {"available": False, "sections": {}, "figures": [], "sources": [], "unread": unread, "errors": errors,
            "queries": queries(profile, year)}
    if not read:
        return {**base, "reason": "No market source page could be read." if chosen else "No usable search result."}
    docs = [SourceDoc(p.url, f"{p.title}\n{p.text[:12_000]}", "publication") for _, p in read]
    sources = [{"url": p.url, "title": p.title or r.title, "accessed_at": (p.fetched_at or datetime.now(timezone.utc)).isoformat()}
               for r, p in read]
    try:
        out = await llm.complete_json(
            PROMPT, f"Client market: {profile}\n\n{pages_to_prompt(docs, 40_000)}", _LLMMarket)
    except LLMUnavailable:
        return {**base, "sources": sources, "reason": "The market analysis needs the AI extraction (not configured)."}
    except LLMError as exc:
        return {**base, "sources": sources, "errors": errors + [f"Market extraction failed: {exc}"],
                "reason": "The market extraction failed."}
    g = Grounder(ledger, docs)
    sections: dict[str, list[dict]] = {}
    dropped = 0
    for field in FIELDS:
        items = []
        for s in getattr(out, field):
            e = g.ground_verified(f"Market — {field.replace('_', ' ')}: {s.statement}", s.source_url, s.quote, 0.75)
            if e and s.statement.strip():
                items.append({"statement": s.statement.strip(), "evidence_id": e.id, "source_url": s.source_url})
            else:
                dropped += 1
        if items:
            sections[field] = items
    figures = []
    for f in out.figures:
        if not (g.is_verified(f.source_url, f.quote) and _digits_in(f.value, f.quote)):
            dropped += 1  # a figure must be stated, with its number, on the page it cites
            continue
        e = g.ground_verified(f"Market figure — {f.metric}: {f.value} ({f.period or 'period not stated'}, "
                              f"{f.geography or 'geography not stated'})", f.source_url, f.quote, 0.8)
        figures.append({"metric": f.metric, "value": f.value, "period": f.period or None,
                        "geography": f.geography or None, "definition": f.definition or None,
                        "original_source": f.original_source or None, "source_url": f.source_url,
                        "evidence_id": e.id if e else None,
                        "complete": bool(f.period and f.geography)})
    return {**base, "available": bool(sections or figures), "sections": sections, "figures": figures,
            "sources": sources, "dropped": dropped,
            "qualitative_assessment": out.qualitative_assessment.strip() or None,
            "reason": None if (sections or figures) else "No statement could be verified against the pages read."}
