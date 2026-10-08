"""Feature Comparison Agent: client vs competitors on the normalized taxonomy (BRS 7.9, 7.11).

The matrix compares the client with the deep-analysed Top-3 competitors. Each capability also gets its
frequency in the Top-3 and in the Top-10 landscape, and a market classification:

* **industry standard** (must-have): offered by at least 60% of the landscape;
* **emerging expectation**: 30-60%, or an AI capability already offered by 20% or more;
* **differentiator**: offered by a few competitors (under 30%, at least two);
* **niche**: offered by a single competitor;
* **unique to client**: offered by the client but no analysed competitor.

Shares use the Top-10 landscape when it has at least four competitors, otherwise the deep-analysed set.
``market`` summarises adoption across the landscape: AI, automation and the share of industry
standards the client offers publicly.
"""

from __future__ import annotations

from cip.agents.base import Agent, RunContext
from cip.core.categories import AUTOMATION_FEATURES
from cip.core.schemas import AgentResult, ComparisonRow, FeatureStatus, Finding

HAS = (FeatureStatus.AVAILABLE, FeatureStatus.PARTIAL)
MIN_LANDSCAPE = 4


def market_class(share: float, count: int, *, ai: bool, client_has: bool) -> str:
    if count == 0:
        return "unique_to_client" if client_has else "niche"
    if share >= 0.6:
        return "industry_standard"
    if share >= 0.3 or (ai and share >= 0.2):
        return "emerging"
    return "niche" if count == 1 else "differentiator"


class FeatureComparisonAgent(Agent):
    name = "feature_comparison"
    description = "Build the client vs competitor feature matrix and classify market capabilities"
    after = ("product_features", "competitor_research")

    async def run(self, ctx: RunContext) -> AgentResult:
        client_obs = ctx.data("product_features").get("observations", {})
        research = ctx.data("competitor_research")
        competitors = research.get("competitors", [])
        deep_ids = {c["id"] for c in competitors}
        landscape = research.get("landscape", [])
        # Feature sets across the landscape (deep competitors use their full deep-analysis features).
        deep_feats = {c["id"]: {o["feature_id"] for o in c["features"] if o["status"] in ("available", "partial")}
                      for c in competitors}
        land_feats = [deep_feats.get(c["id"], set(c.get("feature_ids", []))) for c in landscape] or list(deep_feats.values())
        use_landscape = len(land_feats) >= MIN_LANDSCAPE
        rows: list[ComparisonRow] = []
        for f in ctx.taxonomy.features:
            client_status = FeatureStatus(client_obs.get(f.id, {}).get("status", "unknown"))
            comp_status: dict[str, FeatureStatus] = {}
            cell_evidence = {"client": list(client_obs.get(f.id, {}).get("evidence_ids", []))[:8]}
            for c in competitors:
                ob = next((o for o in c["features"] if o["feature_id"] == f.id), None)
                # Absence on a handful of marketing pages is not proof of absence.
                comp_status[c["id"]] = FeatureStatus(ob["status"]) if ob else FeatureStatus.UNKNOWN
                if ob and ob.get("evidence_ids"):
                    cell_evidence[c["id"]] = list(ob["evidence_ids"])[:8]
            have = sum(1 for s in comp_status.values() if s in HAS)
            coverage = round(have / len(competitors), 3) if competitors else 0.0
            top10 = sum(1 for feats in land_feats if f.id in feats)
            share = top10 / len(land_feats) if use_landscape else coverage
            count = top10 if use_landscape else have
            cls = market_class(share, count, ai=f.ai, client_has=client_status in HAS)
            if client_status in HAS or have or top10:
                rows.append(ComparisonRow(
                    feature_id=f.id, feature_name=f.name, category=f.category_name, client=client_status,
                    competitors=comp_status, competitor_coverage=coverage, top3_count=have,
                    top3_total=len(deep_ids), top10_count=top10, top10_total=len(land_feats),
                    market_class=cls, must_have=cls == "industry_standard",
                    evidence={k: ctx.ledger.validate_refs(v) for k, v in cell_evidence.items() if v}))

        rows.sort(key=lambda r: (r.category, -r.competitor_coverage))
        ahead = [r for r in rows if r.client in HAS and r.competitor_coverage < 0.34 and competitors]
        behind = [r for r in rows if r.client not in HAS and r.competitor_coverage >= 0.5]
        findings = [Finding(category="comparison", title=f"Differentiator: {r.feature_name}",
                            detail=f"Client has it; {r.competitor_coverage:.0%} of competitors evidenced it",
                            confidence=0.6, basis="inferred") for r in ahead]
        findings += [Finding(category="comparison", title=f"Behind market: {r.feature_name}",
                             detail=f"{r.competitor_coverage:.0%} of competitors offer it; client status "
                                    f"{r.client.value}", confidence=0.65, basis="inferred") for r in behind]

        n = len(land_feats)
        ai_ids = {f.id for f in ctx.taxonomy.features if f.ai}
        standards = [r for r in rows if r.must_have]
        market = {
            "landscape_size": n, "deep_size": len(competitors), "basis": "top10" if use_landscape else "top3",
            "ai_adoption": round(sum(1 for fs in land_feats if fs & ai_ids) / n, 3) if n else None,
            "automation_adoption": round(sum(1 for fs in land_feats if fs & AUTOMATION_FEATURES) / n, 3) if n else None,
            "industry_standards": [r.feature_name for r in standards],
            "client_standard_coverage": (round(sum(1 for r in standards if r.client in HAS) / len(standards), 3)
                                         if standards else None),
            "emerging": [r.feature_name for r in rows if r.market_class == "emerging"],
            "differentiators": [r.feature_name for r in rows if r.market_class in ("differentiator", "niche")],
            "unique_to_client": [r.feature_name for r in rows if r.market_class == "unique_to_client"],
        }
        missing_standards = [r for r in standards if r.client not in HAS]
        if missing_standards:
            findings.append(Finding(
                category="comparison", title="Industry standards not publicly identified for the client",
                detail=", ".join(r.feature_name for r in missing_standards), confidence=0.65, basis="inferred"))
        return AgentResult(
            findings=findings,
            confidence=0.75 if competitors else 0.3,
            data={
                "competitors": [{"id": c["id"], "name": c["name"], "classification": c["classification"],
                                 "url": c.get("url"), "rank": c.get("rank")} for c in competitors],
                "rows": [r.model_dump() for r in rows],
                "market": market,
            },
        )
