"""Cross-tab consistency: the tabs read one dataset; contradictions are reported with the stages to re-run."""

import copy

from cip.core.consistency import run_checks

from test_pipeline import run_all


async def test_a_full_analysis_is_consistent_across_tabs(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    checks = ctx.data("quality_assurance")["consistency"]
    assert len(checks) == 7 and all(c["passed"] for c in checks), [c for c in checks if not c["passed"]]
    # The app-store listing makes "Native mobile app" available for that competitor in the comparison too.
    rows = {r["feature_id"]: r for r in ctx.data("feature_comparison")["rows"]}
    cf = next(c["id"] for c in ctx.data("competitor_research")["competitors"] if c["name"] == "ClinicFlow")
    assert rows["ux.mobile_app"]["competitors"][cf] == "available"


async def test_contradictions_are_reported_with_the_stages_to_rerun(make_ctx):
    ctx = make_ctx()
    await run_all(ctx)
    data = {name: copy.deepcopy(ctx.data(name)) for name in ctx.outputs}
    rows = data["feature_comparison"]["rows"]
    gaps = data["gap_analysis"]["gaps"]
    # A stale comparison: the client column no longer matches the inventory.
    row = next(r for r in rows if r["client"] == "available")
    row["client"] = "unknown"
    # A gap for a capability the client has.
    have = next(f for f, o in data["product_features"]["observations"].items() if o["status"] == "available")
    gaps.append({**gaps[0], "id": "gap_x", "name": "Contradiction", "feature_id": have, "gap_type": "missing",
                 "competitors_with": []})
    # The sales summary cites a gap that no longer exists.
    data["sales_intelligence"]["top_gaps"] = [{"gap_id": "gap_gone", "name": "Removed gap"}]
    # Industry drift.
    data["industry_market"]["industry"] = "Mining"
    data["industry_market"]["basis"] = {"industry": "evidence"}

    checks = {c["name"]: c for c in run_checks(lambda n: data.get(n, {}), "ABC Healthcare")}
    failed = {n for n, c in checks.items() if not c["passed"]}
    assert failed == {"comparison_client", "gaps_vs_client", "sales_vs_gaps", "market_vs_profile"}
    assert "feature_comparison" in checks["comparison_client"]["rerun"]
    assert checks["sales_vs_gaps"]["items"] == ["Removed gap"]
    assert "Contradiction" in checks["gaps_vs_client"]["items"][0]
