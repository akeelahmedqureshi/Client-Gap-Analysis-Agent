"""Industry & market profile, Top-10 competitor ranking, Top-3 deep analysis, capability classification."""

from cip.agents.competitor_research import CompetitorResearchAgent
from cip.connectors.research.web import WebFetcher
from cip.core.relevance import FACTORS, CandidateSignals, ClientProfile, industry_terms, score_candidate, tokens

from fakes import FakeSearch, sites_with_landscape, web_transport
from test_pipeline import run_all


def _landscape_ctx(make_ctx, **kw):
    return make_ctx(fetcher=WebFetcher(transport=web_transport(sites_with_landscape())),
                    search=FakeSearch(landscape=True), **kw)


async def test_industry_profile_and_sourced_trends(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    im = ctx.data("industry_market")
    assert im["industry"] == "Healthcare" and im["customer_segment"] == ["clinics"]
    assert im["business_model"] == "Subscription (SaaS)" and im["market_segment"]
    assert im["basis"]["customer_segment"] == "inferred"
    kinds = {t["kind"] for t in im["trends"]}
    assert {"ai_adoption", "automation"} <= kinds
    for t in im["trends"]:
        ev = ctx.ledger.get(t["evidence_id"])
        assert ev and ev.source_url == t["source_url"] and t["statement"] in (ev.extracted_text or "")
    # Competitor discovery searches the market, not just the product name.
    assert any("for clinics" in q for q in ctx.search.queries)


async def test_no_trend_search_without_external_research_approval(make_ctx):
    from cip.agents.orchestrator import Orchestrator
    from cip.core.schemas import AgentStatus
    from test_pipeline import MemStore

    ctx = make_ctx(approvals=("repository_access", "client_report"))
    # As the runner does after a rejection: the gated agent is skipped and the run continues.
    assert await Orchestrator(MemStore(), retry_delay=0).run(
        ctx, {"client_research": AgentStatus.SKIPPED}) == "completed"
    im = ctx.data("industry_market")
    assert im["trends"] == [] and not im["searched"] and im["notes"]
    assert not ctx.search.queries
    assert im["industry"] == "Healthcare", "the profile still comes from the client's own CSV data"


async def test_top10_landscape_is_ranked_by_relevance_and_top3_deep_analysed(make_ctx):
    ctx = _landscape_ctx(make_ctx)
    status, _, _ = await run_all(ctx)
    assert status == "completed"
    cr = ctx.data("competitor_research")
    land = cr["landscape"]
    assert [c["rank"] for c in land] == list(range(1, len(land) + 1)) and len(land) == 6
    scores = [c["relevance"]["overall"] for c in land]
    assert scores == sorted(scores, reverse=True)
    for c in land:
        assert set(c["relevance"]["factors"]) == set(FACTORS) and c["reason"]
        assert all(ctx.ledger.get(e) for e in c["evidence_ids"])
    by_name = {c["name"].split()[0]: c for c in land}
    # A salon booking tool shares features but not customers: adjacent, ranked below the clinic tools.
    assert by_name["SlotSmart"]["classification"] == "adjacent"
    assert by_name["SlotSmart"]["rank"] > max(by_name[n]["rank"] for n in ("MediBook", "ClinicFlow", "BookWell"))
    assert "Random News" in {c["name"] for c in cr["rejected"]}

    deep = cr["competitors"]
    assert len(deep) == 3 and all(c["deep"] for c in deep)
    assert [c["rank"] for c in deep] == [1, 2, 3]
    assert {c["name"] for c in deep} == {c["name"] for c in land if c["deep"]}
    # Deep analysis reads pricing and other sub-pages; light verification reads at most two pages.
    assert all(c["pages_analysed"] <= 2 for c in land if not c["deep"])
    assert any(u.endswith("/pricing") for c in deep for u in c["pages_analysed"])
    assert ctx.data("feature_comparison")["competitors"] == [
        {"id": c["id"], "name": c["name"], "classification": c["classification"], "url": c["url"], "rank": c["rank"]}
        for c in deep]


async def test_one_failing_deep_analysis_is_retried_alone(make_ctx, monkeypatch):
    calls: list[str] = []
    real = CompetitorResearchAgent._deep

    async def flaky(self, ctx, comp, client_feats, errors):
        calls.append(comp.name)
        if comp.name == "MediBook":
            raise RuntimeError("site timed out")
        if comp.name == "ClinicFlow" and calls.count("ClinicFlow") == 1:
            raise RuntimeError("transient")
        return await real(self, ctx, comp, client_feats, errors)

    monkeypatch.setattr(CompetitorResearchAgent, "_deep", flaky)
    ctx = _landscape_ctx(make_ctx)
    await run_all(ctx)
    assert calls.count("MediBook") == 2 and calls.count("ClinicFlow") == 2
    assert sum(1 for c in calls if c.startswith("BookWell")) == 1, "a succeeding competitor is never re-run"
    cr = ctx.outputs["competitor_research"]
    medi = next(c for c in cr.data["competitors"] if c["name"] == "MediBook")
    assert medi["deep_error"] == "site timed out" and medi["features"], "falls back to its light profile"
    assert any("MediBook" in e for e in cr.errors)


async def test_capabilities_are_classified_across_the_landscape(make_ctx):
    ctx = _landscape_ctx(make_ctx)
    await run_all(ctx)
    fc = ctx.data("feature_comparison")
    rows = {r["feature_id"]: r for r in fc["rows"]}
    booking = rows["workflow.scheduling"]
    assert booking["top10_total"] == 6 and booking["top3_total"] == 3
    assert booking["market_class"] == "industry_standard" and booking["must_have"]
    assert rows["ai.assistant"]["market_class"] in ("emerging", "differentiator", "niche")
    for r in fc["rows"]:
        assert r["top3_count"] <= r["top3_total"] and r["top10_count"] <= r["top10_total"]
    m = fc["market"]
    assert m["basis"] == "top10" and 0 < m["ai_adoption"] < 1 and m["automation_adoption"] > 0.5
    assert "Online booking / scheduling" in m["industry_standards"]
    gaps = [g for g in ctx.data("gap_analysis")["gaps"] if g.get("feature_id")]
    assert any(g["market_class"] for g in gaps) and all(
        g["landscape_share"] is None or 0 <= g["landscape_share"] <= 1 for g in gaps)


def test_relevance_prefers_fit_over_size():
    client = ClientProfile(features={"workflow.scheduling", "comm.sms", "comm.email", "ux.self_service_portal"},
                           product_words=tokens("patient scheduling software for clinics"),
                           industry_words=industry_terms("Healthcare"), customer_words=tokens("clinics"))
    niche_fit = score_candidate(client, CandidateSignals(
        text="Patient scheduling for clinics with SMS and email reminders and a patient portal.",
        features={"workflow.scheduling", "comm.sms", "comm.email", "ux.self_service_portal"}))
    giant = score_candidate(client, CandidateSignals(
        text="The world's leading CRM for every business.", features={"comm.email"}, marketplace=True,
        search_mentions=10, has_pricing=True, has_docs=True, evidence_count=12))
    assert niche_fit["overall"] > giant["overall"]
    assert "the same customers" in niche_fit["reason"]
    assert {"clinic", "patien", "hipaa"} <= industry_terms("Healthcare")
