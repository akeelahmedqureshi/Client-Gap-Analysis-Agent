"""Cross-tab consistency: every tab reads one structured dataset, and these checks prove the tabs agree.

Each check compares two stages' outputs for the same fact (a capability's status, a gap's competitors, a price,
the client's industry). A contradiction is never silently kept: it is reported with the items involved and the
stages to re-run (partial re-run) so the affected results are rebuilt from the current evidence.
"""

from __future__ import annotations

from collections.abc import Callable

from cip.core import capabilities
from cip.core.relevance import industry_terms

HAS = ("available", "partial")


def _check(name: str, label: str, items: list[str], rerun: list[str], ok_detail: str, bad_detail: str) -> dict:
    return {"name": name, "label": label, "passed": not items, "items": items[:20],
            "detail": ok_detail if not items else f"{bad_detail} ({len(items)})", "rerun": [] if not items else rerun}


def run_checks(data: Callable[[str], dict], client_name: str) -> list[dict]:
    """``data(agent)`` returns that agent's output data (empty dict when it did not run)."""
    apps = data("app_store")
    features = capabilities.client_observations(data("product_features").get("observations", {}), apps)
    comps = {c["id"]: {**c, "features": capabilities.competitor_observations(c, apps)}
             for c in data("competitor_research").get("competitors", [])}
    rows = data("feature_comparison").get("rows", [])
    gaps = data("gap_analysis").get("gaps", [])
    out = []

    # 1. The comparison's client column = the Products & Services inventory.
    bad = [f"{r['feature_name']}: comparison says {r['client']}, inventory says {features[r['feature_id']]['status']}"
           for r in rows if r["feature_id"] in features and r["client"] != features[r["feature_id"]]["status"]]
    out.append(_check("comparison_client", "Comparison matches the client's capability inventory", bad,
                      ["feature_comparison", "gap_analysis", "opportunity_prioritization"],
                      "every client status matches", "client statuses differ from Products & Services"))

    # 2. The comparison's competitor columns = the Competitors tab.
    bad = []
    for r in rows:
        for cid, status in r.get("competitors", {}).items():
            ob = next((o for o in comps.get(cid, {}).get("features", []) if o["feature_id"] == r["feature_id"]), None)
            if cid in comps and status != (ob or {}).get("status", "unknown"):
                bad.append(f"{r['feature_name']} / {comps[cid]['name']}: comparison {status}, profile "
                           f"{(ob or {}).get('status', 'unknown')}")
    out.append(_check("comparison_competitors", "Comparison matches the competitor profiles", bad,
                      ["feature_comparison", "gap_analysis", "opportunity_prioritization"],
                      "every competitor status matches", "competitor statuses differ from the Competitors tab"))

    # 3. No gap for a capability the client is shown to have.
    # (a full capability can't be a gap; a partial one can only be a "partial" gap)
    bad = [f"{g['name']}: the client's inventory shows it {features[g['feature_id']]['status']}"
           for g in gaps if g.get("feature_id") in features and (
               (features[g["feature_id"]]["status"] == "available" and g["gap_type"] in ("missing", "ai", "partial"))
               or (features[g["feature_id"]]["status"] == "partial" and g["gap_type"] == "missing"))]
    out.append(_check("gaps_vs_client", "Gaps do not contradict the client's capabilities", bad,
                      ["gap_analysis", "opportunity_prioritization", "sales_intelligence"],
                      "no gap names a capability the client has", "gaps name capabilities the client has"))

    # 4. A competitive gap's competitors offer it in the comparison.
    row_by = {r["feature_id"]: r for r in rows}
    bad = []
    for g in gaps:
        r = row_by.get(g.get("feature_id") or "")
        if r and g.get("competitors_with"):
            wrong = [comps.get(c, {}).get("name", c) for c in g["competitors_with"] if r["competitors"].get(c) not in HAS]
            if wrong:
                bad.append(f"{g['name']}: {', '.join(wrong)} not shown offering it in the comparison")
    out.append(_check("gaps_vs_comparison", "Gaps cite competitors the comparison shows offering the capability", bad,
                      ["gap_analysis", "opportunity_prioritization"], "every cited competitor offers it",
                      "gaps cite competitors without that capability"))

    # 5. The sales summary talks about gaps that exist.
    gap_ids = {g["id"] for g in gaps}
    sales = data("sales_intelligence")
    bad = [g["name"] for g in sales.get("top_gaps", []) if g.get("gap_id") and g["gap_id"] not in gap_ids]
    out.append(_check("sales_vs_gaps", "Sales summary uses the current gaps", bad,
                      ["sales_intelligence", "outreach"], "the sales summary uses current gaps",
                      "the sales summary cites gaps that are no longer in the analysis"))

    # 6. Pricing: the two readings of a competitor's prices agree.
    bad = [f"{c['name']} — {x['plan']}: {x['detail']}" for c in comps.values()
           for x in (c.get("pricing_detail") or {}).get("conflicts", [])]
    out.append(_check("pricing_readings", "Pricing readings agree", bad, ["competitor_research", "pricing_analysis"],
                      "no conflicting price readings", "plans were read with different prices"))

    # 7. The market analysis is about the client's industry.
    profile = data("client_research").get("profile", {})
    market = data("industry_market")
    a, b = profile.get("industry"), market.get("industry")
    bad = []
    if a and b and (market.get("basis") or {}).get("industry") == "evidence" and not (industry_terms(a) & industry_terms(b)):
        bad = [f"client profile: {a}; market analysis: {b}"]
    out.append(_check("market_vs_profile", f"Market analysis matches {client_name}'s industry", bad,
                      ["industry_market", "competitor_research"], "the same industry throughout",
                      "the market analysis is about a different industry"))
    return out
