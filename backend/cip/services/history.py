"""Historical competitor benchmarking for a project (BRS 33; PRD 10.42).

Built from the project's completed analyses, oldest first: which competitors appeared, their rank and
how many capabilities each was evidenced to offer, next to the client's own capability count and its
coverage of industry-standard capabilities. Competitors are matched across runs by domain.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.connectors.research.web import registrable_domain
from cip.db.models import AgentExecution, AnalysisRun

DONE = ("completed", "completed_with_errors")
HAS = ("available", "partial")
MAX_RUNS = 20
AGENTS = ("competitor_research", "feature_comparison", "product_features", "pricing_analysis")


def _key(c: dict) -> str:
    return registrable_domain(c.get("url") or "") or (c.get("name") or "").lower()


async def competitor_history(session: AsyncSession, project_id: str) -> dict:
    runs = list((await session.execute(select(AnalysisRun).where(
        AnalysisRun.project_id == project_id, AnalysisRun.status.in_(DONE))
        .order_by(AnalysisRun.created_at.desc()).limit(MAX_RUNS))).scalars())[::-1]
    results: dict[str, dict[str, dict]] = {r.id: {} for r in runs}
    if runs:
        for e in (await session.execute(select(AgentExecution).where(
                AgentExecution.run_id.in_(list(results)), AgentExecution.agent.in_(AGENTS),
                AgentExecution.status == "completed"))).scalars():
            results[e.run_id][e.agent] = (e.result or {}).get("data", {})

    snapshots, series = [], {}
    for r in runs:
        data = results[r.id]
        research = data.get("competitor_research", {})
        deep = {_key(c): c for c in research.get("competitors", [])}
        entries = research.get("landscape") or [
            {**c, "feature_ids": [o["feature_id"] for o in c.get("features", []) if o["status"] in HAS]}
            for c in research.get("competitors", [])]
        rows = data.get("feature_comparison", {}).get("rows", [])
        standards = [row for row in rows if row.get("must_have")]
        client_obs = data.get("product_features", {}).get("observations", {})
        client_count = sum(1 for o in client_obs.values() if o.get("status") in HAS)
        snap = {"run_id": r.id, "date": r.created_at.isoformat(), "client_capabilities": client_count,
                "standards_total": len(standards),
                "standards_covered": sum(1 for row in standards if row["client"] in HAS), "competitors": []}
        for c in entries:
            key = _key(c)
            features = len(c.get("feature_ids") or [])
            if key in deep:
                features = max(features, sum(1 for o in deep[key].get("features", []) if o["status"] in HAS))
            point = {"key": key, "name": c.get("name"), "rank": c.get("rank"), "relevance": c.get("relevance"),
                     "deep": key in deep, "features": features}
            snap["competitors"].append(point)
            series.setdefault(key, {"key": key, "name": c.get("name"), "points": {}})["points"][r.id] = point
        snapshots.append(snap)

    run_ids = [s["run_id"] for s in snapshots]
    competitors = []
    for key, s in series.items():
        present = [rid for rid in run_ids if rid in s["points"]]
        first, last = s["points"][present[0]], s["points"][present[-1]]
        competitors.append({
            "key": key, "name": s["name"], "appearances": len(present), "first_seen": present[0],
            "last_seen": present[-1], "current": present[-1] == (run_ids[-1] if run_ids else None),
            "new": len(run_ids) > 1 and present == [run_ids[-1]],
            "dropped": bool(run_ids) and present[-1] != run_ids[-1],
            "feature_change": last["features"] - first["features"],
            "rank_change": (first["rank"] - last["rank"]) if first.get("rank") and last.get("rank") else None,
            "points": [s["points"].get(rid) for rid in run_ids],
        })
    competitors.sort(key=lambda c: (not c["current"], (c["points"][-1] or {}).get("rank") or 99, c["name"] or ""))
    return {"runs": [{k: v for k, v in s.items() if k != "competitors"} for s in snapshots],
            "competitors": competitors}
