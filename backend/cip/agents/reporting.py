"""Report Generation Agent: assembles the final intelligence report.

The report is built from structured agent outputs only. Every claim carries
citation markers ``[E#]`` that resolve to the evidence appendix; AI-generated
estimates are explicitly labelled.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from cip.agents.base import Agent, ApprovalRequest, RunContext
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.schemas import AgentResult
from cip.core.scoring import PHASE_LABELS

STATUS_ICON = {"available": "✅", "partial": "🟡", "missing": "❌", "unknown": "·"}


class _LLMSummary(BaseModel):
    current_position: str
    key_findings: list[str]
    major_opportunities: list[str]


class Citations:
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx
        self.order: dict[str, int] = {}

    def __call__(self, ids: list[str] | None, limit: int = 3) -> str:
        marks = []
        for i in (ids or [])[:limit]:
            if i not in self.ctx.ledger:
                continue
            if i not in self.order:
                self.order[i] = len(self.order) + 1
            marks.append(f"E{self.order[i]}")
        return f" [{', '.join(marks)}]" if marks else ""

    def appendix(self) -> list[dict]:
        out = []
        for eid, n in sorted(self.order.items(), key=lambda kv: kv[1]):
            e = self.ctx.ledger.get(eid)
            out.append({"ref": f"E{n}", **e.model_dump(mode="json")})
        return out


def build_report(ctx: RunContext, summary: _LLMSummary | None) -> dict:
    cite = Citations(ctx)
    rec = ctx.record
    research = ctx.data("client_research")
    profile = research.get("profile", {})
    features = ctx.data("product_features")
    profiles = ctx.data("code_analysis").get("profiles", [])
    comp = ctx.data("competitor_research").get("competitors", [])
    comparison = ctx.data("feature_comparison")
    gaps = ctx.data("gap_analysis").get("gaps", [])
    prio = ctx.data("opportunity_prioritization")
    plans = ctx.data("enhancement_planning").get("plans", [])

    sections: dict = {}
    top_recs = prio.get("recommendations", [])[:5]
    sections["executive_summary"] = {
        "client": rec.client.name, "project": rec.project.name,
        "industry": profile.get("industry") or rec.client.industry,
        "current_position": summary.current_position if summary else
        (f"{rec.project.name} evidences {sum(1 for f in features.get('inventory', []) if f['status'] == 'available')} "
         f"features against {len(comp)} verified competitor(s); {len(gaps)} gaps identified."),
        "key_findings": summary.key_findings if summary else
        [f"{g['name']}: {g['description']}" for g in gaps[:5]],
        "major_opportunities": summary.major_opportunities if summary else
        [f"{r['feature']} — {PHASE_LABELS[r['phase']]}" for r in top_recs],
        "ai_generated": summary is not None,
    }
    sections["client_intelligence"] = {
        "company": {k: profile.get(k) for k in ("name", "domain", "description", "industry", "headquarters",
                                                "founded_year", "company_size", "business_model")},
        "company_citations": cite(profile.get("evidence_ids"), 5),
        "locations": profile.get("locations", []),
        "target_customers": profile.get("target_customers", []),
        "products": [{**p, "cite": cite(p.get("evidence_ids"))} for p in profile.get("products", [])],
        "leadership": [{**p, "cite": cite(p.get("evidence_ids"))} for p in research.get("leadership", [])],
        "contacts": profile.get("contacts", []),
    }
    sections["project_analysis"] = {
        "features": [{**f, "cite": cite(f.get("evidence_ids"))} for f in features.get("inventory", [])],
        "coverage": features.get("coverage", {}),
        "repositories": [{
            "full_name": p["full_name"], "url": p["url"], "architecture": p.get("architecture", []),
            "summary": p.get("summary"),
            "technologies": [{**t, "cite": cite(t.get("evidence_ids"), 1)} for t in p.get("technologies", [])],
            "has_tests": p.get("has_tests"), "has_ci": p.get("has_ci"), "has_docker": p.get("has_docker"),
            "technical_debt": p.get("technical_debt_indicators", []),
        } for p in profiles],
    }
    sections["market_analysis"] = {
        "competitors": [{"name": c["name"], "url": c.get("url"), "classification": c["classification"],
                         "description": c.get("description"), "pricing": c.get("pricing"),
                         "target_market": c.get("target_market"), "rationale": c.get("rationale"),
                         "cite": cite(c.get("evidence_ids"))} for c in comp],
        "rejected_candidates": [{"name": c["name"], "url": c.get("url"), "reason": c.get("rationale")}
                                for c in ctx.data("competitor_research").get("rejected", [])],
    }
    sections["feature_comparison"] = comparison
    sections["gap_analysis"] = [{**g, "cite": cite(g.get("evidence_ids"))} for g in gaps]
    sections["opportunities"] = prio.get("opportunities", [])
    sections["scoring_weights"] = prio.get("scoring_weights", {})
    sections["roadmap"] = {
        phase: [{**r, "cite": cite(r.get("evidence_ids"))} for r in prio.get("recommendations", [])
                if r["phase"] == phase] for phase in PHASE_LABELS
    }
    sections["patch_plans"] = plans
    sections["evidence_appendix"] = cite.appendix()
    return {
        "title": f"Client Intelligence Report — {rec.client.name}: {rec.project.name}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": ctx.run_id,
        "sections": sections,
    }


def _md_list(items: list[str]) -> str:
    # Leading blank line: strict Markdown (and the PDF renderer) needs one between a paragraph and a list.
    return "\n" + ("\n".join(f"- {i}" for i in items if i) or "- _None identified_")


def render_markdown(report: dict) -> str:
    s = report["sections"]
    es = s["executive_summary"]
    ci = s["client_intelligence"]
    pa = s["project_analysis"]
    ma = s["market_analysis"]
    out: list[str] = [f"# {report['title']}", f"_Generated {report['generated_at']} · run `{report['run_id']}`_", ""]
    out += ["> **Legend** — `[E#]` cites the Evidence Appendix. Items marked _(estimate)_ are AI-generated "
            "estimates or hypotheses, not verified facts.", ""]

    out += ["## 1. Executive Summary", f"**Client:** {es['client']}  ", f"**Project:** {es['project']}  ",
            f"**Industry:** {es.get('industry') or 'Unknown'}", "",
            f"**Current product position{' (estimate)' if es['ai_generated'] else ''}:** {es['current_position']}", "",
            "**Key findings**", _md_list(es["key_findings"]), "", "**Major opportunities**",
            _md_list(es["major_opportunities"]), ""]

    c = ci["company"]
    out += ["## 2. Client Intelligence", f"{c.get('description') or '_No public description found._'}"
            f"{ci['company_citations']}", ""]
    facts = [f"**{k.replace('_', ' ').title()}:** {v}" for k, v in c.items()
             if v and k not in ("name", "description")]
    out += [_md_list(facts), ""]
    if ci["locations"]:
        out += [f"**Locations:** {', '.join(ci['locations'])}", ""]
    out += ["### Products & services"]
    out += [_md_list([f"**{p['name']}** ({p['kind']}) — {p.get('description', '')}{p['cite']}" for p in ci["products"]]), ""]
    if ci["leadership"]:
        out += ["### Leadership", _md_list([f"{p['name']} — {p['title']}{p['cite']}" for p in ci["leadership"]]), ""]
    if ci["contacts"]:
        out += ["### Contact & social", _md_list([f"{x['type']}: {x['value']}" for x in ci["contacts"]]), ""]

    out += ["## 3. Existing Project Analysis", "### Features",
            "| Feature | Status | Technology | Evidence |", "|---|---|---|---|"]
    for f in pa["features"]:
        out.append(f"| {f['name']} | {STATUS_ICON.get(f['status'], '')} {f['status']} | "
                   f"{', '.join(f.get('technology') or []) or '—'} | {f['cite'].strip() or '—'} |")
    out.append("")
    for r in pa["repositories"]:
        out += [f"### Repository: [{r['full_name']}]({r['url']})",
                f"**Architecture:** {', '.join(r['architecture']) or 'Not determined'}"]
        if r.get("summary"):
            out.append(f"\n{r['summary']} _(estimate)_")
        by_cat: dict[str, list[str]] = {}
        for t in r["technologies"]:
            by_cat.setdefault(t["category"], []).append(f"{t['name']}{t['cite']}")
        out += ["", "| Area | Technologies |", "|---|---|"]
        out += [f"| {cat.replace('_', ' ').title()} | {', '.join(v)} |" for cat, v in sorted(by_cat.items())]
        out += ["", f"Tests: {'yes' if r['has_tests'] else 'no'} · CI/CD: {'yes' if r['has_ci'] else 'no'} · "
                    f"Containerized: {'yes' if r['has_docker'] else 'no'}", "", "**Technical debt indicators**",
                _md_list(r["technical_debt"]), ""]

    out += ["## 4. Market Analysis"]
    for comp in ma["competitors"]:
        out.append(f"- **[{comp['name']}]({comp['url']})** — _{comp['classification']}_. "
                   f"{comp.get('description') or ''}{comp['cite']}"
                   + (f" Pricing: {comp['pricing']}." if comp.get("pricing") else ""))
    if not ma["competitors"]:
        out.append("- _No verified competitors._")
    if ma["rejected_candidates"]:
        out += ["", "<details><summary>Candidates rejected during verification</summary>", ""]
        out += [f"- {r['name']} ({r['url']}): {r['reason']}" for r in ma["rejected_candidates"]]
        out += ["", "</details>"]
    out.append("")

    fc = s["feature_comparison"]
    comps = fc.get("competitors", [])
    out += ["## 5. Feature Comparison", "",
            "| Feature | Client | " + " | ".join(c["name"] for c in comps) + " |",
            "|---|---|" + "---|" * len(comps)]
    for row in fc.get("rows", []):
        cells = [STATUS_ICON[row["competitors"].get(c["id"], "unknown")] for c in comps]
        out.append(f"| {row['feature_name']} | {STATUS_ICON[row['client']]} | " + " | ".join(cells) + " |")
    out += ["", "✅ available · 🟡 partial · ❌ not found · `·` not evidenced", ""]

    out += ["## 6. Gap Analysis", "| Gap | Type | Description | Confidence | Evidence |", "|---|---|---|---|---|"]
    for g in s["gap_analysis"]:
        est = " _(estimate)_" if g.get("basis") == "estimate" else ""
        out.append(f"| {g['name']} | {g['gap_type']} | {g['description']}{est} | {g['confidence']:.0%} | "
                   f"{g['cite'].strip() or '—'} |")
    out.append("")

    out += ["## 7. Recommended Opportunities _(estimates)_",
            "| # | Opportunity | Why | Impact | Complexity | Score |", "|---|---|---|---|---|---|"]
    opps = {o["gap_id"]: o for o in s["opportunities"]}
    ranked = [r for phase in PHASE_LABELS for r in s["roadmap"][phase]]
    ranked.sort(key=lambda r: -r["score"]["total"])
    for i, r in enumerate(ranked, 1):
        o = opps.get(r["gap_id"], {})
        out.append(f"| {i} | {r['feature']} | {o.get('business_opportunity', '')} | {r['business_impact']} | "
                   f"{r['complexity']} | {r['score']['total']} |")
    w = s.get("scoring_weights", {})
    out += ["", "Score = Σ weight × factor (0–5) for benefits − weight × (complexity, risk). Weights: "
            + ", ".join(f"{k} {v}" for k, v in w.items()), ""]

    out += ["## 8. Enhancement Roadmap"]
    efforts = {p["recommendation_id"]: p.get("estimated_effort") for p in s["patch_plans"]}
    for phase, label in PHASE_LABELS.items():
        out += [f"### {label}"]
        items = s["roadmap"][phase]
        if not items:
            out += ["- _No items_", ""]
            continue
        out += ["| Feature | Complexity / effort | Dependencies | Expected impact | Evidence |", "|---|---|---|---|---|"]
        for r in items:
            effort = efforts.get(r["id"])
            out.append(f"| {r['feature']} | {r['complexity']}{f' ({effort})' if effort else ''} | "
                       f"{', '.join(r['dependencies']) or '—'} | "
                       f"{r['expected_outcome']} | {r['cite'].strip() or '—'} |")
        out.append("")

    out += ["## 9. Technical Patch Plan _(estimates)_"]
    for p in s["patch_plans"]:
        out += [f"### {p['feature']}", f"**Objective:** {p['objective']}  ",
                f"**Architecture impact:** {p['architecture_impact']}  ",
                f"**Estimated effort:** {p['estimated_effort']} · **Team:** {', '.join(p['recommended_team'])}", ""]
        for key, label in (("frontend_changes", "Frontend"), ("backend_changes", "Backend"),
                           ("database_changes", "Database"), ("api_changes", "API"), ("ai_changes", "AI"),
                           ("infrastructure_changes", "Infrastructure"), ("security_changes", "Security"),
                           ("testing_requirements", "Testing"), ("migration_requirements", "Migration / deployment"),
                           ("acceptance_criteria", "Acceptance criteria")):
            if p.get(key):
                out += [f"**{label}**", _md_list(p[key]), ""]

    out += ["## 10. Evidence Appendix", "| Ref | Claim | Source | Type | Confidence | Collected |",
            "|---|---|---|---|---|---|"]
    for e in s["evidence_appendix"]:
        src = e["source_url"]
        loc = f" `{e['repository_path']}`" + (f" L{e['line_range']}" if e.get("line_range") else "") \
            if e.get("repository_path") else ""
        link = f"[{src[:60]}]({src})" if src.startswith("http") else f"`{src}`"
        claim = e["claim"].replace("|", "\\|")
        out.append(f"| {e['ref']} | {claim} | {link}{loc} | {e['source_type']} | {e['confidence']:.2f} | "
                   f"{e['collected_at'][:10]} |")
    return "\n".join(out) + "\n"


class ReportAgent(Agent):
    name = "report"
    description = "Generate the final client intelligence report"
    after = ("enhancement_planning",)
    max_attempts = 1

    def approval_needed(self, ctx: RunContext) -> ApprovalRequest | None:
        if "client_report" in ctx.approvals:
            return None
        return ApprovalRequest(
            gate="client_report", title="Generate client-facing report",
            what="All findings, evidence, gaps, recommendations and patch plans from this run.",
            why="The report may be shared with the client; review the findings before it is generated.",
            target=f"{ctx.record.client.name} — {ctx.record.project.name}",
            data_analyzed="Stored run results only; no new external access.",
        )

    async def run(self, ctx: RunContext) -> AgentResult:
        summary = None
        errors: list[str] = []
        gaps = ctx.data("gap_analysis").get("gaps", [])
        recs = ctx.data("opportunity_prioritization").get("recommendations", [])
        try:
            summary = await ctx.llm.complete_json(
                "You write concise executive summaries for account managers. Use only the structured findings "
                "provided; do not introduce new facts. 3-5 key findings, 3-5 major opportunities.",
                f"Client: {ctx.record.client.name}\nProject: {ctx.record.project.name}\n"
                f"Features: {[f['name'] + ':' + f['status'] for f in ctx.data('product_features').get('inventory', [])][:40]}\n"
                f"Competitors: {[c['name'] for c in ctx.data('competitor_research').get('competitors', [])]}\n"
                f"Gaps: {[g['name'] + ' (' + g['gap_type'] + ')' for g in gaps][:30]}\n"
                f"Top recommendations: {[r['feature'] + ' / ' + r['phase'] for r in recs]}",
                _LLMSummary,
            )
        except LLMUnavailable:
            pass
        except LLMError as exc:
            errors.append(f"LLM summary failed: {exc}")
        report = build_report(ctx, summary)
        return AgentResult(confidence=0.8, errors=errors,
                           data={"report": report, "markdown": render_markdown(report)})
