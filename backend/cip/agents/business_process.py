"""Business Process & Cost agent: cost-reduction, automation and AI opportunities (BRS 7.12-7.13).

The client's internal processes are not publicly observable, so this agent separates what was
*observed* from what is *assumed*:

* **Observed** — that a business process exists: the client's public pages mention it (with a quoted
  source), it offers the capability that implies it, or its app reviews complain about it. Every
  signal is in the evidence ledger.
* **Assumed** — how the process runs today and where it is inefficient. These come from the process
  catalog (``core/processes.yaml``), are labelled as assumptions and carry basis ``estimate``.

A process becomes an opportunity only when it is observed *and* the client does not already publicly
offer every capability of the improvement. Benefits are qualitative levels (low / medium / high), never
invented percentages. AI is suggested only as the improvement to an observed business process — never
because it is available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from cip.agents.base import Agent, RunContext
from cip.core.schemas import AgentResult, Basis, Finding

CATALOG = Path(__file__).resolve().parents[1] / "core" / "processes.yaml"
LEVEL_SCORE = {"low": 2.0, "medium": 3.5, "high": 5.0}
MAX_OPPORTUNITIES = 6


@dataclass(frozen=True)
class ProcessDef:
    id: str
    area: str
    name: str
    keywords: tuple[str, ...]
    features: tuple[str, ...]
    review_themes: tuple[str, ...]
    industries: tuple[str, ...]
    current_process: str
    inefficiency: str
    improvement: str
    how_it_works: str
    customer_impact: str
    revenue: str
    solution_features: tuple[str, ...]
    benefits: dict
    ai: bool
    automation: bool
    factors: dict


@lru_cache
def load_processes(path: str | None = None) -> tuple[ProcessDef, ...]:
    raw = yaml.safe_load(Path(path or CATALOG).read_text(encoding="utf-8"))
    out = []
    for p in raw["processes"]:
        sig = p.get("signals", {})
        out.append(ProcessDef(
            id=p["id"], area=p["area"], name=p["name"], keywords=tuple(sig.get("keywords", [])),
            features=tuple(sig.get("features", [])), review_themes=tuple(sig.get("review_themes", [])),
            industries=tuple(p.get("industries", [])), current_process=p["current_process"],
            inefficiency=p["inefficiency"], improvement=p["improvement"], how_it_works=p["how_it_works"],
            customer_impact=p["customer_impact"], revenue=p["revenue"],
            solution_features=tuple(p.get("solution_features", [])), benefits=dict(p["benefits"]),
            ai=bool(p.get("ai")), automation=bool(p.get("automation")), factors=dict(p["factors"]),
        ))
    return tuple(out)


def process_by_id(process_id: str) -> ProcessDef | None:
    return next((p for p in load_processes() if p.id == process_id), None)


def _pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])")


def _snippet(text: str, start: int, end: int, width: int = 90) -> str:
    a, b = max(0, start - width), min(len(text), end + width)
    return ("…" if a else "") + " ".join(text[a:b].split()) + ("…" if b < len(text) else "")


def operational_benefit(p: ProcessDef) -> str:
    b = p.benefits
    return (f"{b['resource_saving'].capitalize()} resource saving, {b['time_reduction']} processing-time reduction, "
            f"{b['error_reduction']} error/rework reduction (qualitative estimate).")


class BusinessProcessAgent(Agent):
    name = "business_process"
    description = "Identify cost-reduction, automation and AI opportunities in the client's business processes"
    after = ("client_research", "product_features", "app_store")

    async def run(self, ctx: RunContext) -> AgentResult:
        research = ctx.data("client_research")
        pages = [p for p in research.get("pages", []) + research.get("project_pages", []) if p.get("text")]
        obs = ctx.data("product_features").get("observations", {})
        themes = {t["theme"]: t for t in (ctx.data("app_store").get("client_reviews") or {}).get("themes", [])}
        industry = (research.get("profile", {}).get("industry") or ctx.record.client.industry or "").lower()
        product = ctx.record.project.name

        opportunities, in_place = [], []
        findings: dict[str, Finding] = {}
        for p in load_processes():
            observed, evidence = [], []
            # 1. Public pages that mention the process (quoted, up to two pages).
            hit_pages = 0
            for page in pages:
                low = page["text"].lower()
                match = next((m for k in p.keywords if (m := _pattern(k).search(low))), None)
                if not match:
                    continue
                ev = ctx.ledger.add(f"The client's website mentions “{match.group(0)}” ({p.area.lower()})",
                                    page["url"], "website", 0.7, extracted_text=_snippet(page["text"], *match.span()))
                evidence.append(ev.id)
                hit_pages += 1
                if hit_pages == 1:
                    observed.append(f"Website mentions “{match.group(0)}”")
                if hit_pages == 2:
                    break
            # 2. Capabilities the client offers that imply the process.
            offered = [f for f in p.features if obs.get(f, {}).get("status") in ("available", "partial")]
            for f in offered:
                tf = ctx.taxonomy.get(f)
                observed.append(f"Offers {tf.name if tf else f}")
                evidence += obs[f].get("evidence_ids", [])[:2]
            # 3. Customer pain in app reviews.
            pains = [themes[t] for t in p.review_themes if t in themes and themes[t].get("negative", 0) > 0]
            for t in pains:
                observed.append(f"{t['negative']} negative app review(s) about {t['label'].lower()}")
                evidence += t.get("evidence_ids", [])[:2]
            if not observed:
                continue

            have = {f: obs.get(f, {}).get("status") for f in p.solution_features}
            available = [f for f, s in have.items() if s == "available"]
            if p.solution_features and len(available) == len(p.solution_features):
                in_place.append({"process_id": p.id, "area": p.area, "name": p.name,
                                 "evidence_ids": list(dict.fromkeys(evidence))[:6]})
                continue
            missing = [ctx.taxonomy.get(f).name if ctx.taxonomy.get(f) else f
                       for f, s in have.items() if s not in ("available", "partial")]
            industry_fit = any(i in industry for i in p.industries) if industry else False
            confidence = min(0.7, 0.35 + 0.1 * min(hit_pages, 2) + 0.1 * bool(offered)
                             + 0.15 * bool(pains) + 0.05 * industry_fit)
            fmt = {"client": ctx.record.client.name, "product": product}
            problem = p.inefficiency.format(**fmt)
            if pains:
                problem += " Customers raise it in app reviews: " + "; ".join(
                    f"{t['negative']} negative review(s) about {t['label'].lower()}" for t in pains) + "."
            opp = {
                "id": f"proc_{p.id}", "process_id": p.id, "area": p.area, "name": p.name,
                "status": "partial" if any(s in ("available", "partial") for s in have.values()) else "opportunity",
                "observed": observed, "evidence_ids": list(dict.fromkeys(evidence))[:8],
                "current_process": p.current_process.format(**fmt), "inefficiency": p.inefficiency.format(**fmt),
                "assumptions": [p.current_process.format(**fmt), p.inefficiency.format(**fmt)],
                # BRS 7.13 fields
                "business_problem": problem, "proposed_solution": p.improvement.format(**fmt),
                "how_it_works": p.how_it_works.format(**fmt), "expected_benefit": operational_benefit(p),
                "cost_reduction": p.benefits["resource_saving"], "time_reduction": p.benefits["time_reduction"],
                "error_reduction": p.benefits["error_reduction"],
                "productivity": "high" if p.factors.get("productivity", 0) >= 4 else "medium",
                "customer_impact": p.customer_impact.format(**fmt), "revenue_opportunity": p.revenue.format(**fmt),
                "complexity": p.factors.get("complexity", 3), "ai": p.ai, "automation": p.automation,
                "missing_capabilities": missing, "industry_fit": industry_fit,
                "confidence": round(confidence, 3), "basis": Basis.ESTIMATE.value,
            }
            opportunities.append(opp)
            findings[opp["id"]] = (Finding(
                category="business_process", title=f"{p.area}: {p.name}",
                detail=f"Observed: {'; '.join(observed)}. Assumed: {p.inefficiency} Improvement: {p.improvement}",
                evidence_ids=opp["evidence_ids"][:5], confidence=opp["confidence"], basis=Basis.ESTIMATE))

        def value(o: dict) -> float:
            p = process_by_id(o["process_id"])
            return o["confidence"] * (p.factors.get("cost_saving", 0) + p.factors.get("productivity", 0)
                                      + p.factors.get("business_value", 0))

        opportunities.sort(key=lambda o: -value(o))
        kept = opportunities[:MAX_OPPORTUNITIES]
        return AgentResult(
            confidence=0.55 if kept else 0.3,
            findings=[findings[o["id"]] for o in kept],
            data={"opportunities": kept, "in_place": in_place,
                  "ai_opportunities": [o["id"] for o in kept if o["ai"]],
                  "automation_opportunities": [o["id"] for o in kept if o["automation"]],
                  "method": "Processes are observed from public pages, offered capabilities and app reviews; "
                            "current processes and inefficiencies are assumptions from the process catalog.",
                  "disclaimer": "Internal processes are not publicly observable. Current-process and inefficiency "
                                "statements are assumptions to validate with the client."},
        )
