"""Implementation Planner Agent: a technical patch plan per recommendation."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from cip.agents.base import Agent, RunContext
from cip.core.architecture import build_architecture
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.schemas import AgentResult, Basis, Finding, ImplementationPlan

MAX_LLM_PLANS = 8
EFFORT_BY_COMPLEXITY = {"low": "1-2 engineer-weeks", "medium": "3-6 engineer-weeks",
                        "high": "2-3 engineer-months", "very_high": "4-6+ engineer-months"}


class _LLMPlan(BaseModel):
    objective: str
    architecture_impact: str
    frontend_changes: list[str] = Field(default_factory=list)
    backend_changes: list[str] = Field(default_factory=list)
    database_changes: list[str] = Field(default_factory=list)
    api_changes: list[str] = Field(default_factory=list)
    ai_changes: list[str] = Field(default_factory=list)
    infrastructure_changes: list[str] = Field(default_factory=list)
    security_changes: list[str] = Field(default_factory=list)
    testing_requirements: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    migration_requirements: list[str] = Field(default_factory=list)
    estimated_effort: str = ""
    recommended_team: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a principal engineer writing an implementation (patch) plan for one feature in an
existing codebase. Be concrete and specific to the detected stack and architecture: name the layers, services,
tables, endpoints, and components to add or change. Keep each list item to one line. Include security and
testing work. The plan is an estimate for planning purposes."""


def _stack_summary(ctx: RunContext) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in ctx.data("code_analysis").get("profiles", []):
        for t in p.get("technologies", []):
            out.setdefault(t["category"], [])
            if t["name"] not in out[t["category"]]:
                out[t["category"]].append(t["name"])
        if p.get("architecture"):
            out.setdefault("architecture", []).extend(a for a in p["architecture"] if a not in out.get("architecture", []))
    if ctx.record.project.technology:
        out.setdefault("declared", ctx.record.project.technology)
    return out


def template_plan(rec: dict, ctx: RunContext, stack: dict[str, list[str]]) -> ImplementationPlan:
    fe = ", ".join(stack.get("frontend", [])) or "the web frontend"
    be = ", ".join(stack.get("backend", [])) or "the backend service"
    db = ", ".join(stack.get("database", [])) or "the primary database"
    gap = next((g for g in ctx.data("gap_analysis").get("gaps", []) if g["id"] == rec["gap_id"]), {})
    feature = rec["feature"]
    is_ai = gap.get("gap_type") == "ai"
    is_tech = gap.get("gap_type") == "technology"
    if gap.get("gap_type") == "pricing":
        return ImplementationPlan(
            recommendation_id=rec["id"], feature=feature,
            objective=f"Close the pricing & packaging gap: {rec['problem']}",
            architecture_impact="Commercial / billing change; product changes limited to plans, entitlements and checkout.",
            frontend_changes=["Public pricing page with plan comparison", "Self-serve signup / trial / upgrade flow",
                              "In-app plan & usage indicators"],
            backend_changes=["Plan & entitlement model (feature flags per plan)",
                             "Billing provider integration (e.g. Stripe Billing: products, prices, trials, coupons)",
                             "Webhook handling for subscription lifecycle"],
            database_changes=["plans, subscriptions and entitlements tables (or billing-provider sync)"],
            api_changes=["Entitlement checks on gated endpoints"],
            infrastructure_changes=["Billing webhooks endpoint, secrets in a secrets manager"],
            security_changes=["PCI scope kept with the payment provider (hosted checkout)", "Audit plan changes"],
            testing_requirements=["Billing lifecycle tests (trial → paid → cancel)", "Entitlement unit tests",
                                  "Pricing page A/B or conversion tracking"],
            dependencies=["Pricing decision from leadership / finance"] + rec.get("dependencies", []),
            migration_requirements=["Grandfather existing customers onto equivalent plans"],
            estimated_effort=EFFORT_BY_COMPLEXITY.get(rec["complexity"], "TBD"),
            recommended_team=["Product manager", "Backend engineer", "Frontend engineer", "Finance / RevOps"],
            acceptance_criteria=[f"{feature} live and measurable", "Conversion and ARPA tracked before/after"],
            basis=Basis.ESTIMATE,
        )
    slug = feature.lower().split("(")[0].strip().replace(" / ", "_").replace(" ", "_").replace("-", "_")[:30]
    return ImplementationPlan(
        recommendation_id=rec["id"], feature=feature,
        objective=f"Deliver {feature} to close the identified gap: {rec['problem']}",
        architecture_impact=("Engineering/platform change; no user-facing architecture change." if is_tech else
                             f"New {feature} module in {be} exposed to {fe}; persisted in {db}."),
        frontend_changes=[] if is_tech else [f"{feature} UI in {fe}", "Settings/configuration screen",
                                             "Empty, loading and error states"],
        backend_changes=[f"{feature} service/module in {be}", "Input validation and authorization checks"]
        if not is_tech else [f"Adopt tooling for {feature.lower()} across services"],
        database_changes=[] if is_tech else [f"New table(s) for {slug} with audit columns", "Indexes for lookups"],
        api_changes=[] if is_tech else [f"REST endpoints for {slug} (CRUD + list)", "OpenAPI documentation"],
        ai_changes=["LLM integration via provider abstraction (OpenRouter)", "Prompt templates and evaluation set",
                    "Retrieval over client knowledge (pgvector) where answers need grounding"] if is_ai else [],
        infrastructure_changes=["Environment configuration and secrets in a secrets manager",
                                "Monitoring dashboards and alerts"],
        security_changes=["Role-based access checks", "Threat model review"] +
                         (["PII redaction before LLM calls", "Prompt-injection safeguards"] if is_ai else []),
        testing_requirements=["Unit tests", "Integration tests", "End-to-end happy path"] +
                             (["Prompt/response evaluation suite"] if is_ai else []),
        dependencies=rec.get("dependencies", []),
        migration_requirements=[] if is_tech else ["Forward-only DB migration with rollback script"],
        estimated_effort=EFFORT_BY_COMPLEXITY.get(rec["complexity"], "TBD"),
        recommended_team=["Backend engineer", "Frontend engineer", "QA"] + (["ML/AI engineer"] if is_ai else []),
        acceptance_criteria=[f"{feature} is available to target users", "Automated tests pass in CI",
                             "Feature usage is measurable in analytics"],
        basis=Basis.ESTIMATE,
    )


class EnhancementPlanningAgent(Agent):
    name = "enhancement_planning"
    description = "Generate a technical enhancement/patch plan for each recommendation"
    after = ("opportunity_prioritization",)

    async def run(self, ctx: RunContext) -> AgentResult:
        recs = ctx.data("opportunity_prioritization").get("recommendations", [])
        if not recs:
            return AgentResult(confidence=0.5, data={"plans": []})
        stack = _stack_summary(ctx)
        errors: list[str] = []
        sem = asyncio.Semaphore(3)

        async def plan_for(i: int, rec: dict) -> ImplementationPlan:
            if i >= MAX_LLM_PLANS:
                return template_plan(rec, ctx, stack)
            async with sem:
                try:
                    p = await ctx.llm.complete_json(
                        SYSTEM_PROMPT,
                        f"Project: {ctx.record.project.name} — {ctx.record.project.description or ''}\n"
                        f"Detected stack/architecture: {stack or 'unknown (no repository access)'}\n"
                        f"Feature: {rec['feature']}\nProblem: {rec['problem']}\n"
                        f"Opportunity: {rec['opportunity']}\nComplexity: {rec['complexity']}\n"
                        f"Suggested approach: {rec['technical_approach']}",
                        _LLMPlan,
                    )
                    return ImplementationPlan(recommendation_id=rec["id"], feature=rec["feature"],
                                              basis=Basis.ESTIMATE, **p.model_dump())
                except LLMUnavailable:
                    return template_plan(rec, ctx, stack)
                except LLMError as exc:
                    errors.append(f"LLM plan for {rec['feature']} failed: {exc}")
                    return template_plan(rec, ctx, stack)

        plans = await asyncio.gather(*(plan_for(i, r) for i, r in enumerate(recs)))
        architecture = build_architecture(ctx.data("code_analysis").get("profiles", []),
                                          ctx.record.project.technology, recs,
                                          ctx.data("gap_analysis").get("gaps", []))
        return AgentResult(
            findings=[Finding(category="plan", title=f"Plan: {p.feature}", detail=p.estimated_effort,
                              confidence=0.6, basis=Basis.ESTIMATE) for p in plans],
            confidence=0.6, errors=errors,
            data={"plans": [p.model_dump() for p in plans], "stack": stack, "architecture": architecture},
        )
