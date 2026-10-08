"""Opportunity Analysis + Prioritization Agent.

1. Every gap gets factor scores. Baselines are deterministic (taxonomy
   defaults, competitor coverage, gap type); the LLM may only *adjust* a factor
   by at most ±1 and must justify it. Business narratives are labelled as
   estimates.
2. Priority = transparent weighted score (``cip.core.scoring``).
3. Top-N opportunities become roadmap recommendations assigned to phases.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from cip.agents.base import Agent, RunContext
from cip.agents.business_process import process_by_id
from cip.core.categories import attributes, business_category
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.source_quality import TIER_LABELS
from cip.core.source_quality import factor as source_factor
from cip.core.schemas import AgentResult, Basis, Finding, Opportunity, Recommendation
from cip.core.scoring import (
    ALL_FACTORS,
    PHASE_LABELS,
    assign_phase,
    clamp_factor,
    complexity_label,
    normalized,
    priority_label,
    score_opportunity,
)

TECH_GAP_FACTORS: dict[str, dict[str, float]] = {
    "Automated test coverage": dict(business_value=3, user_impact=2, revenue_potential=1, strategic_alignment=4,
                                    ai_opportunity=1, complexity=2, risk=1),
    "CI/CD pipeline": dict(business_value=3, user_impact=1, revenue_potential=1, strategic_alignment=4,
                           ai_opportunity=0, complexity=1, risk=1),
    "Observability (error tracking, metrics, tracing)": dict(business_value=3, user_impact=3, revenue_potential=1,
                                                             strategic_alignment=4, ai_opportunity=1, complexity=1,
                                                             risk=1),
    "Reproducible builds (dependency locking)": dict(business_value=2, user_impact=1, revenue_potential=0,
                                                     strategic_alignment=3, ai_opportunity=0, complexity=1, risk=1),
    "Framework modernization": dict(business_value=3, user_impact=3, revenue_potential=2, strategic_alignment=4,
                                    ai_opportunity=1, complexity=4, risk=4),
    "Developer documentation": dict(business_value=2, user_impact=1, revenue_potential=0, strategic_alignment=2,
                                    ai_opportunity=1, complexity=1, risk=0),
    "Secrets management": dict(business_value=4, user_impact=2, revenue_potential=1, strategic_alignment=5,
                               ai_opportunity=0, complexity=1, risk=1),
    "Containerized, reproducible deployment": dict(business_value=3, user_impact=1, revenue_potential=1,
                                                   strategic_alignment=3, ai_opportunity=0, complexity=2, risk=2),
}
PRICING_GAP_FACTORS: dict[str, dict[str, float]] = {
    "Transparent self-serve pricing": dict(business_value=4, user_impact=3, revenue_potential=4,
                                           strategic_alignment=3, ai_opportunity=0, complexity=1, risk=2),
    "Free trial": dict(business_value=4, user_impact=4, revenue_potential=4, strategic_alignment=3,
                       ai_opportunity=1, complexity=2, risk=1),
    "Free tier (freemium)": dict(business_value=3, user_impact=4, revenue_potential=3, strategic_alignment=3,
                                 ai_opportunity=0, complexity=2, risk=3),
    "Annual billing discount": dict(business_value=3, user_impact=1, revenue_potential=4, strategic_alignment=3,
                                    ai_opportunity=0, complexity=1, risk=1),
    "Enterprise tier": dict(business_value=4, user_impact=2, revenue_potential=5, strategic_alignment=4,
                            ai_opportunity=0, complexity=2, risk=2),
    "Entry price above market": dict(business_value=4, user_impact=3, revenue_potential=3, strategic_alignment=3,
                                     ai_opportunity=0, complexity=1, risk=3),
}
SECURITY_GAP_FACTORS: dict[str, dict[str, float]] = {
    "HTTPS enforcement": dict(business_value=4, user_impact=3, revenue_potential=1, strategic_alignment=5,
                              ai_opportunity=0, complexity=1, risk=1),
    "Web security hardening (headers & cookies)": dict(business_value=3, user_impact=2, revenue_potential=1,
                                                       strategic_alignment=4, ai_opportunity=0, complexity=1, risk=2),
    "Vulnerable dependency remediation": dict(business_value=4, user_impact=2, revenue_potential=1,
                                              strategic_alignment=5, ai_opportunity=0, complexity=2, risk=3),
    "Secure coding fixes": dict(business_value=4, user_impact=2, revenue_potential=1, strategic_alignment=5,
                                ai_opportunity=0, complexity=2, risk=2),
    "Vulnerability disclosure policy": dict(business_value=2, user_impact=0, revenue_potential=0,
                                            strategic_alignment=3, ai_opportunity=0, complexity=1, risk=0),
}
APP_GAP_FACTORS: dict[str, dict[str, float]] = {
    "Mobile app stability (crashes & bugs)": dict(market_demand=4, competitive_gap=3, business_value=4, user_impact=5, revenue_potential=2,
                                                  strategic_alignment=4, ai_opportunity=0, complexity=2, risk=1),
    "Mobile app performance": dict(market_demand=3, competitive_gap=2, business_value=3, user_impact=4, revenue_potential=1, strategic_alignment=3,
                                   ai_opportunity=0, complexity=3, risk=1),
    "Mobile login & account access": dict(market_demand=4, competitive_gap=3, business_value=4, user_impact=5, revenue_potential=2,
                                          strategic_alignment=4, ai_opportunity=0, complexity=2, risk=2),
    "Reliable notifications & reminders": dict(market_demand=3, competitive_gap=2, business_value=3, user_impact=4, revenue_potential=2,
                                               strategic_alignment=3, ai_opportunity=0, complexity=2, risk=1),
    "Mobile data sync reliability": dict(market_demand=3, competitive_gap=2, business_value=4, user_impact=4, revenue_potential=1,
                                         strategic_alignment=4, ai_opportunity=0, complexity=3, risk=2),
    "Mobile app usability": dict(market_demand=3, competitive_gap=2, business_value=3, user_impact=4, revenue_potential=2, strategic_alignment=3,
                                 ai_opportunity=1, complexity=3, risk=1),
    "Customer support responsiveness": dict(market_demand=3, competitive_gap=2, business_value=3, user_impact=3, revenue_potential=2,
                                            strategic_alignment=3, ai_opportunity=2, complexity=2, risk=1),
    "In-app pricing & subscription experience": dict(market_demand=3, competitive_gap=2, business_value=3, user_impact=3, revenue_potential=3,
                                                     strategic_alignment=3, ai_opportunity=0, complexity=2, risk=2),
    "App store rating below competitors": dict(market_demand=4, competitive_gap=4, business_value=4, user_impact=4, revenue_potential=3,
                                               strategic_alignment=4, ai_opportunity=0, complexity=3, risk=1),
    "Mobile app release cadence": dict(market_demand=2, competitive_gap=2, business_value=2, user_impact=2, revenue_potential=1, strategic_alignment=3,
                                       ai_opportunity=0, complexity=2, risk=1),
}
UX_GAP_FACTORS: dict[str, dict[str, float]] = {
    # Quality gaps found by auditing the client's own site: demand is explicit, not competitor coverage.
    "Accessibility (WCAG 2.2 AA) fixes": dict(market_demand=3, competitive_gap=2, business_value=3, user_impact=4,
                                              revenue_potential=2, strategic_alignment=4, ai_opportunity=0,
                                              complexity=2, risk=1),
    "Mobile-friendly responsive layout": dict(market_demand=4, competitive_gap=3, business_value=4, user_impact=4,
                                              revenue_potential=3, strategic_alignment=4, ai_opportunity=0,
                                              complexity=3, risk=1),
    "Page speed (Core Web Vitals)": dict(market_demand=3, competitive_gap=2, business_value=3, user_impact=4,
                                         revenue_potential=3, strategic_alignment=3, ai_opportunity=0,
                                         complexity=2, risk=1),
    "Clear primary call to action": dict(market_demand=4, competitive_gap=3, business_value=4, user_impact=3,
                                         revenue_potential=4, strategic_alignment=4, ai_opportunity=0,
                                         complexity=1, risk=1),
    "UX quality below competitors": dict(market_demand=3, competitive_gap=4, business_value=3, user_impact=4,
                                         revenue_potential=3, strategic_alignment=3, ai_opportunity=0,
                                         complexity=3, risk=1),
    # Practice gaps (coverage-based demand from the competitors that have them).
    "Self-serve sign-up entry point": dict(business_value=4, user_impact=3, revenue_potential=4,
                                           strategic_alignment=3, ai_opportunity=0, complexity=2, risk=1),
    "Live chat support on the website": dict(business_value=3, user_impact=3, revenue_potential=3,
                                             strategic_alignment=3, ai_opportunity=3, complexity=1, risk=1),
    "Help center / FAQ": dict(business_value=3, user_impact=3, revenue_potential=1, strategic_alignment=3,
                              ai_opportunity=3, complexity=1, risk=0),
    "Trust signals (customers, reviews, compliance)": dict(business_value=3, user_impact=1, revenue_potential=3,
                                                           strategic_alignment=3, ai_opportunity=0, complexity=1,
                                                           risk=0),
    "Site search": dict(business_value=2, user_impact=3, revenue_potential=1, strategic_alignment=2,
                        ai_opportunity=3, complexity=2, risk=1),
}
GENERIC_FACTORS = dict(business_value=3, user_impact=3, revenue_potential=2, strategic_alignment=3,
                       ai_opportunity=1, complexity=3, risk=2)


class _LLMOpp(BaseModel):
    gap_id: str
    business_opportunity: str
    potential_users: str = ""
    revenue_opportunity: str = ""
    user_impact_text: str = ""
    technical_approach: str = ""
    adjustments: dict[str, float] = Field(default_factory=dict)
    adjustment_rationale: str = ""


class _LLMOpps(BaseModel):
    opportunities: list[_LLMOpp] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a product strategy consultant. For each gap, describe the business opportunity,
who benefits, the revenue opportunity and a one-paragraph technical approach that fits the client's stack.
You may propose factor adjustments of at most +1 or -1 (factors: business_value, user_impact, revenue_potential,
strategic_alignment, ai_opportunity, technical_feasibility, complexity, risk) with a rationale grounded in the
provided context. These are estimates and will be labelled as such."""


# Cost-saving and productivity baselines (BRS 7.16) for taxonomy features: category default, feature override.
OPERATIONAL_BY_CATEGORY: dict[str, tuple[float, float]] = {
    "authentication": (1, 1), "communication": (2, 3), "ai": (3, 3), "analytics": (2, 4), "billing": (3, 3),
    "scheduling": (3, 4), "integrations": (3, 3), "experience": (1, 2), "trust": (1, 1),
}
OPERATIONAL_BY_FEATURE: dict[str, tuple[float, float]] = {
    "ai.automation": (5, 5), "ai.document_processing": (5, 5), "ai.assistant": (4, 3), "workflow.automation": (4, 5),
    "billing.invoicing": (4, 4), "ux.self_service_portal": (4, 3), "comm.sms": (3, 3), "analytics.reports": (3, 4),
    "platform.crm_integration": (2, 4), "platform.webhooks": (3, 3), "workflow.scheduling": (3, 4),
}
OPERATIONAL_BY_TYPE: dict[str, tuple[float, float]] = {
    "technology": (2, 4), "security": (1, 1), "pricing": (1, 1), "ux": (1, 2),
}


def baseline_factors(ctx: RunContext, gap: dict, stack: set[str]) -> dict[str, float]:
    if gap.get("process_id") and (proc := process_by_id(gap["process_id"])):
        # Process opportunities: factors from the process catalog; demand from evidence of pain.
        base = dict(proc.factors)
        base["market_demand"] = 3.0 if gap.get("confidence", 0) >= 0.6 else 2.0
        base["competitive_gap"] = 1.5
        base["ai_opportunity"] = 4.0 if proc.ai else 1.0
        base["technical_feasibility"] = 3.5 if stack else 3.0
        base["time_to_value"] = max(1.0, min(5.0, 5.5 - base.get("complexity", 3)))
        return {k: clamp_factor(v) for k, v in base.items()}
    tf = ctx.taxonomy.get(gap["feature_id"]) if gap.get("feature_id") else None
    base = dict(tf.defaults) if tf else dict(TECH_GAP_FACTORS.get(gap["name"])
                                              or PRICING_GAP_FACTORS.get(gap["name"])
                                              or SECURITY_GAP_FACTORS.get(gap["name"])
                                              or APP_GAP_FACTORS.get(gap["name"])
                                              or UX_GAP_FACTORS.get(gap["name"]) or GENERIC_FACTORS)
    n_comp = max(1, len(ctx.data("competitor_research").get("competitors", [])))
    # Demand: the deep-analysed competitors, or the wider Top-10 landscape when that is stronger.
    coverage = max(len(gap.get("competitors_with", [])) / n_comp, gap.get("landscape_share") or 0)
    base["market_demand"] = round(1 + 4 * coverage, 2) if gap["gap_type"] not in ("technology", "security") else 2.0
    base["competitive_gap"] = {
        "missing": 2 + 3 * coverage, "ux": 2 + 3 * coverage, "ai": 2 + 3 * coverage,
        "partial": 1.5 + 2 * coverage, "technology": 1.5, "pricing": 2 + 3 * coverage, "security": 2.5,
    }.get(gap["gap_type"], 2.0)
    if gap.get("market_class") == "industry_standard":
        base["competitive_gap"] = base["competitive_gap"] + 0.5  # a must-have, not just a nice-to-have
    feasibility = 3.0
    if gap["gap_type"] == "partial":
        feasibility += 1  # builds on an existing implementation
    if gap["gap_type"] == "technology":
        feasibility += 1
    if not stack:
        feasibility -= 0.5  # stack unknown => more uncertainty
    base["technical_feasibility"] = feasibility
    if "cost_saving" not in base or "productivity" not in base:
        cost, prod = (OPERATIONAL_BY_FEATURE.get(tf.id) or OPERATIONAL_BY_CATEGORY.get(tf.category_id, (2, 2))
                      if tf else OPERATIONAL_BY_TYPE.get(gap["gap_type"], (2, 2)))
        base.setdefault("cost_saving", cost)
        base.setdefault("productivity", prod)
    base.setdefault("time_to_value", max(1.0, min(5.0, 5.5 - base.get("complexity", 3)
                                                  + (0.5 if gap["gap_type"] in ("pricing", "ux") else 0))))
    explicit = APP_GAP_FACTORS.get(gap["name"]) or UX_GAP_FACTORS.get(gap["name"]) or {}
    if "market_demand" in explicit:
        # Quality gaps evidenced by reviews or audits: demand comes from that evidence, not competitor coverage.
        base["market_demand"] = explicit["market_demand"]
        base["competitive_gap"] = explicit["competitive_gap"]
    return {k: clamp_factor(v) for k, v in base.items()}


# Hiring area (from the client's job board) -> which gaps it signals strategic intent for.
TECH_OPS_GAPS = {"CI/CD pipeline", "Observability (error tracking, metrics, tracing)",
                 "Containerized, reproducible deployment", "Automated test coverage"}
HIRING_GAP_MATCH = {
    "AI / Machine learning": lambda g: g["gap_type"] == "ai",
    "Mobile": lambda g: g.get("feature_id") == "ux.mobile_app",
    "Security": lambda g: (g.get("feature_id") or "").startswith(("security.", "auth.sso", "auth.mfa"))
    or g["name"] == "Secrets management",
    "Cloud / DevOps / SRE": lambda g: g["name"] in TECH_OPS_GAPS,
    "Data & analytics": lambda g: (g.get("feature_id") or "").startswith("analytics.")
    or g.get("feature_id") == "ai.predictive",
}


def hiring_signal_for(gap: dict, signals: list[dict]) -> dict | None:
    for s in signals:
        match = HIRING_GAP_MATCH.get(s["area"])
        if match and match(gap):
            return s
    return None


def default_narrative(gap: dict, process: dict | None = None) -> dict[str, str]:
    t = gap["gap_type"]
    if t == "process" and process:
        return {"business_opportunity": process["proposed_solution"],
                "potential_users": "Operations, support and customer-facing teams",
                "revenue_opportunity": process["revenue_opportunity"],
                "user_impact_text": process["customer_impact"]}
    if t == "technology":
        return {"business_opportunity": f"Reduce delivery risk and operating cost by addressing: {gap['name']}.",
                "potential_users": "Engineering and operations teams",
                "revenue_opportunity": "Indirect — faster, safer releases and lower incident cost.",
                "user_impact_text": "Fewer defects and outages for end users."}
    if t == "security":
        sev = "critical" if "critical" in gap["description"] else "high" if "high" in gap["description"] else None
        return {"business_opportunity": f"Reduce breach and compliance risk: {gap['name']}.",
                "potential_users": "All customers (data protection); security & compliance reviewers",
                "revenue_opportunity": "Protects existing revenue and unblocks security reviews in enterprise deals.",
                "user_impact_text": "Customer data better protected" + (f" ({sev}-severity issues)" if sev else "") + "."}
    if t == "pricing":
        return {"business_opportunity": f"Align pricing & packaging with the market: {gap['name']}.",
                "potential_users": "Prospects evaluating the product; sales and marketing teams",
                "revenue_opportunity": "Higher trial-to-paid conversion and win-rate against priced competitors.",
                "user_impact_text": "Easier evaluation and purchase decisions for prospects."}
    if gap["name"] in UX_GAP_FACTORS:
        legal = " Also reduces legal exposure (ADA, European Accessibility Act)." if "Accessib" in gap["name"] else ""
        return {"business_opportunity": f"Convert and retain more visitors: {gap['name']}.{legal}",
                "potential_users": "Website visitors and prospects, including people using assistive technology "
                                   "and phones",
                "revenue_opportunity": "Higher visitor-to-lead conversion; fewer drop-offs on mobile and slow pages.",
                "user_impact_text": "Easier, faster and more inclusive website experience."}
    if gap["name"] in APP_GAP_FACTORS:
        return {"business_opportunity": f"Win back app users and ratings: {gap['name']}.",
                "potential_users": "Mobile app users (patients/customers) and support teams",
                "revenue_opportunity": "Better store ratings lift installs and conversion; fewer churned users "
                                       "and support tickets.",
                "user_impact_text": "A more reliable, pleasant mobile experience."}
    if t == "ai":
        return {"business_opportunity": f"Differentiate with {gap['name']} to automate work and increase engagement.",
                "potential_users": "End users and internal operations teams",
                "revenue_opportunity": "Potential premium / AI add-on tier.",
                "user_impact_text": "Faster answers and less manual effort."}
    return {"business_opportunity": f"Close a competitive gap by offering {gap['name']}.",
            "potential_users": "Existing and prospective customers",
            "revenue_opportunity": "Improved win-rate and retention; potential upsell.",
            "user_impact_text": f"Users gain {gap['name'].lower()} without switching products."}


class PrioritizationAgent(Agent):
    name = "opportunity_prioritization"
    description = "Translate gaps into business opportunities and rank them with a transparent score"
    after = ("gap_analysis",)

    async def run(self, ctx: RunContext) -> AgentResult:
        gaps = ctx.data("gap_analysis").get("gaps", [])
        if not gaps:
            return AgentResult(confidence=0.5, data={"opportunities": [], "recommendations": []},
                               findings=[Finding(category="opportunity", title="No gaps identified",
                                                 confidence=0.5)])
        stack = {t["name"] for p in ctx.data("code_analysis").get("profiles", []) for t in p.get("technologies", [])}
        stack |= set(ctx.record.project.technology)
        errors: list[str] = []

        llm_by_gap: dict[str, _LLMOpp] = {}
        try:
            out = await ctx.llm.complete_json(
                SYSTEM_PROMPT,
                f"Client: {ctx.record.client.name} ({ctx.data('client_research').get('profile', {}).get('industry') or ctx.record.client.industry or 'industry unknown'})\n"
                f"Project: {ctx.record.project.name} — {ctx.record.project.description or ''}\n"
                f"Technology stack: {sorted(stack) or 'unknown'}\n\nGaps:\n"
                + "\n".join(f"- id={g['id']} [{g['gap_type']}] {g['name']}: {g['description']}" for g in gaps[:40]),
                _LLMOpps,
            )
            llm_by_gap = {o.gap_id: o for o in out.opportunities}
        except LLMUnavailable:
            pass
        except LLMError as exc:
            errors.append(f"LLM opportunity analysis failed: {exc}")

        signals = ctx.data("client_research").get("hiring", {}).get("signals", [])
        # Features customers ask for in app-store reviews (2+ reviews): evidence of demand.
        requested = {r["feature_id"]: r for r in ctx.data("app_store").get("requests", []) if r["count"] >= 2}
        processes = {o["process_id"]: o for o in ctx.data("business_process").get("opportunities", [])}
        n_comp = max(1, len(ctx.data("competitor_research").get("competitors", [])))
        scored: list[tuple[float, dict, Opportunity, Recommendation]] = []
        for g in gaps:
            factors = baseline_factors(ctx, g, stack)
            if g["gap_type"] == "security" and ("critical" in g["description"] or "high" in g["description"]):
                factors["business_value"] = clamp_factor(factors.get("business_value", 0) + 1)
            hiring = hiring_signal_for(g, signals)
            if hiring:
                # The client is staffing up in this area: evidence of strategic intent.
                factors["strategic_alignment"] = clamp_factor(factors.get("strategic_alignment", 0) + 1)
            request = requested.get(g.get("feature_id") or "")
            if request:
                factors["market_demand"] = clamp_factor(factors.get("market_demand", 0) + 1)
            process = processes.get(g.get("process_id") or "")
            narrative = default_narrative(g, process)
            approach = ""
            llm = llm_by_gap.get(g["id"])
            if llm:
                for k, delta in llm.adjustments.items():
                    if k in ALL_FACTORS and k in factors:
                        factors[k] = clamp_factor(factors[k] + max(-1.0, min(1.0, float(delta))))
                narrative.update({k: v for k, v in {
                    "business_opportunity": llm.business_opportunity, "potential_users": llm.potential_users,
                    "revenue_opportunity": llm.revenue_opportunity, "user_impact_text": llm.user_impact_text,
                }.items() if v})
                approach = llm.technical_approach
            if hiring:
                narrative["business_opportunity"] += (
                    f" The client is currently hiring {hiring['count']} {hiring['area']} role(s), "
                    "signalling strategic intent.")
            if request:
                narrative["business_opportunity"] += (
                    f" {request['count']} recent App Store reviews of the client's app ask for it.")
            # Source quality influences (never alone determines) confidence: the best-supporting source's tier.
            tiers = [ev.source_tier for e in g.get("evidence_ids", []) if (ev := ctx.ledger.get(e)) and ev.source_tier]
            best_tier = min(tiers) if tiers else None
            confidence = round(float(g.get("confidence", 0.5)) * source_factor(best_tier, ctx.settings.source_tier_factors), 3)
            score = score_opportunity(factors, ctx.scoring, confidence)
            phase = assign_phase(factors, ctx.scoring)
            priority = priority_label(score, ctx.scoring, confidence)
            category = business_category(g, factors, phase=phase, priority=priority,
                                         coverage=len(g.get("competitors_with", [])) / n_comp, process=process)
            attrs = attributes(factors, complexity_label(factors.get("complexity", 3)))
            if best_tier:
                attrs["source_quality"] = TIER_LABELS[best_tier]
            opp = Opportunity(gap_id=g["id"], name=g["name"], business_opportunity=narrative["business_opportunity"],
                              potential_users=narrative["potential_users"],
                              revenue_opportunity=narrative["revenue_opportunity"], factors=factors,
                              basis=Basis.ESTIMATE, priority=priority, business_category=category, attributes=attrs)
            deps = []
            if g["gap_type"] == "ai":
                deps.append("LLM provider access (OpenRouter) and data-governance review")
            if (g.get("feature_id") or "") == "auth.sso":
                deps.append("Identity provider test tenants (Okta / Entra ID)")
            rec = Recommendation(
                gap_id=g["id"], feature=g["name"], phase=phase, priority=priority, business_category=category,
                attributes=attrs, confidence=confidence,
                problem=g["description"], opportunity=narrative["business_opportunity"],
                business_impact=narrative["revenue_opportunity"], user_impact=narrative["user_impact_text"],
                technical_approach=approach or f"Implement {g['name']} within the existing "
                                               f"{', '.join(sorted(stack)[:4]) or 'application'} stack.",
                dependencies=deps, complexity=complexity_label(factors.get("complexity", 3)),
                expected_outcome=(f"{g['name']} in place; engineering risk reduced."
                                  if g["gap_type"] == "technology" else
                                  f"{g['name']} in place: {process['expected_benefit']}"
                                  if process else
                                  f"{g['name']} available to users; parity with "
                                  f"{len(g.get('competitors_with', []))} competitor(s)."
                                  if g.get("competitors_with") else
                                  f"{g['name']} available to users as a differentiator."),
                evidence_ids=g.get("evidence_ids", []) + ([hiring["evidence_id"]] if hiring else [])
                + (request["evidence_ids"] if request else []), score=score,
            )
            scored.append((normalized(score, ctx.scoring), g, opp, rec))

        scored.sort(key=lambda x: -x[0])
        top = scored[: ctx.settings.roadmap_top_n]
        recommendations = [r for _, _, _, r in top]
        roadmap: dict[str, list[str]] = {k: [] for k in PHASE_LABELS}
        for r in recommendations:
            roadmap[r.phase].append(r.id)

        return AgentResult(
            findings=[Finding(category="opportunity", title=f"#{i + 1} {r.feature} ({PHASE_LABELS[r.phase]})",
                              detail=f"score {r.score.total} (normalized {n:.2f})", evidence_ids=r.evidence_ids,
                              confidence=0.6, basis=Basis.ESTIMATE)
                      for i, (n, _, _, r) in enumerate(top)],
            confidence=0.65,
            errors=errors,
            data={
                "scoring_weights": ctx.scoring.weights,
                "opportunities": [{"normalized_score": n, **o.model_dump(), "score": r.score.model_dump(),
                                   "gap_type": g["gap_type"], "phase": r.phase}
                                  for n, g, o, r in scored],
                "recommendations": [r.model_dump() for r in recommendations],
                "roadmap": roadmap,
            },
        )
