"""Competitor Discovery + Competitor Feature Analysis Agent.

A search result is never treated as a competitor automatically:

1. candidates are gathered from web search (and, if configured, LLM market
   knowledge — explicitly marked as a hypothesis);
2. each candidate's own website is fetched; unreachable candidates stay
   unverified and are excluded from comparison;
3. relevance is measured against the client's feature footprint and the
   candidate is classified (direct / indirect / adjacent / open source /
   enterprise / emerging);
4. features are extracted from the competitor's pages with evidence.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import get_args

from pydantic import BaseModel, Field

from cip.agents.base import Agent, RunContext
from cip.agents.pricing_analysis import record_pricing
from cip.connectors.research.pricing import extract_pricing
from cip.connectors.research.web import registrable_domain
from cip.connectors.source_control.base import parse_repo_url
from cip.core.evidence import snippet
from cip.core.grounding import Grounder, SourceDoc, SourcedValue, pages_to_prompt
from cip.core.llm import LLMError, LLMUnavailable
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


DISCOVERY_PROMPT = """You are a market research analyst. Identify products that compete with the client's
project. Use the search results provided (cite the result url as source_url) and, where results are thin,
well-known products you are confident exist (leave source_url null for those). Only list real software
products/companies with their official website url — not review sites, articles or directories.
classification must be one of: direct, indirect, adjacent, open_source, enterprise, emerging."""

PROFILE_PROMPT = """You are analysing a potential competitor's website. Decide whether it is genuinely
comparable to the client's project (is_comparable) and classify it (direct, indirect, adjacent, open_source,
enterprise, emerging). Extract description, target market and pricing, and the product features using ONLY
feature ids from the taxonomy. Every value needs the SOURCE url and a verbatim quote from that page."""


MARKETPLACE_HOSTS = ("g2.com", "capterra.com", "producthunt.com", "getapp.com", "softwareadvice.com",
                     "trustradius.com", "alternativeto.net", "saasworthy.com")
STOPWORDS = {"the", "and", "for", "with", "platform", "software", "solution", "solutions", "system", "app",
             "application", "management", "online", "based", "web", "tool", "tools", "service", "services", "from",
             "that", "your", "their", "this", "into", "using", "our"}
MIN_OSS_STARS = 50


def _queries(ctx: RunContext, profile: dict) -> list[str]:
    rec = ctx.record
    desc = (rec.project.description or profile.get("description") or rec.project.name)[:120]
    industry = profile.get("industry") or rec.client.industry or ""
    qs = [f"{desc} software alternatives", f"{rec.project.name} competitors",
          f"best {industry} {desc} platforms".strip(),
          # Review sites & launch directories: used as *sources of names*, never as competitors themselves.
          f"{desc} alternatives site:g2.com", f"{desc} site:capterra.com", f"{desc} site:producthunt.com"]
    return [q for q in qs if q.strip()]


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
    after = ("client_research", "product_features")

    async def run(self, ctx: RunContext) -> AgentResult:
        if "external_research" not in ctx.approvals:
            # Web search, GitHub search and competitor websites are covered by the external-research gate.
            return AgentResult(status=AgentStatus.SKIPPED, confidence=1.0,
                               findings=[Finding(category="market", title="Competitor research skipped",
                                                 detail="External research was not approved for this run.",
                                                 confidence=1.0)],
                               data={"competitors": [], "rejected": [],
                                     "reason": "external research not approved"})
        rec = ctx.record
        profile = ctx.data("client_research").get("profile", {})
        client_obs = ctx.data("product_features").get("observations", {})
        client_feats = {fid for fid, o in client_obs.items() if o["status"] in ("available", "partial")}
        client_domain = registrable_domain(profile.get("domain") or rec.client.domain or "") or None
        ledger = ctx.ledger
        errors: list[str] = []
        findings: list[Finding] = []

        # 1. Candidate discovery ---------------------------------------------
        results = []
        for q in _queries(ctx, profile):
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
                f"Industry: {profile.get('industry') or rec.client.industry or 'unknown'}\n"
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
                data={"competitors": [], "rejected": []},
            )

        # 2-4. Verify, classify and profile each candidate -------------------
        limit = ctx.settings.max_competitors * 2
        items = list(candidates.items())[:limit]
        profiled = await asyncio.gather(*(self._profile(ctx, host, cand, client_feats, search_docs, errors)
                                          for host, cand in items))
        competitors = [c for c in profiled if c and c.verified]
        rejected = [c.model_dump() for c in profiled if c and not c.verified]
        competitors.sort(key=lambda c: c.confidence, reverse=True)
        competitors = competitors[: ctx.settings.max_competitors]

        for c in competitors:
            n = sum(1 for f in c.features if f.status == FeatureStatus.AVAILABLE)
            findings.append(Finding(category=f"competitor.{c.classification}", title=f"{c.name} ({c.classification})",
                                    detail=f"{c.rationale} — {n} comparable features evidenced",
                                    evidence_ids=c.evidence_ids, confidence=c.confidence))
        return AgentResult(
            findings=findings,
            evidence=[ledger.get(e) for c in competitors for e in
                      [*c.evidence_ids, *(i for f in c.features for i in f.evidence_ids)] if ledger.get(e)],
            confidence=0.75 if competitors else 0.3,
            errors=errors,
            data={"competitors": [c.model_dump() for c in competitors], "rejected": rejected},
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

    async def _profile_github(self, ctx: RunContext, repo, comp: Competitor, client_feats: set[str]) -> Competitor:
        """Verify an open-source alternative through the GitHub API (README + metadata)."""
        api = ctx.settings.github_api_url.rstrip("/")
        meta = await ctx.fetcher.get_json(f"{api}/repos/{repo.full_name}")
        readme = await ctx.fetcher.get_text(f"{api}/repos/{repo.full_name}/readme",
                                            headers={"Accept": "application/vnd.github.raw+json"})
        if not isinstance(meta, dict) or not readme:
            comp.rationale = (comp.rationale + " — repository could not be verified").strip(" —")
            comp.confidence = 0.2
            return comp
        comp.verified = True
        comp.classification = "open_source"
        comp.description = (meta.get("description") or "")[:300]
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
        overlap = len(set(obs) & client_feats) / max(1, len(client_feats)) if client_feats else 0.5
        if overlap < 0.1 and client_feats:
            comp.verified = False
            comp.rationale = f"Rejected as not comparable (feature overlap {overlap:.0%}). {comp.rationale}".strip()
            comp.confidence = 0.3
            return comp
        comp.confidence = round(min(0.9, 0.45 + overlap * 0.5), 3)
        return comp

    async def _profile(self, ctx: RunContext, host: str, cand: _LLMCandidate, client_feats: set[str],
                       search_docs: list[SourceDoc], errors: list[str]) -> Competitor | None:
        ledger = ctx.ledger
        comp = Competitor(name=cand.name, url=cand.url or f"https://{host}",
                          classification=cand.classification if cand.classification in CLASSES else "direct",
                          rationale=cand.rationale)
        if cand.source_url:
            sd = next((d for d in search_docs if d.url == cand.source_url), None)
            stype = _source_type(cand.source_url)
            ev = ledger.add(f"{cand.name} surfaced in {'a review/launch directory' if stype == 'marketplace' else 'market search'}",
                            cand.source_url, stype, 0.5, extracted_text=sd.text[:400] if sd else None)
            comp.evidence_ids.append(ev.id)

        repo = parse_repo_url(comp.url)
        if repo and repo.provider == "github":
            return await self._profile_github(ctx, repo, comp, client_feats)

        pages = await ctx.fetcher.crawl(comp.url, max_pages=6, prefer=["/pricing", "/plans"])
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

        # Deterministic feature evidence from keyword matches
        obs: dict[str, FeatureObservation] = {}
        for p in pages:
            text = f"{p.title} {p.description} {' '.join(p.headings)} {p.text}"
            for fid, kws in ctx.taxonomy.match_text(text).items():
                e = ledger.add(f"{cand.name} offers {ctx.taxonomy.get(fid).name}", p.url, "website",
                               0.6 if len(kws) > 1 else 0.5, extracted_text=snippet(text, kws[0]))
                o = obs.setdefault(fid, FeatureObservation(feature_id=fid, status=FeatureStatus.AVAILABLE,
                                                           confidence=e.confidence, basis=Basis.EVIDENCE))
                o.evidence_ids.append(e.id)
                o.confidence = max(o.confidence, e.confidence)

        comparable = True
        llm_classified = False
        docs = [SourceDoc(p.url, f"{p.title}\n{' / '.join(p.headings)}\n{p.text}", "website") for p in pages]
        grounder = Grounder(ledger, docs)
        try:
            prof = await ctx.llm.complete_json(
                PROFILE_PROMPT,
                f"Client project: {ctx.record.project.name} — {ctx.record.project.description or ''}\n"
                f"Client features: {sorted(client_feats)}\nCandidate: {cand.name}\n\nTaxonomy:\n"
                f"{ctx.taxonomy.describe_for_prompt()}\n\n{pages_to_prompt(docs, 24_000)}",
                _LLMCompetitorProfile,
            )
            comparable = prof.is_comparable
            if prof.classification in CLASSES:
                comp.classification = prof.classification
                llm_classified = True
            comp.rationale = prof.rationale or comp.rationale
            for attr in ("description", "target_market", "pricing"):
                sv = getattr(prof, attr)
                e = grounder.ground_value(f"{cand.name} {attr.replace('_', ' ')}", sv)
                if e:
                    setattr(comp, attr, sv.value)
                    comp.evidence_ids.append(e.id)
            for f in prof.features:
                if f.feature_id not in ctx.taxonomy:
                    continue
                e = grounder.ground(f"{cand.name} offers {ctx.taxonomy.get(f.feature_id).name}", f.source_url,
                                    f.quote, 0.85)
                if not e:
                    continue
                status = FeatureStatus.PARTIAL if f.status == "partial" else FeatureStatus.AVAILABLE
                o = obs.setdefault(f.feature_id, FeatureObservation(feature_id=f.feature_id, status=status,
                                                                    confidence=e.confidence))
                o.evidence_ids.append(e.id)
                o.confidence = max(o.confidence, e.confidence)
        except LLMUnavailable:
            pass
        except LLMError as exc:
            errors.append(f"LLM profile of {cand.name} failed: {exc}")

        comp.description = comp.description or home.description or ""
        comp.features = list(obs.values())
        comp.pricing_profile = record_pricing(ledger, cand.name, extract_pricing(pages))
        if comp.pricing_profile and not comp.pricing:
            entry = comp.pricing_profile.get("entry_price_monthly")
            comp.pricing = (f"from {entry:g} {comp.pricing_profile.get('currency') or ''}/month".strip()
                            if entry else "custom / contact sales" if comp.pricing_profile.get("enterprise_contact")
                            else None)

        # Relevance against the client's footprint
        comp_feats = {o.feature_id for o in comp.features}
        overlap = len(comp_feats & client_feats) / max(1, len(client_feats)) if client_feats else 0.5
        if not comparable or (overlap < 0.1 and client_feats):
            comp.verified = False
            comp.rationale = f"Rejected as not comparable (feature overlap {overlap:.0%}). {comp.rationale}".strip()
            comp.confidence = 0.3
            return comp
        comp.confidence = round(min(0.95, 0.5 + overlap * 0.5), 3)
        if not llm_classified and comp.classification != "open_source":
            comp.classification = "direct" if overlap >= 0.4 else "indirect" if overlap >= 0.2 else "adjacent"
        return comp
