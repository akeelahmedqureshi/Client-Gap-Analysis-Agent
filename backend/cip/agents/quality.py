"""Quality Assurance agent: evidence, consistency and completeness checks before the report (BRS 32, 41).

Runs after every analysis stage and before the report. It never changes findings; it measures and
flags them, so reviewers and the report can say how much the analysis can be trusted:

* **Data quality** — valid statuses and score ranges, no duplicate capabilities, gaps that reference
  real competitors.
* **Evidence quality** — evidence coverage of significant findings, references that do not resolve,
  low-confidence and inferred/assumption-based findings, source freshness (fresh / aging / stale) and
  conflicting information between sources (CSV vs website, website vs app stores, CSV feature list vs
  public evidence).
* **Recommendation quality** — every recommendation has a business justification, priority, complexity
  and evidence, and none rests only on thin evidence of absence.
* **Output quality** — the sales summary matches the recommendations and the outreach draft passes the
  claim check (no internal-only knowledge).
* **Completeness** (BRS 41) — *complete*, *complete with warnings*, *partial* (a mandatory stage did not
  complete) or *needs review* (a blocking quality issue). The report shows this state prominently.

It also records reproducibility metadata (BRS 31): model, prompt / taxonomy / process-catalog /
scoring versions and the research time window.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from cip import agents as agents_pkg
from cip.agents.base import Agent, RunContext
from cip.core.relevance import industry_terms
from cip.core.schemas import AgentResult, AgentStatus, Basis, Finding

MANDATORY = {
    "client_research": "Client research", "product_features": "Capability inventory",
    "industry_market": "Industry & market analysis", "competitor_research": "Competitor research",
    "feature_comparison": "Feature comparison", "gap_analysis": "Gap analysis",
    "business_process": "Cost & automation analysis", "opportunity_prioritization": "Prioritization",
    "capability_matching": "Capability matching", "sales_intelligence": "Sales intelligence",
    "outreach": "Outreach email",
}
MIN_EVIDENCE_COVERAGE = 0.7
LOW_CONFIDENCE = 0.45
CORE = Path(__file__).resolve().parents[1] / "core"


def _hash(*parts: str) -> str:
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:10]


def prompt_version() -> str:
    """A fingerprint of every LLM prompt in the agents, so a prompt change shows up as a new version."""
    texts = []
    for path in sorted(Path(agents_pkg.__file__).parent.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        texts += [line for line in src.splitlines() if "PROMPT" in line or "You are" in line]
    return _hash(*texts)


def file_version(name: str) -> str:
    p = CORE / name
    return _hash(p.read_text(encoding="utf-8")) if p.exists() else "n/a"


def aware(ts: datetime) -> datetime:
    # Evidence reloaded from SQLite on a resumed run has no timezone; it is stored in UTC.
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def freshness(age_days: float, fresh: int, stale: int) -> str:
    return "fresh" if age_days <= fresh else "aging" if age_days <= stale else "stale"


class QualityAssuranceAgent(Agent):
    name = "quality_assurance"
    description = "Validate evidence, consistency and completeness before the report"
    after = ("outreach", "capability_matching", "enhancement_planning", "business_process", "industry_market",
             "security_review", "ux_review", "app_store", "pricing_analysis")

    async def run(self, ctx: RunContext) -> AgentResult:
        s = ctx.settings
        issues: list[dict] = []  # {severity: blocking|warning|info, area, message}

        def flag(severity: str, area: str, message: str) -> None:
            issues.append({"severity": severity, "area": area, "message": message})

        gaps = ctx.data("gap_analysis").get("gaps", [])
        prio = ctx.data("opportunity_prioritization")
        recs = prio.get("recommendations", [])
        opps = prio.get("opportunities", [])
        comps = ctx.data("competitor_research").get("competitors", [])
        comp_ids = {c["id"] for c in comps}
        rows = ctx.data("feature_comparison").get("rows", [])
        ledger = ctx.ledger

        # --- completeness --------------------------------------------------------------------
        done = {n for n, r in ctx.outputs.items() if r.status == AgentStatus.COMPLETED}
        missing = [label for n, label in MANDATORY.items() if n not in done]
        for label in missing:
            flag("warning", "completeness", f"{label} did not complete; the related sections are incomplete.")

        # --- data quality --------------------------------------------------------------------
        ids = [r["feature_id"] for r in rows]
        if len(ids) != len(set(ids)):
            flag("blocking", "data", "Duplicate capabilities in the comparison matrix.")
        for g in gaps:
            unknown = [c for c in g.get("competitors_with", []) if c not in comp_ids]
            if unknown:
                flag("blocking", "data", f"Gap “{g['name']}” references competitors that are not in the analysis.")
            if not 0 <= g.get("confidence", 0) <= 1:
                flag("blocking", "data", f"Gap “{g['name']}” has an invalid confidence.")
        for o in opps:
            bad = [k for k, v in o.get("factors", {}).items() if not 0 <= v <= 5]
            if bad:
                flag("blocking", "data", f"Opportunity “{o['name']}” has factor scores out of range: {', '.join(bad)}.")

        # --- evidence quality ------------------------------------------------------------------
        significant = ([("gap", g["name"], g.get("evidence_ids", []), g.get("confidence", 0), g.get("basis"))
                        for g in gaps]
                       + [("recommendation", r["feature"], r.get("evidence_ids", []), r.get("confidence", 0.5),
                           r.get("basis")) for r in recs]
                       + [("competitor", c["name"], c.get("evidence_ids", []), c.get("confidence", 0), "evidence")
                          for c in comps])
        with_evidence = [x for x in significant if any(e in ledger for e in x[2])]
        coverage = round(len(with_evidence) / len(significant), 3) if significant else None
        unresolved = sorted({e for x in significant for e in x[2] if e not in ledger})
        if unresolved:
            flag("blocking", "evidence", f"{len(unresolved)} evidence reference(s) do not resolve to a source.")
        if coverage is not None and coverage < MIN_EVIDENCE_COVERAGE:
            flag("warning", "evidence", f"Only {coverage:.0%} of significant findings have supporting evidence "
                                        f"(threshold {MIN_EVIDENCE_COVERAGE:.0%}).")
        unsupported = [x for x in significant if not any(e in ledger for e in x[2])]
        for kind, name, *_ in unsupported[:10]:
            flag("info", "evidence", f"Unsupported {kind}: “{name}” has no linked evidence.")
        low_conf = [x for x in significant if x[3] < LOW_CONFIDENCE]
        inferred = [x for x in significant if x[4] == Basis.INFERRED.value]
        assumed = [x for x in significant if x[4] == Basis.ESTIMATE.value]

        now = datetime.now(timezone.utc)
        ages = {}
        for e in ledger.all():
            ages[e.id] = (now - aware(e.collected_at)).total_seconds() / 86400
        fresh_counts = {"fresh": 0, "aging": 0, "stale": 0}
        for a in ages.values():
            fresh_counts[freshness(a, s.freshness_fresh_days, s.freshness_stale_days)] += 1
        if fresh_counts["stale"]:
            flag("warning", "freshness", f"{fresh_counts['stale']} source(s) are older than {s.freshness_stale_days} days; "
                                         "re-run the analysis before relying on them.")
        source_types: dict[str, int] = {}
        for e in ledger.all():
            source_types[e.source_type] = source_types.get(e.source_type, 0) + 1

        # --- conflicts between sources ----------------------------------------------------------
        conflicts: list[dict] = []
        profile = ctx.data("client_research").get("profile", {})
        csv_ind, web_ind = ctx.record.client.industry, profile.get("industry")
        if csv_ind and web_ind and not (industry_terms(csv_ind) & industry_terms(web_ind)):
            conflicts.append({"topic": "Industry", "detail": f"CSV says “{csv_ind}”; the website says “{web_ind}”.",
                              "evidence_ids": profile.get("evidence_ids", [])[:2]})
        obs = ctx.data("product_features").get("observations", {})
        apps = ctx.data("app_store")
        mobile = obs.get("ux.mobile_app", {})
        if apps.get("enabled") and mobile.get("status") == "available" and not apps.get("client_has_app") \
                and "app_store" in done:
            conflicts.append({"topic": "Mobile app",
                              "detail": "The website mentions a mobile app, but no store listing could be verified.",
                              "evidence_ids": mobile.get("evidence_ids", [])[:2]})
        for item in ctx.record.project.existing_features:
            matched = ctx.taxonomy.match_text(item)
            for fid in matched:
                if obs.get(fid, {}).get("status") not in ("available", "partial"):
                    conflicts.append({"topic": "CSV feature list",
                                      "detail": f"The CSV lists “{item}”, but it is not publicly identified.",
                                      "evidence_ids": []})
        for c in conflicts:
            flag("warning", "conflict", f"{c['topic']}: {c['detail']}")

        # --- recommendation quality -----------------------------------------------------------
        gap_by_id = {g["id"]: g for g in gaps}
        for r in recs:
            problems = [label for label, ok in (
                ("business justification", bool(r.get("business_impact"))), ("priority", bool(r.get("priority"))),
                ("complexity", bool(r.get("complexity"))), ("evidence", any(e in ledger for e in r.get("evidence_ids", []))),
            ) if not ok]
            if problems:
                flag("warning", "recommendations", f"“{r['feature']}” lacks: {', '.join(problems)}.")
            g = gap_by_id.get(r["gap_id"], {})
            if g and not g.get("competitors_with") and g.get("gap_type") in ("missing", "partial") \
                    and g.get("confidence", 0) < LOW_CONFIDENCE:
                flag("warning", "recommendations", f"“{r['feature']}” rests only on thin evidence of absence.")
            if r.get("priority") == "high" and r.get("confidence", 1) < ctx.scoring.high_min_confidence \
                    and not r.get("review"):  # a reviewer's deliberate decision is not a scoring error
                flag("blocking", "recommendations", f"“{r['feature']}” is High priority on low-confidence evidence.")

        # --- output quality -------------------------------------------------------------------
        sales = ctx.data("sales_intelligence")
        rec_ids = {r["id"] for r in recs}
        if sales and not {i["recommendation_id"] for i in sales.get("top_improvements", [])} <= rec_ids:
            flag("blocking", "outputs", "The sales summary does not match the recommendations.")
        outreach = ctx.data("outreach")
        for p in outreach.get("problems", []):
            flag("blocking" if ("internal-only" in p or "security" in p.lower()) else "warning", "outputs",
                 f"Outreach draft: {p}")

        for o in ctx.review:
            if o["kind"] == "gap" and o["value"] == "rework":
                flag("warning", "review", f"A reviewer asked for rework of gap {o['target_id']}: {o['note'] or 'no note'}")
        for event in ctx.usage.events:
            flag("warning", "budget", f"{event}: some steps used their deterministic fallback or skipped requests.")
        blocking = [i for i in issues if i["severity"] == "blocking"]
        warnings = [i for i in issues if i["severity"] == "warning"]
        state = ("needs_review" if blocking else "partial" if missing else
                 "complete_with_warnings" if warnings else "complete")
        timestamps = [aware(e.collected_at) for e in ledger.all()]
        metrics = {
            "evidence_coverage": coverage, "significant_findings": len(significant),
            "findings_with_evidence": len(with_evidence), "unsupported_findings": len(unsupported),
            "low_confidence_findings": len(low_conf), "inferred_findings": len(inferred),
            "assumption_findings": len(assumed), "evidence_items": len(ages), "source_freshness": fresh_counts,
            "source_types": source_types, "conflicts": len(conflicts),
            "competitors_deep": len(comps), "competitors_target": s.deep_competitors,
            "landscape_size": len(ctx.data("competitor_research").get("landscape", [])),
            "comparison_rows": len(rows),
            "comparison_coverage": (round(sum(1 for r in rows if any(v in ("available", "partial")
                                                                     for v in r["competitors"].values())) / len(rows), 3)
                                    if rows else None),
            "stages_completed": sorted(done), "stages_missing": missing,
            "blocking_issues": len(blocking), "warnings": len(warnings),
            "usage": ctx.usage.summary(),
            "manual_overrides": len(ctx.review),
        }
        repro = {
            "model": s.openrouter_model if s.llm_enabled else "none (deterministic pipeline)",
            "llm_temperature": s.llm_temperature if s.llm_enabled else None,
            "prompt_version": prompt_version(), "taxonomy_version": file_version("taxonomy.yaml"),
            "process_catalog_version": file_version("processes.yaml"),
            "scoring_weights": ctx.scoring.weights, "workflow": sorted(ctx.outputs),
            "research_from": min(timestamps).isoformat() if timestamps else None,
            "research_to": max(timestamps).isoformat() if timestamps else None,
            "settings": {"max_competitors": s.max_competitors, "deep_competitors": s.deep_competitors,
                         "crawler_max_pages": s.crawler_max_pages, "roadmap_top_n": s.roadmap_top_n},
        }
        label = {"complete": "Complete", "complete_with_warnings": "Complete with warnings",
                 "partial": "Partially complete", "needs_review": "Needs review"}[state]
        return AgentResult(
            confidence=0.9,
            findings=[Finding(category="quality", title=f"Analysis {label.lower()}",
                              detail=f"{len(blocking)} blocking issue(s), {len(warnings)} warning(s); evidence "
                                     f"coverage {coverage:.0%}." if coverage is not None else label,
                              confidence=0.9, basis=Basis.INFERRED)]
            + [Finding(category=f"quality.{i['area']}", title=i["message"][:200], confidence=0.8,
                       basis=Basis.INFERRED) for i in blocking + warnings],
            data={"state": state, "label": label, "issues": issues, "conflicts": conflicts, "metrics": metrics,
                  "reproducibility": repro,
                  "thresholds": {"min_evidence_coverage": MIN_EVIDENCE_COVERAGE, "low_confidence": LOW_CONFIDENCE,
                                 "fresh_days": s.freshness_fresh_days, "stale_days": s.freshness_stale_days}},
        )

