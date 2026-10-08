"""Portfolio & cross-client intelligence (BRS 33, 35; PRD 10.39-10.41).

Built from each visible project's **latest completed run** (the most recent analysis version), so every
number traces back to a run the user can open. Everything is computed from stored agent results; no new
research is done.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.db.models import AgentExecution, AnalysisRun, Client, Project, User
from cip.services.access import visible_projects

DONE = ("completed", "completed_with_errors")
AGENTS = ("opportunity_prioritization", "gap_analysis", "quality_assurance", "industry_market", "app_store",
          "capability_matching", "business_process", "client_research")


async def build(session: AsyncSession, user: User) -> dict:
    rows = (await session.execute(
        select(Project, Client).join(Client, Client.id == Project.client_id).where(visible_projects(user))
    )).all()
    project_ids = [p.id for p, _ in rows]
    runs = (await session.execute(select(AnalysisRun).where(AnalysisRun.project_id.in_(project_ids))
                                  .order_by(AnalysisRun.created_at.desc()))).scalars().all() if project_ids else []
    latest_any: dict[str, AnalysisRun] = {}
    latest_done: dict[str, AnalysisRun] = {}
    status_counts: Counter = Counter()
    for r in runs:
        latest_any.setdefault(r.project_id, r)
        if r.status in DONE:
            latest_done.setdefault(r.project_id, r)
    for r in latest_any.values():
        status_counts[r.status] += 1

    done_ids = [r.id for r in latest_done.values()]
    results: dict[str, dict[str, dict]] = defaultdict(dict)
    if done_ids:
        for e in (await session.execute(select(AgentExecution).where(
                AgentExecution.run_id.in_(done_ids), AgentExecution.agent.in_(AGENTS),
                AgentExecution.status == "completed"))).scalars():
            results[e.run_id][e.agent] = (e.result or {}).get("data", {})

    projects, opportunities = [], []
    gap_projects: dict[str, set] = defaultdict(set)
    gap_type: dict[str, str] = {}
    ai_projects: dict[str, set] = defaultdict(set)
    automation_projects: dict[str, set] = defaultdict(set)
    requests: dict[str, set] = defaultdict(set)
    record_projects: dict[str, dict] = {}
    industries: Counter = Counter()
    needs_review = 0
    for p, c in rows:
        run = latest_done.get(p.id)
        current = latest_any.get(p.id)
        data = results.get(run.id, {}) if run else {}
        prio = data.get("opportunity_prioritization", {})
        recs = sorted(prio.get("recommendations", []), key=lambda r: -r["score"]["total"])
        opps = prio.get("opportunities", [])
        qa = data.get("quality_assurance", {})
        industry = (data.get("industry_market", {}).get("industry")
                    or data.get("client_research", {}).get("profile", {}).get("industry")
                    or c.industry or (p.record or {}).get("client", {}).get("industry"))
        if industry:
            industries[industry] += 1
        if qa.get("state") == "needs_review":
            needs_review += 1
        top = recs[0] if recs else None
        score = round(sum(o.get("normalized_score", 0) for o in sorted(
            opps, key=lambda o: -o.get("normalized_score", 0))[:3]) / 3, 3) if opps else None
        projects.append({
            "project_id": p.id, "project": p.name, "client_id": c.id, "client": c.name, "url": p.url,
            "industry": industry, "status": current.status if current else None,
            "run_id": current.id if current else None, "analysed_run_id": run.id if run else None,
            "quality": qa.get("label"), "quality_state": qa.get("state"),
            "started": current.created_at.isoformat() if current else None,
            "completed": run.updated_at.isoformat() if run else None,
            "top_priority": top["feature"] if top else None, "top_priority_level": top.get("priority") if top else None,
            "high_priority_count": sum(1 for r in recs if r.get("priority") == "high"),
            "opportunity_score": score,
            "evidence_coverage": (qa.get("metrics") or {}).get("evidence_coverage"),
        })
        name = f"{c.name} — {p.name}"
        for r in recs[:5]:
            opportunities.append({"project_id": p.id, "project": name, "run_id": run.id, "feature": r["feature"],
                                  "priority": r.get("priority"), "category": r.get("business_category"),
                                  "score": r["score"]["total"]})
        for g in data.get("gap_analysis", {}).get("gaps", []):
            gap_projects[g["name"]].add(p.id)
            gap_type[g["name"]] = g["gap_type"]
            if g["gap_type"] == "ai":
                ai_projects[g["name"]].add(p.id)
        for o in data.get("business_process", {}).get("opportunities", []):
            (ai_projects if o.get("ai") else automation_projects)[o["name"]].add(p.id)
        for rq in data.get("app_store", {}).get("requests", []):
            requests[rq["name"]].add(p.id)
        for rec in data.get("capability_matching", {}).get("by_record", []):
            entry = record_projects.setdefault(rec["record_id"], {"record_id": rec["record_id"], "title": rec["title"],
                                                                  "kind": rec["kind"], "client_facing": rec["client_facing"],
                                                                  "projects": set(), "needs": Counter()})
            entry["projects"].add(p.id)
            entry["needs"].update(rec.get("needs", []))

    def ranked(groups: dict[str, set], n: int = 10, extra=None) -> list[dict]:
        out = [{"name": k, "projects": len(v), **(extra(k) if extra else {})} for k, v in groups.items()]
        return sorted(out, key=lambda x: (-x["projects"], x["name"]))[:n]

    demand = sorted(({**{k: v for k, v in e.items() if k not in ("projects", "needs")}, "projects": len(e["projects"]),
                      "top_needs": [n for n, _ in e["needs"].most_common(3)]} for e in record_projects.values()),
                    key=lambda x: (-x["projects"], x["title"]))
    return {
        "summary": {
            "clients": len({c.id for _, c in rows}), "projects": len(rows),
            "analysed": len(latest_done), "running": status_counts["running"] + status_counts["queued"],
            "awaiting_approval": status_counts["awaiting_approval"], "failed": status_counts["failed"],
            "needs_review": needs_review, "never_analysed": len(rows) - len(latest_any),
        },
        "projects": projects,
        "top_opportunities": sorted(opportunities, key=lambda o: -o["score"])[:10],
        "recurring_gaps": ranked(gap_projects, extra=lambda k: {"type": gap_type.get(k)}),
        "recurring_ai": ranked(ai_projects),
        "recurring_automation": ranked(automation_projects),
        "requested_capabilities": ranked(requests),
        "industries": [{"name": k, "projects": v} for k, v in industries.most_common(10)],
        "capability_demand": demand[:10],
        "shared_case_studies": [d for d in demand if d["kind"] == "case_study" and d["projects"] >= 2],
    }


def filter_projects(items: list[dict], *, q: str | None = None, industry: str | None = None, status: str | None = None,
                    priority: str | None = None, sort: str = "score") -> list[dict]:
    def keep(p: dict) -> bool:
        if q and q.lower() not in " ".join(str(p.get(k) or "") for k in ("project", "client", "industry", "url",
                                                                           "top_priority")).lower():
            return False
        if industry and (p.get("industry") or "").lower() != industry.lower():
            return False
        if status and p.get("status") != status and p.get("quality_state") != status:
            return False
        if priority and not (p.get("top_priority_level") == priority or (priority == "high" and p["high_priority_count"])):
            return False
        return True

    kept = [p for p in items if keep(p)]
    if sort == "date":
        return sorted(kept, key=lambda p: p.get("completed") or "", reverse=True)
    if sort == "name":
        return sorted(kept, key=lambda p: (p["client"].lower(), p["project"].lower()))
    if sort == "confidence":
        return sorted(kept, key=lambda p: -(p.get("evidence_coverage") or -1))
    return sorted(kept, key=lambda p: -(p.get("opportunity_score") or -1))
