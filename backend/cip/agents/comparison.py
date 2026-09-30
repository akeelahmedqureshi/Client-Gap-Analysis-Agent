"""Feature Comparison Agent: client vs competitors on the normalized taxonomy."""

from __future__ import annotations

from cip.agents.base import Agent, RunContext
from cip.core.schemas import AgentResult, ComparisonRow, FeatureStatus, Finding

HAS = (FeatureStatus.AVAILABLE, FeatureStatus.PARTIAL)


class FeatureComparisonAgent(Agent):
    name = "feature_comparison"
    description = "Build the client vs competitor feature matrix"
    after = ("product_features", "competitor_research")

    async def run(self, ctx: RunContext) -> AgentResult:
        client_obs = ctx.data("product_features").get("observations", {})
        competitors = ctx.data("competitor_research").get("competitors", [])
        rows: list[ComparisonRow] = []
        for f in ctx.taxonomy.features:
            client_status = FeatureStatus(client_obs.get(f.id, {}).get("status", "unknown"))
            comp_status: dict[str, FeatureStatus] = {}
            for c in competitors:
                ob = next((o for o in c["features"] if o["feature_id"] == f.id), None)
                # Absence on a handful of marketing pages is not proof of absence.
                comp_status[c["id"]] = FeatureStatus(ob["status"]) if ob else FeatureStatus.UNKNOWN
            have = sum(1 for s in comp_status.values() if s in HAS)
            coverage = round(have / len(competitors), 3) if competitors else 0.0
            if client_status in HAS or have:
                rows.append(ComparisonRow(feature_id=f.id, feature_name=f.name, category=f.category_name,
                                          client=client_status, competitors=comp_status,
                                          competitor_coverage=coverage))

        rows.sort(key=lambda r: (r.category, -r.competitor_coverage))
        ahead = [r for r in rows if r.client in HAS and r.competitor_coverage < 0.34 and competitors]
        behind = [r for r in rows if r.client not in HAS and r.competitor_coverage >= 0.5]
        findings = [Finding(category="comparison", title=f"Differentiator: {r.feature_name}",
                            detail=f"Client has it; {r.competitor_coverage:.0%} of competitors evidenced it",
                            confidence=0.6, basis="inferred") for r in ahead]
        findings += [Finding(category="comparison", title=f"Behind market: {r.feature_name}",
                             detail=f"{r.competitor_coverage:.0%} of competitors offer it; client status "
                                    f"{r.client.value}", confidence=0.65, basis="inferred") for r in behind]
        return AgentResult(
            findings=findings,
            confidence=0.75 if competitors else 0.3,
            data={
                "competitors": [{"id": c["id"], "name": c["name"], "classification": c["classification"],
                                 "url": c.get("url")} for c in competitors],
                "rows": [r.model_dump() for r in rows],
            },
        )
