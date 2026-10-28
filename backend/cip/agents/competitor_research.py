"""Competitor Discovery, Ranking and Deep Analysis Agent (BRS 7.5-7.7).

A search result is never treated as a competitor automatically:

1. candidates are gathered from web search shaped by the industry & market profile (and, if configured,
   LLM market knowledge — explicitly marked as a hypothesis), plus open-source alternatives on GitHub;
2. **light verification**: each candidate's own website (1-2 pages) is fetched; unreachable candidates
   stay unverified. Every verified candidate gets a nine-factor relevance score (``core/relevance.py``)
   and is classified (direct / indirect / adjacent / open source / enterprise / emerging);
3. the **Top 10** by relevance form the competitive landscape (``data.landscape``) — ranked for
   relevance to this client, not company size;
4. the **Top 3** get a **deep analysis** (product, features, pricing, docs, help, integrations,
   customers and news pages, LLM profile with grounded quotes). Each deep analysis is retried on its
   own, so one failing competitor never re-runs the others. ``data.competitors`` holds these and feeds
   the comparison matrix, pricing, app-store and UX analyses.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import get_args

from pydantic import BaseModel, Field

from cip.agents.base import Agent, RunContext
from cip.agents.pricing_analysis import record_pricing
from cip.connectors.research.enrichment import find_announcements
from cip.connectors.research.pages import CAPABILITY_KINDS, OFFERING_KINDS, kind_of
from cip.connectors.research.pricing import extract_pricing
from cip.connectors.research.web import registrable_domain
from cip.connectors.source_control.base import parse_repo_url
from cip.core import offering, pricing_detail
from cip.core.evidence import snippet
from cip.core.negation import affirmed
from cip.core.grounding import Grounder, SourceDoc, SourcedValue, pages_to_prompt
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.relevance import (
    DEFAULT_WEIGHTS,
    FACTORS,
    CandidateSignals,
    ClientProfile,
    comparable,
    industry_terms,
    model_signals,
    score_candidate,
    tokens,
)
from cip.core.schemas import (
    AgentResult,
    AgentStatus,
    Basis,
    Competitor,
    CompetitorClass,
    FeatureObservation,
    FeatureStatus,
    Finding,
)

log = logging.getLogger(__name__)
CLASSES = set(get_args(CompetitorClass))
NON_VENDOR_HOSTS = ("g2.com", "capterra.com", "getapp.com", "softwareadvice.com", "producthunt.com",
                    "wikipedia.org", "linkedin.com", "youtube.com", "reddit.com", "medium.com", "forbes.com",
                    "techcrunch.com", "gartner.com", "trustradius.com", "quora.com", "facebook.com", "x.com",
                    "twitter.com", "crunchbase.com", "alternativeto.net", "saasworthy.com", "slashdot.org")


class _LLMCandidate(BaseModel):
    name: str
    url: str | None = None
    classification: str = "direct"
    rationale: str = ""
    source_url: str | None = None


class _LLMCandidates(BaseModel):
    candidates: list[_LLMCandidate] = Field(default_factory=list)


class _LLMCompFeature(BaseModel):
    feature_id: str
    status: str = "available"
    source_url: str
    quote: str = ""


class _LLMCompetitorProfile(BaseModel):
    is_comparable: bool = True
    classification: str = "direct"
    rationale: str = ""
    description: SourcedValue | None = None
    target_market: SourcedValue | None = None
    pricing: SourcedValue | None = None
    features: list[_LLMCompFeature] = Field(default_factory=list)
    # Deep profile sections; every item quoted from the competitor's own pages.
    overview: list[SourcedValue] = Field(default_factory=list)
    products: list[SourcedValue] = Field(default_factory=list)
    target_customers: list[SourcedValue] = Field(default_factory=list)
    industries: list[SourcedValue] = Field(default_factory=list)
    value_proposition: list[SourcedValue] = Field(default_factory=list)
    positioning: list[SourcedValue] = Field(default_factory=list)
    integrations: list[SourcedValue] = Field(default_factory=list)
    ai_capabilities: list[SourcedValue] = Field(default_factory=list)
    automation: list[SourcedValue] = Field(default_factory=list)
    onboarding_support: list[SourcedValue] = Field(default_factory=list)
    differentiators: list[SourcedValue] = Field(default_factory=list)
    strengths: list[SourcedValue] = Field(default_factory=list)
    limitations: list[SourcedValue] = Field(default_factory=list)


PROFILE_SECTIONS = ("overview", "products", "target_customers", "industries", "value_proposition", "positioning",
                    "integrations", "ai_capabilities", "automation", "onboarding_support", "differentiators",
                    "strengths", "limitations")


DISCOVERY_PROMPT = """You are a market research analyst. Identify products that compete with the client's
project. Use the search results provided (cite the result url as source_url) and, where results are thin,
well-known products you are confident exist (leave source_url null for those). Only list real software
products/companies with their official website url — not review sites, articles or directories.
classification must be one of: direct, indirect, adjacent, open_source, enterprise, emerging."""

PROFILE_PROMPT = """You are analysing a potential competitor's website. Decide whether it is genuinely
comparable to the client's project (is_comparable) and classify it (direct, indirect, adjacent, open_source,
enterprise, emerging). Extract description, target market and pricing, and the product features using ONLY
feature ids from the taxonomy. Then build a profile specific to THIS company — never generic statements that
would fit any competitor — with these lists: overview (company and business), products (products and
services offered), target_customers, industries, value_proposition, positioning (how it presents itself in the
market), integrations (named third-party integrations, API), ai_capabilities, automation, onboarding_support
(onboarding, training, support channels and hours), differentiators (what it claims sets it apart),
strengths and limitations (only those the pages show, e.g. documented restrictions, plan limits, missing
platforms). Every value needs the SOURCE url and a short verbatim quote copied exactly from that page; leave a
list empty when the pages do not say it. Sources may be in any language: copy quotes verbatim in the source's
language and write extracted values in English."""


MARKETPLACE_HOSTS = ("g2.com", "capterra.com", "producthunt.com", "getapp.com", "softwareadvice.com",
                     "trustradius.com", "alternativeto.net", "saasworthy.com")
STOPWORDS = {"the", "and", "for", "with", "platform", "software", "solution", "solutions", "system", "app",
             "application", "management", "online", "based", "web", "tool", "tools", "service", "services", "from",
             "that", "your", "their", "this", "into", "using", "our"}
MIN_OSS_STARS = 50


def _queries(ctx: RunContext, profile: dict, industry: dict | None = None) -> list[str]:
    rec = ctx.record
    industry = industry or {}
    desc = (rec.project.description or profile.get("description") or rec.project.name)[:120]
    sector = industry.get("industry") or profile.get("industry") or rec.client.industry or ""
    qs = [f"{desc} software alternatives", f"{rec.project.name} competitors",
          f"best {sector} {desc} platforms".strip(),
          # Review sites & launch directories: used as *sources of names*, never as competitors themselves.
          f"{desc} alternatives site:g2.com", f"{desc} site:capterra.com", f"{desc} site:producthunt.com"]
    segment = industry.get("market_segment")
    customers = ", ".join((industry.get("customer_segment") or [])[:2])
    if segment and segment[:120] != desc:
        qs.append(f"{segment} software")
    if customers:
        qs.append(f"{desc} for {customers}")
    return list(dict.fromkeys(q for q in qs if q.strip()))


def _keywords(text: str, n: int = 3) -> list[str]:
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", text.lower()) if w not in STOPWORDS]
    return list(dict.fromkeys(words))[:n]


def _source_type(url: str | None) -> str:
    host = registrable_domain(url or "")
    return "marketplace" if any(host == m or host.endswith("." + m) for m in MARKETPLACE_HOSTS) else "search"


def _key(url: str) -> str:
    ref = parse_repo_url(url)
    return f"{ref.host}/{ref.full_name}".lower() if ref else registrable_domain(url)


class CompetitorResearchAgent(Agent):
    name = "competitor_research"
    description = "Discover, verify, classify and profile competitors"
    after = ("client_research", "product_features", "industry_market")

    async def run(self, ctx: RunContext) -> AgentResult:
        if "external_research" not in ctx.approvals:
            # Web search, GitHub search and competitor websites are covered by the external-research gate.
            return AgentResult(status=AgentStatus.SKIPPED, confidence=1.0,
                               findings=[Finding(category="market", title="Competitor research skipped",
                                                 detail="External research was not approved for this run.",
                                                 confidence=1.0)],
                               data={"competitors": [], "landscape": [], "rejected": [],
                                     "reason": "external research not approved"})
        rec = ctx.record
        profile = ctx.data("client_research").get("profile", {})
        industry = ctx.data("industry_market")
        client_obs = ctx.data("product_features").get("observations", {})
        client_feats = {fid for fid, o in client_obs.items() if o["status"] in ("available", "partial")}
        client_domain = registrable_domain(profile.get("domain") or rec.client.domain or "") or None
        ledger = ctx.ledger
        errors: list[str] = []
        findings: list[Finding] = []

        # 1. Candidate discovery ---------------------------------------------
        results = []
        for q in _queries(ctx, profile, industry):
            try:
                results += await ctx.search.search(q, limit=8)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"search failed: {exc}")
        search_docs = [SourceDoc(r.url, f"{r.title}\n{r.snippet}", "search") for r in results]

        candidates: dict[str, _LLMCandidate] = {}
        try:
            llm_c = await ctx.llm.complete_json(
                DISCOVERY_PROMPT,
                f"Client: {rec.client.name}\nProject: {rec.project.name}\n"
                f"Description: {rec.project.description or profile.get('description') or ''}\n"
                f"Industry: {industry.get('industry') or profile.get('industry') or rec.client.industry or 'unknown'}\n"
                f"Market segment: {industry.get('market_segment') or 'unknown'}\n"
                f"Customers: {', '.join(industry.get('customer_segment') or []) or 'unknown'}\n"
                f"Client features: {sorted(client_feats)}\n\nSearch results:\n"
                + "\n".join(f"- {r.title} | {r.url} | {r.snippet[:200]}" for r in results[:30]),
                _LLMCandidates,
            )
            for c in llm_c.candidates:
                if c.url:
                    candidates.setdefault(_key(c.url), c)
        except LLMUnavailable:
            # Deterministic fallback: vendor-looking search results only.
            for r in results:
                host = registrable_domain(r.url)
                if not host or any(host == h or host.endswith("." + h) for h in NON_VENDOR_HOSTS):
                    continue
                candidates.setdefault(host, _LLMCandidate(name=r.title.split("|")[0].split("-")[0].strip()[:80],
                                                          url=f"https://{host}", source_url=r.url,
                                                          rationale="search result"))
        except LLMError as exc:
            errors.append(f"LLM competitor discovery failed: {exc}")

        # Open-source alternatives via GitHub's search API (verified through the API, not crawling).
        for key, cand in (await self._github_candidates(ctx, profile, errors)).items():
            candidates.setdefault(key, cand)

        candidates = {h: c for h, c in candidates.items()
                      if h and h != client_domain and not any(h == n or h.endswith("." + n) for n in NON_VENDOR_HOSTS)}
        if not candidates:
            return AgentResult(
                confidence=0.2, errors=errors,
                findings=[Finding(category="market", title="No competitor candidates discovered",
                                  detail="Configure a search provider (CIP_SEARCH_PROVIDER) and/or the LLM to enable "
                                         "competitor discovery.", confidence=1.0)],
                data={"competitors": [], "landscape": [], "rejected": []},
            )

        # 2. Light verification and relevance ranking of every candidate -> Top-N landscape ----
        client = _client_profile(ctx, profile, industry, client_feats)
        items = list(candidates.items())[: ctx.settings.max_competitors * 2]
        light = await asyncio.gather(*(self._light(ctx, host, cand, client, results, search_docs)
                                       for host, cand in items))
        verified = [c for c in light if c.verified]
        rejected = [c.model_dump() for c in light if not c.verified]
        verified.sort(key=lambda c: -(c.relevance or {}).get("overall", 0))
        landscape = verified[: ctx.settings.max_competitors]
        for i, c in enumerate(landscape, 1):
            c.rank = i

        # 3. Deep analysis of the Top-N (default 3); each competitor is retried on its own ------------
        deep: list[Competitor] = []
        queue = list(landscape)
        while queue and len(deep) < ctx.settings.deep_competitors:
            wave, queue = queue[: ctx.settings.deep_competitors - len(deep)], queue[ctx.settings.deep_competitors - len(deep):]
            done = await asyncio.gather(*(self._deep_with_retry(ctx, c, client_feats, errors) for c in wave))
            for c, ok in zip(wave, done):
                if ok is None:  # judged not comparable on closer inspection
                    landscape.remove(c)
                    rejected.append(c.model_dump())
                else:
                    deep.append(ok)
        for i, c in enumerate(landscape, 1):
            c.rank = i
        deep.sort(key=lambda c: c.rank or 99)

        for c in landscape:
            n = sum(1 for f in c.features if f.status == FeatureStatus.AVAILABLE)
            findings.append(Finding(
                category=f"competitor.{c.classification}",
                title=f"#{c.rank} {c.name} ({c.classification}{', deep analysis' if c.deep else ''})",
                detail=f"Relevance {c.relevance['overall']:.0%}. {c.rationale} — {n} comparable features evidenced",
                evidence_ids=c.evidence_ids, confidence=c.confidence))
        return AgentResult(
            findings=findings,
            evidence=[ledger.get(e) for c in landscape for e in
                      [*c.evidence_ids, *(i for f in c.features for i in f.evidence_ids)] if ledger.get(e)],
            confidence=0.75 if deep else 0.3,
            errors=errors,
            data={"competitors": [c.model_dump() for c in deep],
                  "landscape": [_landscape_entry(c) for c in landscape],
                  "rejected": rejected,
                  "ranking": {"weights": DEFAULT_WEIGHTS, "factors": list(FACTORS),
                              "deep_count": ctx.settings.deep_competitors, "landscape_size": ctx.settings.max_competitors,
                              "candidates_considered": len(items)}},
        )

    async def _github_candidates(self, ctx: RunContext, profile: dict, errors: list[str]) -> dict[str, _LLMCandidate]:
        rec = ctx.record
        kws = _keywords(rec.project.description or profile.get("description") or rec.project.name)
        if not kws:
            return {}
        api = ctx.settings.github_api_url.rstrip("/")
        data = None
        for n in (len(kws), 2):  # broaden if the full keyword set finds nothing
            q = "+".join(kws[:n]) + "+in:name,description,topics+archived:false"
            data = await ctx.fetcher.get_json(f"{api}/search/repositories?q={q}&sort=stars&order=desc&per_page=5")
            if isinstance(data, dict) and data.get("items"):
                break
        out: dict[str, _LLMCandidate] = {}
        for item in (data or {}).get("items", []) if isinstance(data, dict) else []:
            if item.get("stargazers_count", 0) < MIN_OSS_STARS or item.get("fork"):
                continue
            url = item.get("html_url", "")
            out[_key(url)] = _LLMCandidate(
                name=item.get("name", url), url=url, classification="open_source",
                rationale=f"Open-source project on GitHub ({item.get('stargazers_count', 0):,} stars): "
                          f"{(item.get('description') or '')[:200]}")
        return out

    async def _profile_github(self, ctx: RunContext, repo, comp: Competitor) -> tuple[Competitor, str]:
        """Verify an open-source alternative through the GitHub API (README + metadata)."""
        api = ctx.settings.github_api_url.rstrip("/")
        meta = await ctx.fetcher.get_json(f"{api}/repos/{repo.full_name}")
        readme = await ctx.fetcher.get_text(f"{api}/repos/{repo.full_name}/readme",
                                            headers={"Accept": "application/vnd.github.raw+json"})
        if not isinstance(meta, dict) or not readme:
            comp.rationale = (comp.rationale + " — repository could not be verified").strip(" —")
            comp.confidence = 0.2
            return comp, ""
        comp.verified = True
        comp.classification = "open_source"
        comp.description = (meta.get("description") or "")[:300]
        comp.stars = int(meta.get("stargazers_count") or 0)
        ev = ctx.ledger.add(f"{comp.name}: open-source repository ({meta.get('stargazers_count', 0):,} stars, "
                            f"last push {str(meta.get('pushed_at') or '')[:10]})", comp.url, "github", 0.9,
                            extracted_text=comp.description or None, repository_path="README")
        comp.evidence_ids.append(ev.id)
        obs: dict[str, FeatureObservation] = {}
        for fid, kws in ctx.taxonomy.match_text(readme).items():
            e = ctx.ledger.add(f"{comp.name} offers {ctx.taxonomy.get(fid).name}", comp.url, "github",
                               0.6 if len(kws) > 1 else 0.5, extracted_text=snippet(readme, kws[0]),
                               repository_path="README")
            obs[fid] = FeatureObservation(feature_id=fid, status=FeatureStatus.AVAILABLE, confidence=e.confidence,
                                          evidence_ids=[e.id], basis=Basis.EVIDENCE)
        comp.features = list(obs.values())
        comp.pages_analysed = [comp.url]
        return comp, f"{comp.description}\n{readme[:20_000]}"

    async def _light(self, ctx: RunContext, host: str, cand: _LLMCandidate, client: ClientProfile,
                     results: list, search_docs: list[SourceDoc]) -> Competitor:
        """Verify a candidate from its own site (1-2 pages) and score its relevance to the client."""
        ledger = ctx.ledger
        comp = Competitor(name=cand.name, url=cand.url or f"https://{host}",
                          classification=cand.classification if cand.classification in CLASSES else "direct",
                          rationale=cand.rationale)
        marketplace = False
        if cand.source_url:
            sd = next((d for d in search_docs if d.url == cand.source_url), None)
            stype = _source_type(cand.source_url)
            marketplace = stype == "marketplace"
            ev = ledger.add(f"{cand.name} surfaced in {'a review/launch directory' if marketplace else 'market search'}",
                            cand.source_url, stype, 0.5, extracted_text=sd.text[:400] if sd else None)
            comp.evidence_ids.append(ev.id)
        name = cand.name.lower()
        marketplace = marketplace or any(_source_type(r.url) == "marketplace" and name and name in
                                         f"{r.title} {r.snippet}".lower() for r in results)
        mentions = sum(1 for r in results if registrable_domain(r.url) == host)

        repo = parse_repo_url(comp.url)
        if repo and repo.provider == "github":
            comp, text = await self._profile_github(ctx, repo, comp)
            has_pricing = has_docs = False
        else:
            pages = await ctx.fetcher.crawl(comp.url, max_pages=ctx.settings.competitor_light_pages,
                                            prefer=["/features", "/product"])
            if not pages:
                comp.rationale = (comp.rationale + " — website could not be verified").strip(" —")
                comp.confidence = 0.2
                return comp
            comp.verified = True
            home = pages[0]
            ev = ledger.add(f"{cand.name} official website", home.url, "website", 0.9,
                            extracted_text=home.description or snippet(home.text))
            comp.evidence_ids.append(ev.id)
            if host.endswith("github.com"):
                comp.classification = "open_source"
            comp.features = list(_keyword_features(ctx, cand.name, pages, {}).values())
            comp.description = home.description or ""
            comp.pages_analysed = [p.url for p in pages]
            text = "\n".join(f"{p.title} {p.description} {' '.join(p.headings)} {p.text}" for p in pages)
            links = " ".join(l.lower() for p in pages for l in p.links)
            has_pricing = "/pricing" in links or "/plans" in links or bool(re.search(r"[$€£]\s?\d", text))
            has_docs = any(k in links for k in ("/docs", "/help", "/support", "/documentation", "/api"))
        if not comp.verified:
            return comp

        result = score_candidate(client, CandidateSignals(
            text=text, features={o.feature_id for o in comp.features if o.status != FeatureStatus.UNKNOWN},
            marketplace=marketplace,
            search_mentions=mentions, oss_stars=comp.stars or 0, has_pricing=has_pricing, has_docs=has_docs,
            evidence_count=len(comp.evidence_ids) + len(comp.features)))
        comp.relevance = result
        if not comparable(result):
            comp.verified = False
            comp.rationale = (f"Rejected as not comparable (feature overlap {result['factors']['feature_overlap']:.0%}, "
                              f"product similarity {result['factors']['product_similarity']:.0%}). {comp.rationale}").strip()
            comp.confidence = 0.3
            return comp
        f = result["factors"]
        comp.rationale = result["reason"] if comp.rationale in ("", "search result") else f"{comp.rationale} {result['reason']}"
        comp.confidence = round(min(0.95, 0.45 + 0.5 * result["overall"]), 3)
        if comp.classification != "open_source":
            different_customers = bool(client.customer_words) and f["customer_similarity"] == 0
            comp.classification = ("adjacent" if different_customers else
                                   "direct" if f["feature_overlap"] >= 0.4 else
                                   "indirect" if f["feature_overlap"] >= 0.2 else "adjacent")
        return comp

    async def _deep_with_retry(self, ctx: RunContext, comp: Competitor, client_feats: set[str],
                               errors: list[str]) -> Competitor | None:
        """Deep-analyse one competitor; a failure retries only this competitor (BRS 11)."""
        for attempt in (1, 2):
            try:
                return await self._deep(ctx, comp, client_feats, errors)
            except Exception as exc:  # noqa: BLE001
                log.warning("Deep analysis of %s failed (attempt %s): %s", comp.name, attempt, exc)
                if attempt == 2:
                    errors.append(f"Deep analysis of {comp.name} failed; using its light profile: {exc}")
                    comp.deep, comp.deep_error = True, str(exc)[:300]
                    return comp
                await asyncio.sleep(0)
        return comp

    async def _deep(self, ctx: RunContext, comp: Competitor, client_feats: set[str],
                    errors: list[str]) -> Competitor | None:
        """Top-3 deep research: product, feature, pricing, docs, help, integrations, customers, news pages."""
        ledger = ctx.ledger
        comp.deep = True
        if comp.classification == "open_source" and parse_repo_url(comp.url or ""):
            return comp  # the README was already analysed through the GitHub API
        pages = await ctx.fetcher.crawl(comp.url, max_pages=ctx.settings.competitor_deep_pages, prefer=DEEP_PATHS)
        if not pages:
            return comp
        obs = {o.feature_id: o for o in comp.features}
        _keyword_features(ctx, comp.name, pages, obs)
        comparable_ = True
        llm_classified = False
        docs = [SourceDoc(p.url, f"{p.title}\n{' / '.join(p.headings)}\n{p.text}", "website") for p in pages]
        grounder = Grounder(ledger, docs)
        try:
            prof = await ctx.llm.complete_json(
                PROFILE_PROMPT,
                f"Client project: {ctx.record.project.name} — {ctx.record.project.description or ''}\n"
                f"Client features: {sorted(client_feats)}\nCandidate: {comp.name}\n\nTaxonomy:\n"
                f"{ctx.taxonomy.describe_for_prompt()}\n\n{pages_to_prompt(docs, 24_000)}",
                _LLMCompetitorProfile,
            )
            comparable_ = prof.is_comparable
            if prof.classification in CLASSES:
                comp.classification = prof.classification
                llm_classified = True
            if prof.rationale:
                comp.rationale = f"{prof.rationale} {comp.relevance['reason'] if comp.relevance else ''}".strip()
            # Only values whose quote is found in the competitor's page are kept (never unverified text).
            for attr in ("description", "target_market", "pricing"):
                sv = getattr(prof, attr)
                e = sv and grounder.ground_verified(f"{comp.name} {attr.replace('_', ' ')}: {sv.value}",
                                                    sv.source_url, sv.quote)
                if e:
                    setattr(comp, attr, sv.value)
                    comp.evidence_ids.append(e.id)
            profile: dict[str, list[dict]] = {}
            for section in PROFILE_SECTIONS:
                items = []
                for sv in getattr(prof, section):
                    e = grounder.ground_verified(f"{comp.name} {section.replace('_', ' ')}: {sv.value}",
                                                 sv.source_url, sv.quote, 0.8)
                    if e and sv.value.strip() and sv.value.strip().lower() not in {i["value"].lower() for i in items}:
                        items.append({"value": sv.value.strip(), "evidence_id": e.id, "source_url": sv.source_url})
                if items:
                    profile[section] = items
            comp.profile = profile
            for f in prof.features:
                if f.feature_id not in ctx.taxonomy:
                    continue
                e = grounder.ground_verified(f"{comp.name} offers {ctx.taxonomy.get(f.feature_id).name}",
                                             f.source_url, f.quote, 0.85)
                if not e:
                    continue
                status = FeatureStatus.PARTIAL if f.status == "partial" else FeatureStatus.AVAILABLE
                o = obs.setdefault(f.feature_id, FeatureObservation(feature_id=f.feature_id, status=status,
                                                                    confidence=e.confidence))
                o.evidence_ids.append(e.id)
                o.confidence = max(o.confidence, e.confidence)
                # Stated on its own pages with a verbatim quote: documented, with the status it states (an
                # earlier "available" from keywords stays unless the competitor says it is limited).
                if o.verification != "documented" or status == FeatureStatus.AVAILABLE:
                    o.status = status if o.verification != "documented" else FeatureStatus.AVAILABLE
                o.verification, o.basis, o.notes = "documented", Basis.EVIDENCE, ""
        except LLMUnavailable:
            pass
        except LLMError as exc:
            errors.append(f"LLM profile of {comp.name} failed: {exc}")
        if not comparable_:
            comp.verified, comp.deep = False, False
            comp.rationale = f"Rejected as not comparable after deep analysis. {comp.rationale}".strip()
            return None

        # Detailed features and documented workflows / use cases, as for the client (core/offering.py).
        page_dicts = [{"url": p.url, "title": p.title, "headings": p.headings, "text": p.text} for p in pages]
        offer_docs = offering.capability_docs(page_dicts)
        try:
            comp.inventory, errs = await offering.extract_features(
                ctx.llm, ledger, offer_docs, comp.name, comp.name, ctx.taxonomy,
                max_batches=ctx.settings.competitor_feature_batches)
            errors += [f"{comp.name}: {e}" for e in errs]
            comp.workflows, errs = await offering.extract_workflows(ctx.llm, ledger, offer_docs, comp.name, comp.name)
            errors += [f"{comp.name}: {e}" for e in errs]
        except LLMUnavailable:
            pass
        for item in comp.inventory:
            fid = item["feature_id"]
            if fid and item["verification"] == "documented":
                status = FeatureStatus(item["status"])
                o = obs.setdefault(fid, FeatureObservation(feature_id=fid, status=status, confidence=0.85))
                o.evidence_ids = list(dict.fromkeys(o.evidence_ids + item["evidence_ids"][:2]))
                # An explicit "not offered" never overrides evidence that it is offered.
                if o.verification != "documented" and not (status == FeatureStatus.MISSING
                                                           and o.status in (FeatureStatus.AVAILABLE,
                                                                            FeatureStatus.PARTIAL)):
                    o.status = status
                o.verification, o.basis, o.notes = "documented", Basis.EVIDENCE, ""

        comp.description = comp.description or pages[0].description or ""
        comp.features = list(obs.values())
        comp.overlap = feature_overlap(ctx, comp.features, client_feats)
        comp.pages_analysed = list(dict.fromkeys([*comp.pages_analysed, *(p.url for p in pages)]))
        comp.pricing_profile = record_pricing(ledger, comp.name, extract_pricing(pages))
        s = ctx.settings
        priced_page = next((p for p in pages if kind_of(p.url) == "pricing"), None)
        comp.pricing_detail = await pricing_detail.extract(
            ctx.llm, ledger, pages, comp.name, base_currency=s.pricing_base_currency, rates=s.pricing_fx_rates,
            rates_date=s.pricing_fx_date, deterministic=comp.pricing_profile,
            accessed_at=priced_page.fetched_at if priced_page else None)
        # Public announcements (blog, news, changelog) on its own pages: product moves to watch.
        comp.announcements = []
        for a in find_announcements(pages, limit=8):
            e = ledger.add(f"{comp.name} announcement: {a.title}", a.source_url, "website", 0.8 if a.date else 0.7,
                           extracted_text=f"{a.title} — {a.url}" + (f" ({a.date})" if a.date else ""))
            comp.announcements.append({"title": a.title, "url": a.url, "date": a.date, "is_product": a.is_product,
                                       "evidence_id": e.id})
        if comp.pricing_profile and not comp.pricing:
            entry = comp.pricing_profile.get("entry_price_monthly")
            comp.pricing = (f"from {entry:g} {comp.pricing_profile.get('currency') or ''}/month".strip()
                            if entry else "custom / contact sales" if comp.pricing_profile.get("enterprise_contact")
                            else None)
        if not llm_classified and comp.classification not in ("open_source", "adjacent"):
            overlap = len(comp.overlap.get("shared", [])) / max(1, len(client_feats))
            comp.classification = "direct" if overlap >= 0.4 else "indirect" if overlap >= 0.2 else "adjacent"
        return comp


DEEP_PATHS = ["/pricing", "/plans", "/features", "/product", "/products", "/integrations", "/docs", "/help",
              "/support", "/customers", "/case-studies", "/blog", "/news", "/changelog"]


def _keyword_features(ctx: RunContext, name: str, pages: list, obs: dict[str, FeatureObservation]
                      ) -> dict[str, FeatureObservation]:
    """Deterministic feature evidence from taxonomy keyword matches on the competitor's own pages.

    Same rules as for the client (agents/product_features.combine_signals): only pages that can describe the
    offering count (never blog posts, job adverts or legal pages). A match on a page where the company states
    its offering, several keywords on one page, or two pages make it *evidenced* (available); a single keyword
    on one other page is only *mentioned* and stays "not publicly identified"."""
    for p in pages:
        kind = kind_of(p.url)
        if kind not in CAPABILITY_KINDS:
            continue
        text = f"{p.title} {p.description} {' '.join(p.headings)} {p.text}"
        for fid, kws in ctx.taxonomy.match_text(text).items():
            kws = affirmed(text, kws)
            if not kws:
                continue
            if fid in obs and any(ctx.ledger.get(e) and ctx.ledger.get(e).source_url == p.url
                                  for e in obs[fid].evidence_ids):
                continue
            e = ctx.ledger.add(f"{name} offers {ctx.taxonomy.get(fid).name}", p.url, "website",
                               0.6 if len(kws) > 1 else 0.5, extracted_text=snippet(text, kws[0]))
            o = obs.setdefault(fid, FeatureObservation(
                feature_id=fid, status=FeatureStatus.UNKNOWN, confidence=e.confidence, basis=Basis.INFERRED,
                verification="mentioned",
                notes=f"mentioned once on {p.url}; not confirmed as an offered capability"))
            o.evidence_ids.append(e.id)
            o.confidence = max(o.confidence, e.confidence)
            strong = len(kws) > 1 or kind in OFFERING_KINDS or len(o.evidence_ids) >= 2
            if strong and o.verification == "mentioned":
                o.status, o.verification, o.basis, o.notes = (FeatureStatus.AVAILABLE, "evidenced", Basis.EVIDENCE,
                                                               "")
    return obs


def feature_overlap(ctx: RunContext, features: list[FeatureObservation], client_feats: set[str]) -> dict:
    """Which evidenced capabilities the competitor shares with the client, and which only one of them offers."""
    theirs = {o.feature_id for o in features if o.status in (FeatureStatus.AVAILABLE, FeatureStatus.PARTIAL)}

    def named(ids: set[str]) -> list[dict]:
        return sorted(({"feature_id": f, "name": ctx.taxonomy.get(f).name} for f in ids if ctx.taxonomy.get(f)),
                      key=lambda x: x["name"])

    return {"shared": named(theirs & client_feats), "competitor_only": named(theirs - client_feats),
            "client_only": named(client_feats - theirs)}


def _client_profile(ctx: RunContext, profile: dict, industry: dict, client_feats: set[str]) -> ClientProfile:
    rec = ctx.record
    own = tokens(f"{rec.client.name} {profile.get('name') or ''}")
    product = " ".join([rec.project.name, rec.project.description or "", profile.get("description") or "",
                        *(p.get("description", "") for p in profile.get("products", []))])
    pages = ctx.data("client_research").get("pages", [])
    return ClientProfile(
        features=client_feats,
        product_words=tokens(product) - own,
        industry_words=industry_terms(industry.get("industry") or profile.get("industry") or rec.client.industry or ""),
        customer_words=tokens(" ".join(industry.get("customer_segment") or profile.get("target_customers") or [])) - own,
        geo_words=tokens(" ".join(industry.get("geography") or profile.get("geographic_markets") or [])),
        model=model_signals(" ".join(p.get("text", "") for p in pages)),
    )


def _landscape_entry(c: Competitor) -> dict:
    return {"id": c.id, "rank": c.rank, "name": c.name, "url": c.url, "classification": c.classification,
            "description": c.description, "target_market": c.target_market, "reason": c.rationale,
            "relevance": c.relevance, "deep": c.deep, "deep_error": c.deep_error,
            "feature_ids": sorted(o.feature_id for o in c.features if o.status != FeatureStatus.UNKNOWN),
            "evidence_ids": c.evidence_ids[:6], "confidence": c.confidence, "pages_analysed": len(c.pages_analysed)}
