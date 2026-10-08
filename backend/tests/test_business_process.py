"""Cost-reduction, automation and AI opportunity analysis; BRS scoring dimensions, priority and categories."""

import re

from cip.agents.business_process import BusinessProcessAgent, load_processes
from cip.core.categories import AI, AUTOMATION, CRITICAL, CX, REVENUE, RISK, business_category
from cip.core.schemas import AgentResult
from cip.core.scoring import ScoringConfig, normalized, priority_label, score_opportunity

from fakes import sites_with_client_app, web_transport
from test_pipeline import run_all


def test_catalog_is_complete_and_never_invents_percentages():
    procs = load_processes()
    assert len(procs) >= 10
    areas = {p.area for p in procs}
    assert {"Scheduling", "Customer support", "Lead processing", "Document processing", "Reporting",
            "Customer onboarding", "Manual data entry", "Internal approvals"} <= areas
    for p in procs:
        assert set(p.benefits.values()) <= {"low", "medium", "high"}
        for text in (p.current_process, p.inefficiency, p.improvement, p.how_it_works):
            assert not re.search(r"\d+\s*%", text), f"{p.id}: no invented percentages"
        assert p.keywords or p.features or p.review_themes, f"{p.id} must be observable"
        assert {"cost_saving", "productivity", "complexity"} <= set(p.factors)


async def test_process_opportunities_are_observed_with_evidence_and_assumptions_labelled(make_ctx):
    from cip.connectors.research.web import WebFetcher

    ctx = make_ctx(fetcher=WebFetcher(transport=web_transport(sites_with_client_app())))
    status, _, _ = await run_all(ctx)
    assert status == "completed"
    bp = ctx.data("business_process")
    by_id = {o["process_id"]: o for o in bp["opportunities"]}
    assert {"scheduling", "customer_support"} <= set(by_id)
    for o in bp["opportunities"]:
        assert o["observed"] and o["evidence_ids"] and o["basis"] == "estimate"
        assert o["assumptions"] == [o["current_process"], o["inefficiency"]]
        assert {"business_problem", "proposed_solution", "how_it_works", "expected_benefit", "cost_reduction",
                "productivity", "customer_impact", "revenue_opportunity", "complexity"} <= set(o)
        for ev_id in o["evidence_ids"]:
            assert ctx.ledger.get(ev_id) is not None
    quoted = ctx.ledger.get(by_id["scheduling"]["evidence_ids"][0])
    assert "appointment" in quoted.claim.lower() and "appointment" in (quoted.extracted_text or "").lower()
    assert "negative app review" in " ".join(by_id["customer_support"]["observed"])
    assert by_id["customer_support"]["ai"] and by_id["scheduling"]["automation"] and not by_id["scheduling"]["ai"]
    assert set(bp["ai_opportunities"]) <= {o["id"] for o in bp["opportunities"]}

    # They become process gaps, scored with the BRS operational factors.
    gaps = [g for g in ctx.data("gap_analysis")["gaps"] if g["gap_type"] == "process"]
    assert {g["process_id"] for g in gaps} == set(by_id)
    opps = {o["gap_id"]: o for o in ctx.data("opportunity_prioritization")["opportunities"]}
    support = next(g for g in gaps if g["process_id"] == "customer_support")
    o = opps[support["id"]]
    assert o["factors"]["cost_saving"] == 5 and o["business_category"] == AI
    sched = opps[next(g["id"] for g in gaps if g["process_id"] == "scheduling")]
    assert sched["business_category"] == AUTOMATION and sched["attributes"]["efficiency_impact"] == "high"

    # The sales summary's cost-saving pick is the strongest cost saver; its AI pick is justified.
    s = ctx.data("sales_intelligence")
    assert opps[s["cost_saving_opportunity"]["gap_id"]]["factors"]["cost_saving"] >= 3.5
    ai_gap = next(g for g in ctx.data("gap_analysis")["gaps"] if g["id"] == s["ai_opportunity"]["gap_id"])
    assert ai_gap["competitors_with"] or ai_gap.get("process_id")


async def test_process_already_in_place_is_not_raised(make_ctx):
    ctx = make_ctx()
    ctx.outputs["client_research"] = AgentResult(data={"pages": [
        {"url": "https://abc-healthcare.com", "text": "Book an appointment online in seconds."}], "profile": {}})
    ctx.outputs["product_features"] = AgentResult(data={"observations": {
        "workflow.scheduling": {"status": "available", "evidence_ids": []},
        "comm.sms": {"status": "available", "evidence_ids": []},
        "workflow.automation": {"status": "available", "evidence_ids": []}}})
    out = (await BusinessProcessAgent().run(ctx)).data
    assert "scheduling" in {p["process_id"] for p in out["in_place"]}
    assert "scheduling" not in {o["process_id"] for o in out["opportunities"]}


async def test_nothing_observed_means_no_process_opportunities(make_ctx):
    ctx = make_ctx()
    ctx.outputs["client_research"] = AgentResult(data={"pages": [{"url": "https://x.example", "text": "Hello world."}]})
    out = (await BusinessProcessAgent().run(ctx)).data
    assert out["opportunities"] == [], "AI/automation is never suggested without an observed business process"


def test_evidence_confidence_scales_the_score_and_caps_priority():
    cfg = ScoringConfig()
    factors = {k: 4 for k in ("business_value", "user_impact", "market_demand", "competitive_gap", "revenue_potential",
                              "strategic_alignment", "ai_opportunity", "technical_feasibility", "cost_saving",
                              "productivity", "time_to_value")} | {"complexity": 2, "risk": 1}
    strong, weak, plain = (score_opportunity(factors, cfg, 0.9), score_opportunity(factors, cfg, 0.3),
                           score_opportunity(factors, cfg))
    assert strong.total > weak.total and plain.raw_total is None and plain.confidence_factor is None
    assert strong.raw_total == weak.raw_total == plain.total
    assert strong.contributions["complexity"] == weak.contributions["complexity"], "penalties are not scaled"
    assert priority_label(strong, cfg, 0.9) == "high"
    assert priority_label(score_opportunity(factors, cfg, 0.45), cfg, 0.45) != "high", "weak evidence is never High"
    low = score_opportunity({"business_value": 1, "user_impact": 1, "complexity": 4, "risk": 3}, cfg, 0.8)
    assert priority_label(low, cfg, 0.8) == "low" and normalized(low, cfg) < cfg.medium_min_normalized
    assert {"cost_saving", "productivity", "time_to_value"} <= set(strong.weights)


def test_business_categories():
    f = {"revenue_potential": 2, "user_impact": 3}
    comp = {"gap_type": "missing", "competitors_with": ["a", "b", "c"], "feature_id": "billing.payments"}
    assert business_category(comp, f, phase="phase_2_growth", priority="high", coverage=1.0) == CRITICAL
    assert business_category(comp, f, phase="phase_2_growth", priority="medium", coverage=1.0) != CRITICAL
    assert business_category({"gap_type": "ai", "feature_id": "ai.assistant"}, f, phase="phase_4_strategic",
                             priority="low", coverage=0) == AI
    assert business_category({"gap_type": "process"}, f, phase="phase_1_quick_wins", priority="medium",
                             coverage=0, process={"ai": False}) == AUTOMATION
    assert business_category({"gap_type": "pricing", "competitors_with": []}, f, phase="phase_1_quick_wins",
                             priority="medium", coverage=0) == REVENUE
    assert business_category({"gap_type": "ux", "competitors_with": []}, f, phase="phase_1_quick_wins",
                             priority="medium", coverage=0) == CX
    assert business_category({"gap_type": "security"}, f, phase="phase_1_quick_wins", priority="high",
                             coverage=0) == RISK
