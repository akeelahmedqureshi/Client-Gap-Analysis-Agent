"""Industry & Market agent: the business context for competitor selection and prioritisation (BRS 7.4).

Builds an ``IndustryProfile`` before competitor discovery:

* **Industry, market segment, customer segment, product category, business model, geography** — from
  the client's own grounded profile, its capability inventory and the CSV. With an LLM, values are
  extracted from the client's pages and must be grounded (source url + verbatim quote); otherwise they
  are derived deterministically and marked ``inferred``.
* **Trends, emerging technology, AI adoption and automation trends** — from web search (only when
  external research is approved). Each trend is a statement *from a source*: with an LLM it must quote
  the search result it came from; without one, the result's own sentence is used. Third-party sources
  carry lower confidence than the client's own site.
* **Search keywords** for competitor discovery, so competitors are chosen by market fit, not size.

Adoption *within the competitive set* (how many competitors offer AI or automation) is measured later,
by the feature comparison, from competitor evidence.
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field

from cip.agents.base import Agent, RunContext
from cip.core.positioning import audiences
from cip.core.grounding import Grounder, SourceDoc, SourcedValue, pages_to_prompt
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.relevance import model_signals
from cip.core.schemas import AgentResult, Basis, Finding

TREND_KINDS = ("trend", "technology", "ai_adoption", "automation")
TREND_CUES = ("trend", "adoption", "adopting", "growing", "growth", "increasingly", "shift", "demand", "rising",
              "expect", "automat", " ai ", "artificial intelligence")
STOP = {"the", "and", "for", "with", "software", "platform", "solution", "solutions", "system", "app", "online",
        "management", "services", "service", "tool", "tools", "your", "our", "their", "from", "that", "this"}


class _Trend(BaseModel):
    statement: str
    kind: str = "trend"
    source_url: str
    quote: str


class _LLMIndustry(BaseModel):
    industry: SourcedValue | None = None
    market_segment: SourcedValue | None = None
    product_category: SourcedValue | None = None
    customer_segment: list[SourcedValue] = Field(default_factory=list)
    trends: list[_Trend] = Field(default_factory=list)


PROMPT = """You are an industry analyst. From the client's pages, extract its industry, market segment (the
specific market its product competes in), product category and customer segments. From the search results,
list up to 8 industry trends, emerging technologies, AI adoption and automation trends relevant to this market
(kind: trend | technology | ai_adoption | automation). Every value needs the SOURCE url and a verbatim quote
from that source. Do not add anything that is not stated in the sources. Sources may be in any language: copy quotes verbatim in the source's language and write extracted values in English."""


def _kind(text: str) -> str:
    low = f" {text.lower()} "
    if " ai " in low or "artificial intelligence" in low or "machine learning" in low:
        return "ai_adoption"
    if "automat" in low:
        return "automation"
    if any(w in low for w in ("cloud", "mobile", "api", "telehealth", "digital", "technology")):
        return "technology"
    return "trend"


MODEL_LABELS = {"subscription": "Subscription (SaaS)", "free_tier": "Freemium", "free_trial": "Free trial",
                "enterprise_contact": "Sales-led / custom pricing", "open_source": "Open source"}


def keywords(*texts: str, n: int = 6) -> list[str]:
    words = [w for t in texts for w in re.findall(r"[a-z][a-z-]{3,}", (t or "").lower()) if w not in STOP]
    return [w for w, _ in Counter(words).most_common(n)]


class IndustryMarketAgent(Agent):
    name = "industry_market"
    description = "Establish industry, market segment, customers, business model and market trends"
    after = ("client_research", "product_features")

    async def run(self, ctx: RunContext) -> AgentResult:
        rec = ctx.record
        research = ctx.data("client_research")
        prof = research.get("profile", {})
        obs = ctx.data("product_features").get("observations", {})
        ledger, errors = ctx.ledger, []

        # --- deterministic baseline ----------------------------------------------------------
        cats = Counter(ctx.taxonomy.get(f).category_name for f, o in obs.items()
                       if o.get("status") in ("available", "partial") and ctx.taxonomy.get(f))
        products = prof.get("products") or []
        product_text = products[0].get("description", "") if products else ""
        segment_src = rec.project.description or product_text or prof.get("description") or rec.project.name
        out = {
            "industry": prof.get("industry") or rec.client.industry,
            "market_segment": (segment_src or "").split(".")[0][:120] or None,
            "product_category": ", ".join(c for c, _ in cats.most_common(2)) or None,
            "customer_segment": list(prof.get("target_customers") or []) or audiences(
                [rec.project.description or "", prof.get("description") or "", product_text]
                + [p.get("text", "")[:2000] for p in research.get("pages", [])[:6]]),
            "business_model": prof.get("business_model") or prof.get("revenue_model") or ", ".join(
                MODEL_LABELS[m] for m in sorted(model_signals(" ".join(
                    p.get("text", "") for p in research.get("pages", []) + research.get("project_pages", []))))
                if m in MODEL_LABELS) or None,
            "geography": list(prof.get("geographic_markets") or []) or ([prof["headquarters"]]
                                                                         if prof.get("headquarters") else []),
            "basis": {"industry": "evidence" if prof.get("industry") else "inferred",
                      "market_segment": "inferred", "product_category": "inferred",
                      "customer_segment": "evidence" if prof.get("target_customers") else "inferred",
                      "business_model": "evidence" if prof.get("business_model") else "inferred"},
            "trends": [], "evidence_ids": list(prof.get("evidence_ids", []))[:5],
        }

        # --- market research (needs the external-research approval) ----------------------
        results = []
        searched = "external_research" in ctx.approvals
        if searched:
            topic = " ".join(filter(None, [out["industry"], out["market_segment"]]))[:120] or rec.project.name
            for q in (f"{topic} industry trends", f"{out['industry'] or topic} AI adoption",
                      f"{topic} automation trends"):
                try:
                    results += await ctx.search.search(q, limit=6)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"trend search failed: {exc}")
            seen: set[str] = set()
            results = [r for r in results if not (r.url in seen or seen.add(r.url))]

        pages = [p for p in research.get("pages", []) + research.get("project_pages", []) if p.get("text")]
        docs = [SourceDoc(p["url"], f"{p.get('title', '')}\n{p['text']}", "website") for p in pages[:8]]
        search_docs = [SourceDoc(r.url, f"{r.title}\n{r.snippet}", "search") for r in results]
        grounder = Grounder(ledger, docs + search_docs)
        try:
            llm = await ctx.llm.complete_json(
                PROMPT, f"Client: {rec.client.name}\nProduct: {rec.project.name}\n\nClient pages:\n"
                        f"{pages_to_prompt(docs, 12_000)}\n\nSearch results:\n"
                + "\n".join(f"- {r.title} | {r.url} | {r.snippet}" for r in results[:20]), _LLMIndustry)
            for attr in ("industry", "market_segment", "product_category"):
                sv = getattr(llm, attr)
                ev = grounder.ground_value(f"{rec.client.name} {attr.replace('_', ' ')}", sv)
                if ev:
                    out[attr], out["basis"][attr] = sv.value, "evidence"
                    out["evidence_ids"].append(ev.id)
            for sv in llm.customer_segment:
                ev = grounder.ground_value(f"{rec.client.name} serves", sv)
                if ev and sv.value not in out["customer_segment"]:
                    out["customer_segment"].append(sv.value)
                    out["evidence_ids"].append(ev.id)
            for t in llm.trends[:8]:
                ev = grounder.ground(f"Market trend: {t.statement}", t.source_url, t.quote, 0.5)
                if ev:
                    out["trends"].append({"statement": t.statement, "kind": t.kind if t.kind in TREND_KINDS
                                          else _kind(t.statement), "source_url": t.source_url, "evidence_id": ev.id})
        except LLMUnavailable:
            # Deterministic: a search result's own sentence that talks about a trend, quoted as-is.
            for r in results:
                sentence = next((s.strip() for s in re.split(r"(?<=[.!?])\s+", r.snippet)
                                 if any(c in f" {s.lower()} " for c in TREND_CUES)), None)
                if sentence and len(sentence) > 25:
                    ev = ledger.add(f"Market trend ({r.title}): {sentence}", r.url, "search", 0.45,
                                    extracted_text=sentence)
                    out["trends"].append({"statement": sentence, "kind": _kind(sentence), "source_url": r.url,
                                          "evidence_id": ev.id})
        except LLMError as exc:
            errors.append(f"LLM industry analysis failed: {exc}")

        out["keywords"] = keywords(out["market_segment"] or "", out["industry"] or "",
                                   " ".join(out["customer_segment"]), rec.project.description or "")
        out["searched"] = searched
        findings = [Finding(category="industry", title=f"Market: {out['market_segment'] or 'unknown'}",
                            detail=f"Industry {out['industry'] or 'unknown'}; category {out['product_category'] or 'unknown'}; "
                                   f"customers {', '.join(out['customer_segment']) or 'not publicly identified'}.",
                            evidence_ids=out["evidence_ids"][:5], confidence=0.6,
                            basis=Basis.EVIDENCE if out["basis"]["market_segment"] == "evidence" else Basis.INFERRED)]
        findings += [Finding(category=f"industry.{t['kind']}", title=t["statement"][:200], evidence_ids=[t["evidence_id"]],
                             confidence=0.5, basis=Basis.EVIDENCE) for t in out["trends"]]
        if not searched:
            out["notes"] = ["Market trends need the external-research approval (web search)."]
        return AgentResult(confidence=0.6 if out["trends"] else 0.45, findings=findings, data=out, errors=errors)
