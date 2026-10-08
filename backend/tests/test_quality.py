"""Quality assurance, completeness and the 15-section report (BRS 7.23, 28, 31, 32, 41)."""

from datetime import datetime, timedelta, timezone

from cip.agents.orchestrator import Orchestrator
from cip.agents.quality import QualityAssuranceAgent
from cip.agents.reporting import CONTENTS
from cip.core.schemas import AgentResult, AgentStatus, Evidence

from fakes import sites_with_client_app, web_transport
from test_pipeline import MemStore, run_all


async def test_complete_analysis_passes_quality_checks(make_ctx):
    ctx = make_ctx()
    status, _, _ = await run_all(ctx)
    assert status == "completed"
    q = ctx.data("quality_assurance")
    assert q["state"] == "complete" and not [i for i in q["issues"] if i["severity"] == "blocking"]
    m = q["metrics"]
    assert m["evidence_coverage"] == 1.0 and m["unsupported_findings"] == 0
    assert m["source_freshness"]["fresh"] == m["evidence_items"] > 0
    assert m["inferred_findings"] + m["assumption_findings"] > 0 and m["stages_missing"] == []
    r = q["reproducibility"]
    assert r["model"].startswith("none") and len(r["prompt_version"]) == 10 and r["taxonomy_version"] != "n/a"
    assert r["research_from"] <= r["research_to"] and "quality_assurance" not in r["workflow"]


async def test_rejected_research_makes_the_report_partial(make_ctx):
    ctx = make_ctx(approvals=("repository_access", "client_report"))
    assert await Orchestrator(MemStore(), retry_delay=0).run(
        ctx, {"client_research": AgentStatus.SKIPPED}) == "completed"
    q = ctx.data("quality_assurance")
    assert q["state"] == "partial" and "Client research" in q["metrics"]["stages_missing"]
    md = ctx.data("report")["markdown"]
    assert "Analysis status: Partially complete" in md and "Client research did not complete" in md


async def test_blocking_issues_mean_needs_review(make_ctx):
    ctx = make_ctx()
    ctx.outputs["gap_analysis"] = AgentResult(data={"gaps": [
        {"id": "gap_1", "name": "SMS", "gap_type": "missing", "description": "x", "competitors_with": ["cmp_ghost"],
         "evidence_ids": ["ev_missing"], "confidence": 0.6, "basis": "inferred"}]})
    ctx.outputs["opportunity_prioritization"] = AgentResult(data={
        "opportunities": [{"gap_id": "gap_1", "name": "SMS", "factors": {"business_value": 7}}],
        "recommendations": [{"id": "rec_1", "gap_id": "gap_1", "feature": "SMS", "business_impact": "",
                             "priority": "high", "confidence": 0.3, "complexity": "low", "evidence_ids": []}]})
    ctx.outputs["outreach"] = AgentResult(data={"problems": ["Mentions internal-only or non-referenceable information: “X”"]})
    q = (await QualityAssuranceAgent().run(ctx)).data
    assert q["state"] == "needs_review"
    text = " | ".join(i["message"] for i in q["issues"] if i["severity"] == "blocking")
    for expected in ("not in the analysis", "out of range", "do not resolve", "High priority on low-confidence",
                     "Outreach draft"):
        assert expected in text
    warn = " | ".join(i["message"] for i in q["issues"] if i["severity"] == "warning")
    assert "lacks: business justification" in warn


async def test_conflicts_and_stale_sources_are_flagged(make_ctx, sample_record):
    record = sample_record.model_copy(deep=True)
    record.client.industry = "Logistics"
    record.project.existing_features = ["Two-factor authentication"]
    ctx = make_ctx(record=record)
    ctx.outputs["client_research"] = AgentResult(data={"profile": {"industry": "Healthcare", "evidence_ids": []}})
    ctx.outputs["product_features"] = AgentResult(data={"observations": {
        "ux.mobile_app": {"status": "available", "evidence_ids": []}}})
    ctx.outputs["app_store"] = AgentResult(data={"enabled": True, "client_has_app": False})
    old = Evidence(claim="Old pricing page", source_url="https://abc-healthcare.com/pricing", source_type="website",
                   confidence=0.8, collected_at=datetime.now(timezone.utc) - timedelta(days=400))
    ctx.ledger.add(old.claim, old.source_url, "website", 0.8)
    ctx.ledger.all()[-1].collected_at = old.collected_at.replace(tzinfo=None)  # as reloaded from SQLite
    q = (await QualityAssuranceAgent().run(ctx)).data
    topics = {c["topic"] for c in q["conflicts"]}
    assert {"Industry", "Mobile app", "CSV feature list"} <= topics
    assert q["metrics"]["source_freshness"]["stale"] == 1
    assert any("older than" in i["message"] for i in q["issues"])
    assert q["state"] in ("partial", "complete_with_warnings")


async def test_report_follows_the_brs_structure(make_ctx):
    from cip.connectors.research.web import WebFetcher

    ctx = make_ctx(fetcher=WebFetcher(transport=web_transport(sites_with_client_app())))
    await run_all(ctx)
    md = ctx.data("report")["markdown"]
    sections = {title: md.index(f"## {i}. {title}") for i, title in enumerate(CONTENTS, 1)}
    body = lambda title: md[sections[title]:md.index("\n## ", sections[title] + 5)]  # noqa: E731
    assert "| Gap | Category | Priority |" in body("Feature Gap Analysis")
    assert "(assumption)" in body("Business Cost-Reduction Opportunities")
    assert "### AI opportunities" in body("AI & Automation Opportunities")
    assert "### High priority" in body("Prioritized Recommendations")
    assert "Short term" in body("Strategic Roadmap") and "Long term" in body("Strategic Roadmap")
    assert "| Cost |" in body("Business Impact Summary")
    assert "Industry standard (must-have)" in body("Common Competitor Features")
    assert "| Dimension | Value | Basis |" in body("Industry & Market Analysis")
    assert "Reproducibility" in md[md.index("## Appendix B."):]
    sections_json = ctx.data("report")["report"]["sections"]
    assert sections_json["completeness"]["state"] == "complete" and sections_json["business_impact"]
