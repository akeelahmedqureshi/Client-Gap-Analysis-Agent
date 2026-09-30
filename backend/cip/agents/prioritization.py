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
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.schemas import AgentResult, Basis, Finding, Opportunity, Recommendation
from cip.core.scoring import (
    ALL_FACTORS,
    PHASE_LABELS,
    assign_phase,
    clamp_factor,
    complexity_label,
    normalized,
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


def baseline_factors(ctx: RunContext, gap: dict, stack: set[str]) -> dict[str, float]:
    tf = ctx.taxonomy.get(gap["feature_id"]) if gap.get("feature_id") else None
    base = dict(tf.defaults) if tf else dict(TECH_GAP_FACTORS.get(gap["name"], GENERIC_FACTORS))
    n_comp = max(1, len(ctx.data("competitor_research").get("competitors", [])))
    coverage = len(gap.get("competitors_with", [])) / n_comp
    base["market_demand"] = round(1 + 4 * coverage, 2) if gap["gap_type"] != "technology" else 2.0
    base["competitive_gap"] = {
        "missing": 2 + 3 * coverage, "ux": 2 + 3 * coverage, "ai": 2 + 3 * coverage,
        "partial": 1.5 + 2 * coverage, "technology": 1.5,
    }.get(gap["gap_type"], 2.0)
    feasibility = 3.0
    if gap["gap_type"] == "partial":
        feasibility += 1  # builds on an existing implementation
    if gap["gap_type"] == "technology":
        feasibility += 1
    if not stack:
        feasibility -= 0.5  # stack unknown => more uncertainty
    base["technical_feasibility"] = feasibility
    return {k: clamp_factor(v) for k, v in base.items()}


def default_narrative(gap: dict) -> dict[str, str]:
    t = gap["gap_type"]
    if t == "technology":
        return {"business_opportunity": f"Reduce delivery risk and operating cost by addressing: {gap['name']}.",
                "potential_users": "Engineering and operations teams",
                "revenue_opportunity": "Indirect — faster, safer releases and lower incident cost.",
                "user_impact_text": "Fewer defects and outages for end users."}
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

        scored: list[tuple[float, dict, Opportunity, Recommendation]] = []
        for g in gaps:
            factors = baseline_factors(ctx, g, stack)
            narrative = default_narrative(g)
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
            score = score_opportunity(factors, ctx.scoring)
            opp = Opportunity(gap_id=g["id"], name=g["name"], business_opportunity=narrative["business_opportunity"],
                              potential_users=narrative["potential_users"],
                              revenue_opportunity=narrative["revenue_opportunity"], factors=factors,
                              basis=Basis.ESTIMATE)
            deps = []
            if g["gap_type"] == "ai":
                deps.append("LLM provider access (OpenRouter) and data-governance review")
            if (g.get("feature_id") or "") == "auth.sso":
                deps.append("Identity provider test tenants (Okta / Entra ID)")
            rec = Recommendation(
                gap_id=g["id"], feature=g["name"], phase=assign_phase(factors, ctx.scoring),
                problem=g["description"], opportunity=narrative["business_opportunity"],
                business_impact=narrative["revenue_opportunity"], user_impact=narrative["user_impact_text"],
                technical_approach=approach or f"Implement {g['name']} within the existing "
                                               f"{', '.join(sorted(stack)[:4]) or 'application'} stack.",
                dependencies=deps, complexity=complexity_label(factors.get("complexity", 3)),
                expected_outcome=(f"{g['name']} in place; engineering risk reduced."
                                  if g["gap_type"] == "technology" else
                                  f"{g['name']} available to users; parity with "
                                  f"{len(g.get('competitors_with', []))} competitor(s)."
                                  if g.get("competitors_with") else
                                  f"{g['name']} available to users as a differentiator."),
                evidence_ids=g.get("evidence_ids", []), score=score,
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
