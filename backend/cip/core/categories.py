"""Business gap categories and attribute labels (BRS 7.10, PRD 10.11).

Every scored opportunity gets one business category, chosen by deterministic rules from its gap type,
factor scores, competitor coverage, roadmap phase and priority, plus Low/Medium/High labels for the
business attributes the BRS asks for (relevance, customer value, competitive importance, revenue and
efficiency impact, time to value).
"""

from __future__ import annotations

CRITICAL = "Critical competitive gap"
HIGH_VALUE = "High-value product gap"
CX = "Customer experience gap"
OPERATIONAL = "Operational efficiency gap"
REVENUE = "Revenue opportunity"
AI = "AI opportunity"
AUTOMATION = "Automation opportunity"
STRATEGIC = "Strategic / long-term opportunity"
RISK = "Risk & compliance gap"  # security findings; not in the BRS list, kept separate on purpose
CATEGORIES = (CRITICAL, HIGH_VALUE, CX, OPERATIONAL, REVENUE, AI, AUTOMATION, STRATEGIC, RISK)

# Capabilities whose main value is automating work (non-AI automation opportunities).
AUTOMATION_FEATURES = {
    "ai.automation", "workflow.automation", "workflow.scheduling", "comm.sms", "comm.push", "comm.email",
    "billing.invoicing", "ai.document_processing", "platform.webhooks", "platform.crm_integration",
}


def level(value: float | None) -> str:
    v = float(value or 0)
    return "high" if v >= 4 else "medium" if v >= 2.5 else "low"


def attributes(factors: dict[str, float], complexity: str) -> dict[str, str]:
    return {
        "business_relevance": level(factors.get("business_value")),
        "customer_value": level(factors.get("user_impact")),
        "competitive_importance": level(factors.get("competitive_gap")),
        "revenue_impact": level(factors.get("revenue_potential")),
        "efficiency_impact": level(max(factors.get("cost_saving", 0), factors.get("productivity", 0))),
        "time_to_value": level(factors.get("time_to_value")),
        "complexity": complexity,
    }


def business_category(gap: dict, factors: dict[str, float], *, phase: str, priority: str, coverage: float,
                      process: dict | None = None) -> str:
    t = gap["gap_type"]
    fid = gap.get("feature_id") or ""
    if t == "security":
        return RISK
    competitive = t in ("missing", "partial", "ux", "ai", "pricing") and bool(gap.get("competitors_with"))
    if competitive and coverage >= 0.66 and priority == "high":
        return CRITICAL
    if t == "ai" or fid.startswith("ai.") or (process and process.get("ai")):
        return AI
    if t == "process" or fid in AUTOMATION_FEATURES:
        return AUTOMATION
    if phase == "phase_4_strategic":
        return STRATEGIC
    if t == "pricing":
        return REVENUE
    if t == "ux":
        return CX
    if factors.get("revenue_potential", 0) >= 4:
        return REVENUE
    if t == "technology" or max(factors.get("cost_saving", 0), factors.get("productivity", 0)) >= 4:
        return OPERATIONAL
    if factors.get("user_impact", 0) >= 4:
        return CX
    return HIGH_VALUE
