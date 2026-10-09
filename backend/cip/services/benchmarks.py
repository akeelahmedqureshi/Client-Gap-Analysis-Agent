"""Industry-wide benchmarking and predicted trends (BRS 33 Phase 3; PRD 10.42).

Built from the organization's own completed analyses (projects the user can see), grouped by industry:

* **Adoption** — for every capability, the share of companies in the industry evidenced to offer it. The
  companies are the analysed clients plus every competitor found for them (deduplicated by domain), each
  counted once with its latest observation. Bands: standard (≥ 60 %), common (≥ 30 %), emerging (< 30 %).
* **Client position** — each client's capability count against the industry (percentile), the industry
  standards it covers and the ones not publicly identified.
* **Predicted trends** — capabilities whose adoption rose between the earlier and the later half of the
  industry's analyses, projected forward if the trend continues. These are estimates from a small, non-random
  sample (``basis: estimate``), and need at least ``MIN_TREND_COMPANIES`` companies in each half.
* **Sourced trends** — industry trends found by the industry & market agent, counted across clients, with
  their sources.

Nothing new is researched; numbers below ``MIN_COMPANIES`` companies are flagged as insufficient.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.connectors.research.web import registrable_domain
from cip.core.taxonomy import load_taxonomy
from cip.db.models import AgentExecution, AnalysisRun, Client, Project, User
from cip.services.access import visible_projects

DONE = ("completed", "completed_with_errors")
HAS = ("available", "partial")
AGENTS = ("product_features", "competitor_research", "industry_market", "client_research")
MIN_COMPANIES = 3
MIN_TREND_COMPANIES = 2
RISING = 0.15


@dataclass
class Company:
    key: str
    name: str
    kind: str  # client | competitor
    capabilities: set[str] = field(default_factory=set)
    seen_at: str = ""
    project_id: str | None = None


def industry_key(name: str | None) -> str:
    return " ".join((name or "").lower().replace("&", "and").split())


def band(share: float) -> str:
    return "standard" if share >= 0.6 else "common" if share >= 0.3 else "emerging"


async def _load(session: AsyncSession, user: User) -> list[dict]:
    """Every completed run of a visible project, oldest first, with the data benchmarking needs."""
    rows = (await session.execute(select(AnalysisRun, Project, Client)
                                  .join(Project, Project.id == AnalysisRun.project_id)
                                  .join(Client, Client.id == Project.client_id)
                                  .where(visible_projects(user), AnalysisRun.status.in_(DONE))
                                  .order_by(AnalysisRun.created_at))).all()
    if not rows:
        return []
    data: dict[str, dict] = defaultdict(dict)
    for e in (await session.execute(select(AgentExecution).where(
            AgentExecution.run_id.in_([r.id for r, _, _ in rows]), AgentExecution.agent.in_(AGENTS),
            AgentExecution.status == "completed"))).scalars():
        data[e.run_id][e.agent] = (e.result or {}).get("data", {})
    out = []
    for run, project, client in rows:
        d = data.get(run.id, {})
        industry = (d.get("industry_market", {}).get("industry")
                    or d.get("client_research", {}).get("profile", {}).get("industry") or client.industry)
        if industry:
            out.append({"run": run, "project": project, "client": client, "data": d, "industry": industry})
    return out


def _companies(runs: list[dict]) -> dict[str, Company]:
    """Latest observation of every company in these runs (runs are oldest first)."""
    companies: dict[str, Company] = {}
    for r in runs:
        d, when = r["data"], r["run"].created_at.isoformat()
        obs = d.get("product_features", {}).get("observations", {})
        ckey = "client:" + (r["client"].domain or r["client"].id)
        companies[ckey] = Company(ckey, r["client"].name, "client",
                                  {f for f, o in obs.items() if o.get("status") in HAS}, when, r["project"].id)
        research = d.get("competitor_research", {})
        deep = {registrable_domain(c.get("url") or "") or c["name"].lower(): c for c in research.get("competitors", [])}
        for c in research.get("landscape", []) or research.get("competitors", []):
            key = registrable_domain(c.get("url") or "") or c["name"].lower()
            caps = set(c.get("feature_ids") or [])
            if key in deep:
                caps |= {o["feature_id"] for o in deep[key].get("features", []) if o.get("status") in HAS}
            companies["comp:" + key] = Company("comp:" + key, c["name"], "competitor", caps, when)
    return companies


def _adoption(companies: list[Company]) -> dict[str, float]:
    if not companies:
        return {}
    counts = Counter(f for c in companies for f in c.capabilities)
    return {f: round(n / len(companies), 3) for f, n in counts.items()}


async def industries(session: AsyncSession, user: User) -> list[dict]:
    runs = await _load(session, user)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        groups[industry_key(r["industry"])].append(r)
    out = []
    for key, rs in groups.items():
        comps = _companies(rs)
        out.append({"key": key, "name": Counter(r["industry"] for r in rs).most_common(1)[0][0],
                    "clients": sum(1 for c in comps.values() if c.kind == "client"),
                    "companies": len(comps), "analyses": len(rs),
                    "sufficient": len(comps) >= MIN_COMPANIES})
    return sorted(out, key=lambda x: (-x["companies"], x["name"]))


async def benchmark(session: AsyncSession, user: User, industry: str) -> dict | None:
    key = industry_key(industry)
    runs = [r for r in await _load(session, user) if industry_key(r["industry"]) == key]
    if not runs:
        return None
    tax = load_taxonomy()
    name = lambda f: tax.get(f).name if tax.get(f) else f  # noqa: E731
    companies = _companies(runs)
    adoption = _adoption(list(companies.values()))
    capabilities = sorted(({"feature_id": f, "name": name(f), "category": tax.get(f).category_name if tax.get(f) else "",
                            "adoption": share, "band": band(share),
                            "companies": round(share * len(companies))} for f, share in adoption.items()),
                          key=lambda x: (-x["adoption"], x["name"]))
    standards = [c["feature_id"] for c in capabilities if c["band"] == "standard"]
    counts = sorted(len(c.capabilities) for c in companies.values())

    def percentile(n: int) -> int:
        return round(100 * sum(1 for x in counts if x <= n) / len(counts))

    clients = []
    for c in companies.values():
        if c.kind != "client":
            continue
        clients.append({"name": c.name, "project_id": c.project_id, "capabilities": len(c.capabilities),
                        "percentile": percentile(len(c.capabilities)),
                        "standards_covered": sum(1 for f in standards if f in c.capabilities),
                        "standards_total": len(standards),
                        "standards_missing": [name(f) for f in standards if f not in c.capabilities]})

    # Predicted trends: adoption in the earlier vs the later half of the industry's analyses.
    trends, trend_note = [], None
    times = sorted({r["run"].created_at for r in runs})
    if len(times) >= 2:
        cut = times[len(times) // 2]
        early = _companies([r for r in runs if r["run"].created_at < cut])
        late = _companies([r for r in runs if r["run"].created_at >= cut])
        if len(early) >= MIN_TREND_COMPANIES and len(late) >= MIN_TREND_COMPANIES:
            a0, a1 = _adoption(list(early.values())), _adoption(list(late.values()))
            for f in set(a0) | set(a1):
                before, after = a0.get(f, 0.0), a1.get(f, 0.0)
                if after - before >= RISING and after >= 0.2:
                    trends.append({"feature_id": f, "name": name(f), "before": before, "after": after,
                                   "projected": round(min(1.0, after + (after - before)), 3),
                                   "ai": bool(tax.get(f) and tax.get(f).ai), "basis": "estimate"})
            trends.sort(key=lambda t: -(t["after"] - t["before"]))
        else:
            trend_note = "Not enough companies in each period to estimate trends."
    else:
        trend_note = "Trends need analyses from at least two points in time."

    sourced: dict[str, dict] = {}
    for r in runs:
        for t in r["data"].get("industry_market", {}).get("trends", []):
            k = (t.get("statement") or "").strip().lower()
            if not k:
                continue
            entry = sourced.setdefault(k, {"text": t["statement"], "kind": t.get("kind"),
                                           "sources": set(), "clients": set()})
            if t.get("source_url"):
                entry["sources"].add(t["source_url"])
            entry["clients"].add(r["client"].id)
    sourced_list = sorted(({"text": v["text"], "kind": v["kind"], "sources": sorted(v["sources"])[:3],
                            "clients": len(v["clients"])} for v in sourced.values()),
                          key=lambda x: (-x["clients"], x["text"]))[:15]

    return {"industry": Counter(r["industry"] for r in runs).most_common(1)[0][0], "key": key,
            "companies": len(companies), "clients": len(clients),
            "competitors": sum(1 for c in companies.values() if c.kind == "competitor"),
            "analyses": len(runs), "sufficient": len(companies) >= MIN_COMPANIES,
            "median_capabilities": counts[len(counts) // 2] if counts else 0, "capability_counts": counts,
            "capabilities": capabilities, "standards": [name(f) for f in standards],
            "client_positions": sorted(clients, key=lambda c: -c["capabilities"]),
            "predicted_trends": trends[:10], "trend_note": trend_note, "sourced_trends": sourced_list,
            "method": "Share of analysed companies (clients and their competitors) evidenced to offer each "
                      "capability; trends compare the earlier and later half of the analyses and are estimates."}


async def for_run(session: AsyncSession, user: User, run: AnalysisRun) -> dict | None:
    """Where this run's client stands in its industry benchmark."""
    runs = await _load(session, user)
    me = next((r for r in runs if r["run"].id == run.id), None)
    if me is None:
        return None
    bench = await benchmark(session, user, me["industry"])
    if bench is None:
        return None
    obs = me["data"].get("product_features", {}).get("observations", {})
    mine = {f for f, o in obs.items() if o.get("status") in HAS}
    by_id = {c["feature_id"]: c for c in bench["capabilities"]}
    missing = [by_id[f] for f in by_id if f not in mine and by_id[f]["band"] in ("standard", "common")]
    return {"industry": bench["industry"], "companies": bench["companies"], "sufficient": bench["sufficient"],
            "capabilities": len(mine), "median_capabilities": bench["median_capabilities"],
            "percentile": round(100 * sum(1 for n in bench["capability_counts"] if n <= len(mine))
                                / max(1, len(bench["capability_counts"]))),
            "missing_common": sorted(missing, key=lambda c: -c["adoption"])[:10],
            "ahead": sorted((by_id[f] for f in mine if f in by_id and by_id[f]["band"] == "emerging"),
                            key=lambda c: c["adoption"])[:10],
            "predicted_trends": bench["predicted_trends"], "trend_note": bench["trend_note"]}
