"""Gap verdicts: a competitor offering something does not make it a gap to build."""

from cip.agents.prioritization import gap_verdict

from test_pipeline import run_all

OK = {"passed": True, "detail": ""}
HIGH = {"business_value": 4, "user_impact": 4, "revenue_potential": 4, "cost_saving": 2, "productivity": 3,
        "competitive_gap": 4, "strategic_alignment": 3, "complexity": 3}


def gap(**checks):
    return {"gap_type": "missing", "assessment": {"competitor_verified": OK, "client_status": OK, "relevant": OK,
                                                  **checks}}


def test_six_checks_decide_confirmed_potential_or_optional():
    verdict, checks, risks = gap_verdict(gap(), HIGH, "medium")
    assert verdict == "confirmed" and [c["name"] for c in checks] == [
        "competitor_verified", "client_status", "relevant", "value", "advantage", "effort_justified"]
    assert all(c["passed"] for c in checks) and risks == []

    doubtful = {"passed": False, "detail": "mentioned once on the client's site; may already exist"}
    verdict, _, risks = gap_verdict(gap(client_status=doubtful), HIGH, "medium")
    assert verdict == "potential" and "may already exist" in risks[0]

    low = {**HIGH, "business_value": 1, "user_impact": 1, "revenue_potential": 1, "productivity": 1, "complexity": 5}
    verdict, checks, risks = gap_verdict(gap(), low, "low")
    assert verdict == "optional" and not next(c for c in checks if c["name"] == "effort_justified")["passed"]
    assert "High implementation complexity." in risks


async def test_pipeline_gaps_carry_classification_evidence_and_verdicts(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    gaps = {g["name"]: g for g in ctx.data("gap_analysis")["gaps"]}
    opps = {o["gap_id"]: o for o in ctx.data("opportunity_prioritization")["opportunities"]}

    ai = gaps["AI assistant / chatbot"]
    assert ai["classification"] == "ai_automation" and ai["competitor_evidence"]
    assert ai["assessment"]["competitor_verified"]["passed"] and "MediBook" in ai["assessment"]["competitor_verified"]["detail"]

    process = [g for g in gaps.values() if g["gap_type"] == "process"]
    assert process and all(opps[g["id"]]["verdict"] != "confirmed" for g in process)   # assumptions: never confirmed
    assert all(not g["assessment"]["client_status"]["passed"] for g in process)

    tech = gaps["CI/CD pipeline"]
    assert tech["classification"] == "operational_efficiency"
    for g in gaps.values():
        o = opps[g["id"]]
        assert o["verdict"] in ("confirmed", "potential", "optional") and len(o["checks"]) == 6
        assert o["technical_approach"] and o["complexity"]
    counts = ctx.data("opportunity_prioritization")["verdicts"]
    assert sum(counts.values()) == len(gaps)
