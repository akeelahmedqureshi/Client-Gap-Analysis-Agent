"""Report Generation Agent: assembles the final intelligence report.

The report is built from structured agent outputs only. Every claim carries
citation markers ``[E#]`` that resolve to the evidence appendix; AI-generated
estimates are explicitly labelled.
"""

from __future__ import annotations

from datetime import datetime, timezone
from cip.core.urls import urlparse

from pydantic import BaseModel

from cip.agents.base import Agent, ApprovalRequest, RunContext
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.schemas import STATUS_LABELS, AgentResult
from cip.agents.pricing_analysis import MODEL_LABELS
from cip.core.scoring import PHASE_LABELS
from cip.connectors.research.ux import PRACTICES

STATUS_ICON = {"available": "✅", "partial": "🟡", "missing": "❌", "unknown": "❔"}
STATUS_LEGEND = "✅ available · 🟡 partially available · ❔ not publicly identified · ❌ confirmed missing"


SUMMARY_PROMPT = ("You write concise executive summaries for account managers. Use only the structured findings "
                  "provided; do not introduce new facts. 3-5 key findings, 3-5 major opportunities.")


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
    top_recs = sorted(prio.get("recommendations", []), key=lambda r: -r["score"]["total"])[:5]
    scores = {o["gap_id"]: o["score"]["total"] for o in prio.get("opportunities", [])}
    ranked_gaps = sorted(gaps, key=lambda g: -scores.get(g["id"], 0))
    sections["executive_summary"] = {
        "client": rec.client.name, "project": rec.project.name,
        "industry": profile.get("industry") or rec.client.industry,
        "current_position": summary.current_position if summary else
        (f"{rec.project.name} evidences {sum(1 for f in features.get('inventory', []) if f['status'] == 'available')} "
         f"features against {len(comp)} verified competitor(s); {len(gaps)} gaps identified."),
        "key_findings": summary.key_findings if summary else
        [f"{g['name']}: {g['description']}" for g in ranked_gaps[:5]],
        "major_opportunities": summary.major_opportunities if summary else
        [f"{r['feature']} — {PHASE_LABELS[r['phase']]}" for r in top_recs],
        "ai_generated": summary is not None,
    }
    sections["client_intelligence"] = {
        "company": {k: profile.get(k) for k in ("name", "legal_name", "domain", "description", "industry",
                                                "headquarters", "founded_year", "company_size", "business_model",
                                                "revenue_model")},
        "brands": profile.get("brands", []),
        "subsidiaries": profile.get("subsidiaries", []),
        "divisions": profile.get("divisions", []),
        "geographic_markets": profile.get("geographic_markets", []),
        "hiring": {**research.get("hiring", {}),
                   "signals": [{**s, "cite": cite([s["evidence_id"]])}
                               for s in research.get("hiring", {}).get("signals", [])]},
        "announcements": [{**a, "cite": cite([a["evidence_id"]])} for a in research.get("announcements", [])],
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
    sec = ctx.data("security_review")
    sections["security"] = None if not sec.get("enabled") else {
        "score": sec.get("score"), "grade": sec.get("grade"), "counts": sec.get("counts", {}),
        "method": sec.get("method"), "scope": sec.get("scope", []), "note": sec.get("note"),
        "issues": [{**i, "cite": cite([i["evidence_id"]])} for i in sec.get("issues", [])],
    }
    ux = ctx.data("ux_review")
    sections["ux"] = None if not ux.get("client") else {
        "companies": [{k: c.get(k) for k in ("name", "is_client", "pages", "score", "browser", "issue_counts")}
                      | {"practices": {p: bool(v) for p, v in (c.get("practices") or {}).items()}}
                      for c in ux.get("companies", [])],
        "issues": [{**i, "cite": cite([i["evidence_id"]])} for i in ux.get("issues", [])],
        "market": ux.get("market", {}), "notes": ux.get("notes", []), "method": ux.get("method"),
        "disclaimer": ux.get("disclaimer"),
    }
    sections["market_analysis"] = {
        "competitors": [{"name": c["name"], "url": c.get("url"), "classification": c["classification"],
                         "description": c.get("description"), "pricing": c.get("pricing"),
                         "target_market": c.get("target_market"), "rationale": c.get("rationale"),
                         "cite": cite(c.get("evidence_ids"))} for c in comp],
        "rejected_candidates": [{"name": c["name"], "url": c.get("url"), "reason": c.get("rationale")}
                                for c in ctx.data("competitor_research").get("rejected", [])],
    }
    pricing = ctx.data("pricing_analysis")
    client_pricing = pricing.get("client") or {}
    sections["pricing"] = {
        "position": pricing.get("position", "unknown"),
        "market": pricing.get("market", {}),
        "client": {**client_pricing, "cite": cite(client_pricing.get("evidence_ids"))} if client_pricing else None,
        "competitors": [{**r, "cite": cite(r.get("evidence_ids"))} for r in pricing.get("competitors", [])],
    }
    apps = ctx.data("app_store")
    sections["app_store"] = None if not apps.get("enabled") or not (apps.get("client_apps") or
                                                                    apps.get("competitor_apps")) else {
        "client_apps": [{**a, "cite": cite([a["evidence_id"]])} for a in apps.get("client_apps", [])],
        "competitor_apps": [{"name": e["name"], "apps": [{**a, "cite": cite([a["evidence_id"]])} for a in e["apps"]]}
                            for e in apps.get("competitor_apps", [])],
        "market": apps.get("market", {}),
        "reviews": {**(apps.get("client_reviews") or {}),
                    "themes": [{**t, "cite": cite(t.get("evidence_ids"))}
                               for t in (apps.get("client_reviews") or {}).get("themes", [])]},
        "requests": [{**r, "cite": cite(r.get("evidence_ids"))} for r in apps.get("requests", [])],
        "notes": apps.get("notes", []), "method": apps.get("method"),
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
    sections["architecture"] = ctx.data("enhancement_planning").get("architecture")
    # --- BRS 7.23 sections added on top of the original report data -------------------------------
    opp_by_gap = {o["gap_id"]: o for o in prio.get("opportunities", [])}
    qa = ctx.data("quality_assurance")
    sections["completeness"] = None if not qa else {
        "state": qa["state"], "label": qa["label"], "metrics": qa["metrics"],
        "issues": [i for i in qa["issues"] if i["severity"] in ("blocking", "warning")],
        "conflicts": [{**c, "cite": cite(c.get("evidence_ids"))} for c in qa.get("conflicts", [])],
        "reproducibility": qa.get("reproducibility", {}), "thresholds": qa.get("thresholds", {}),
    }
    im = ctx.data("industry_market")
    sections["industry"] = None if not im else {
        **{k: im.get(k) for k in ("industry", "market_segment", "product_category", "customer_segment",
                                  "business_model", "geography", "basis", "notes")},
        "cite": cite(im.get("evidence_ids"), 3),
        "trends": [{**t, "cite": cite([t["evidence_id"]])} for t in im.get("trends", [])],
        "market": comparison.get("market"),
    }
    research_c = ctx.data("competitor_research")
    sections["landscape"] = [{**c, "cite": cite(c.get("evidence_ids"), 2)} for c in research_c.get("landscape", [])]
    sections["deep_competitors"] = [{
        "name": c["name"], "url": c.get("url"), "rank": c.get("rank"), "classification": c["classification"],
        "description": c.get("description"), "target_market": c.get("target_market"), "pricing": c.get("pricing"),
        "rationale": c.get("rationale"), "pages_analysed": len(c.get("pages_analysed", [])),
        "deep_error": c.get("deep_error"), "cite": cite(c.get("evidence_ids")),
        "capabilities": [{"name": ctx.taxonomy.get(o["feature_id"]).name if ctx.taxonomy.get(o["feature_id"]) else
                          o["feature_id"], "status": o["status"], "cite": cite(o.get("evidence_ids"), 1)}
                         for o in c.get("features", []) if o["status"] in ("available", "partial")],
    } for c in comp]
    rows = comparison.get("rows", [])
    sections["common_capabilities"] = {
        cls: [{"name": r["feature_name"], "client": r["client"], "top3": f"{r['top3_count']}/{r['top3_total']}",
               "top10": f"{r['top10_count']}/{r['top10_total']}" if r.get("top10_total") else "—"}
              for r in rows if r.get("market_class") == cls]
        for cls in ("industry_standard", "emerging", "differentiator", "niche", "unique_to_client")
    }
    procs = ctx.data("business_process")
    gap_by_process = {g.get("process_id"): g for g in gaps if g.get("process_id")}
    sections["processes"] = [{
        **p, "cite": cite(p.get("evidence_ids")),
        "priority": opp_by_gap.get(gap_by_process.get(p["process_id"], {}).get("id"), {}).get("priority"),
    } for p in procs.get("opportunities", [])]
    sections["process_disclaimer"] = procs.get("disclaimer")
    ai_gaps = [g for g in gaps if g["gap_type"] == "ai" and g["id"] in opp_by_gap]
    sections["ai_gaps"] = [{
        "name": g["name"], "description": g["description"], "basis": g.get("basis"),
        "priority": opp_by_gap[g["id"]].get("priority"), "opportunity": opp_by_gap[g["id"]].get("business_opportunity"),
        "revenue": opp_by_gap[g["id"]].get("revenue_opportunity"),
        "complexity": opp_by_gap[g["id"]].get("attributes", {}).get("complexity"), "cite": cite(g.get("evidence_ids")),
    } for g in sorted(ai_gaps, key=lambda g: -opp_by_gap[g["id"]]["score"]["total"])]
    recs_all = sorted(prio.get("recommendations", []), key=lambda r: -r["score"]["total"])
    sections["recommendations"] = [{**r, "cite": cite(r.get("evidence_ids")),
                                    "why": opp_by_gap.get(r["gap_id"], {}).get("business_opportunity", "")}
                                   for r in recs_all]
    dims = (("Revenue", "revenue_potential"), ("Cost", "cost_saving"), ("Productivity", "productivity"),
            ("Customer experience", "user_impact"), ("Competitive positioning", "competitive_gap"))
    impact = []
    for label, factor in dims:
        top = sorted((o for o in prio.get("opportunities", []) if o.get("factors", {}).get(factor, 0) >= 4
                      and o["gap_id"] in {r["gap_id"] for r in recs_all}),
                     key=lambda o: -o["score"]["total"])[:3]
        impact.append({"dimension": label, "level": "high" if len(top) >= 2 else "medium" if top else "low",
                       "drivers": [o["name"] for o in top]})
    sections["business_impact"] = impact
    counts = {"high": 0, "medium": 0, "low": 0}
    for r in recs_all:
        counts[r.get("priority", "medium")] = counts.get(r.get("priority", "medium"), 0) + 1
    sections["executive_summary"]["priority_counts"] = counts
    sections["executive_summary"]["completeness"] = qa.get("label") if qa else None
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


CONTENTS = ["Executive Summary", "Client Overview", "Client Website & Product Analysis", "Industry & Market Analysis",
            "Competitor Landscape", "Top 3 Competitor Deep Analysis", "Feature Comparison Matrix",
            "Feature Gap Analysis", "Common Competitor Features", "Business Cost-Reduction Opportunities",
            "AI & Automation Opportunities", "Prioritized Recommendations", "Quick Wins", "Strategic Roadmap",
            "Business Impact Summary"]
CLASS_LABEL = {"industry_standard": "Industry standard (must-have)", "emerging": "Emerging market expectation",
               "differentiator": "Competitive differentiator", "niche": "Niche capability",
               "unique_to_client": "Unique to the client"}
HORIZONS = (("Short term (0-3 months)", ("phase_1_quick_wins",)), ("Medium term (3-6 months)", ("phase_2_growth",)),
            ("Long term (6-12 months)", ("phase_3_major", "phase_4_strategic")))


def _cell(text) -> str:
    return str(text or "").replace("|", "/").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")


def render_markdown(report: dict) -> str:
    s = report["sections"]
    es = s["executive_summary"]
    ci = s["client_intelligence"]
    pa = s["project_analysis"]
    q = s.get("completeness")
    out: list[str] = [f"# {report['title']}", f"_Generated {report['generated_at']} · run `{report['run_id']}`_", ""]
    if q:
        icon = {"complete": "✅", "complete_with_warnings": "⚠️", "partial": "⚠️", "needs_review": "⛔"}[q["state"]]
        out += [f"> {icon} **Analysis status: {q['label']}.** "
                + {"complete": "All analysis stages completed and passed the quality checks.",
                   "complete_with_warnings": "All stages completed; review the warnings below before relying on them.",
                   "partial": "Some analysis stages did not complete; the affected sections are incomplete.",
                   "needs_review": "Quality checks found blocking issues; review before sharing this report."}[q["state"]]]
        for i in q["issues"][:6]:
            out.append(f"> - {i['message']}")
        out.append("")
    out += ["> **Legend** — `[E#]` cites the Evidence Appendix. Items marked _(estimate)_ are AI-generated "
            "estimates or hypotheses, and _(assumption)_ marks statements about internal processes that are not "
            "publicly observable. Not publicly identified ≠ absent.", "",
            "**Contents:** " + " · ".join(f"{i}. {t}" for i, t in enumerate(CONTENTS, 1))
            + " · Appendices: A. Technical Patch Plan · B. Analysis Quality · C. Evidence", ""]

    # 1 ---------------------------------------------------------------------------------------
    pc = es.get("priority_counts") or {}
    out += ["## 1. Executive Summary", f"**Client:** {es['client']}  ", f"**Project:** {es['project']}  ",
            f"**Industry:** {es.get('industry') or 'Unknown'}  "]
    if es.get("completeness"):
        out.append(f"**Analysis status:** {es['completeness']}  ")
    out += ["", f"**Current product position{' (estimate)' if es['ai_generated'] else ''}:** {es['current_position']}", "",
            "**Key findings**", _md_list(es["key_findings"]), "", "**Major opportunities**",
            _md_list(es["major_opportunities"]), ""]
    if pc:
        out += [f"**Recommended priorities:** {pc.get('high', 0)} high, {pc.get('medium', 0)} medium, "
                f"{pc.get('low', 0)} low (see section 12).", ""]

    # 2 ---------------------------------------------------------------------------------------
    c = ci["company"]
    out += ["## 2. Client Overview", f"{c.get('description') or '_No public description found._'}"
            f"{ci['company_citations']}", ""]
    facts = [f"**{k.replace('_', ' ').title()}:** {v}" for k, v in c.items()
             if v and k not in ("name", "description")]
    out += [_md_list(facts), ""]
    if ci["locations"]:
        out += [f"**Locations:** {', '.join(ci['locations'])}", ""]
    for key, label in (("geographic_markets", "Markets served"), ("brands", "Brands"),
                       ("subsidiaries", "Subsidiaries"), ("divisions", "Business divisions")):
        if ci.get(key):
            out += [f"**{label}:** {', '.join(ci[key])}", ""]
    out += ["### Products & services"]
    out += [_md_list([f"**{p['name']}** ({p['kind']}) — {p.get('description', '')}{p['cite']}" for p in ci["products"]]), ""]
    if ci["leadership"]:
        out += ["### Leadership", _md_list([f"{p['name']} — {p['title']}{p['cite']}" for p in ci["leadership"]]), ""]
    if ci["contacts"]:
        out += ["### Contact & social", _md_list([f"{x['type']}: {x['value']}" for x in ci["contacts"]]), ""]
    hiring = ci.get("hiring") or {}
    if hiring.get("signals"):
        out += ["### Hiring signals",
                f"{hiring['job_count']} open role(s) on the company's job board — where they are investing:", "",
                "| Area | Open roles | Examples | Evidence |", "|---|---|---|---|"]
        out += [f"| {s['area']} | {s['count']} | {'; '.join(s['examples'][:3])} | {s['cite'].strip()} |"
                for s in hiring["signals"]]
        out.append("")
    if ci.get("announcements"):
        out += ["### Recent announcements",
                _md_list([f"{'**Product:** ' if a['is_product'] else ''}[{a['title']}]({a['url']})"
                          + (f" ({a['date']})" if a.get("date") else "") + a["cite"] for a in ci["announcements"]]),
                ""]

    ind = s.get("industry") or {}
    if ind.get("customer_segment") or ind.get("geography"):
        out += ["### Target audience & market",
                _md_list([f"**Customers:** {', '.join(ind.get('customer_segment') or []) or 'not publicly identified'}",
                          f"**Markets:** {', '.join(ind.get('geography') or []) or 'not publicly identified'}",
                          f"**Business model:** {ind.get('business_model') or 'not publicly identified'}"]), ""]

    # 3 ---------------------------------------------------------------------------------------
    out += ["## 3. Client Website & Product Analysis", "### Capability inventory",
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

    sec = s.get("security")
    if sec:
        c = sec["counts"]
        out += ["### Security review",
                f"**Grade {sec['grade']}** (score {sec['score']}/100) — "
                + ", ".join(f"{c.get(k, 0)} {k}" for k in ("critical", "high", "medium", "low", "info")) + ".", "",
                f"_{sec['note']}_ Scoring: {sec['method']}.", "", "**Scope**", _md_list(sec["scope"]), ""]
        if sec["issues"]:
            out += ["| Severity | Issue | Recommendation | Evidence |", "|---|---|---|---|"]
            for i in sec["issues"]:
                refs = " ".join(r for r in i.get("references", []) if r.startswith("CVE-"))
                title = i["title"].replace("|", "\\|") + (f" ({refs})" if refs else "")
                out.append(f"| {i['severity']} | {title} | {i['recommendation'].replace('|', '/')} | "
                           f"{i['cite'].strip() or '—'} |")
            out.append("")

    ux = s.get("ux")
    if ux:
        def esc(text) -> str:  # table cell: no pipes, newlines or live HTML (recommendations quote markup)
            return str(text).replace("|", "/").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")

        comps = ux["companies"]
        cats = ["accessibility", "mobile", "performance", "conversion"]
        cats = [c for c in cats if any(c in ((x.get("score") or {}).get("categories") or {}) for x in comps)]
        out += ["### UX review", "", f"_{ux['disclaimer']}_", "",
                "| Company | Overall | " + " | ".join(c.title() for c in cats) + " |",
                "|---|---|" + "---|" * len(cats)]
        for x in comps:
            sc = x.get("score") or {}
            name = f"**{esc(x['name'])} (client)**" if x["is_client"] else esc(x["name"])
            out.append(f"| {name} | {sc.get('overall', '—')} | "
                       + " | ".join(str((sc.get('categories') or {}).get(c, '—')) for c in cats) + " |")
        out += ["", "| Practice | " + " | ".join(esc(x["name"]) for x in comps) + " |",
                "|---|" + "---|" * len(comps)]
        for key, label in PRACTICES.items():
            out.append(f"| {label} | " + " | ".join("✅" if x["practices"].get(key) else "❌" for x in comps) + " |")
        if ux["issues"]:
            out += ["", "| Severity | Issue | WCAG | Pages | Recommendation | Evidence |", "|---|---|---|---|---|---|"]
            for i in ux["issues"]:
                if i["severity"] == "info":
                    continue
                pages = ", ".join(urlparse(p).path or "/" for p in i.get("pages", [])[:4])
                out.append(f"| {i['severity']} | {esc(i['title'])} | {i.get('wcag') or '—'} | {pages} | "
                           f"{esc(i['recommendation'])} | {i['cite'].strip() or '—'} |")
        out += [""] + [f"_{n}_" for n in ux.get("notes", [])] + [f"_Scoring: {ux['method']}_", ""]

    ap = s.get("app_store")
    if ap:
        def cell(text) -> str:
            return str(text).replace("|", "/").replace("\n", " ")

        def app_row(owner: str, a: dict) -> str:
            age = a.get("days_since_update")
            return (f"| {cell(owner)} | [{cell(a['name'])}]({a['url']}) | {'iOS' if a['platform'] == 'ios' else 'Android'}"
                    f" | {a['rating']:g}★ | {a.get('rating_count') or 0:,} | "
                    f"{(a.get('updated') or '')[:10] or '—'}{' ⚠ stale' if age and age > 180 else ''} | "
                    f"{a['cite'].strip() or '—'} |" if a.get("rating") else
                    f"| {cell(owner)} | [{cell(a['name'])}]({a['url']}) | "
                    f"{'iOS' if a['platform'] == 'ios' else 'Android'} | — | — | — | {a['cite'].strip() or '—'} |")

        out += ["### Mobile apps (app stores)", "",
                "| Company | App | Platform | Rating | Ratings | Last release | Evidence |",
                "|---|---|---|---|---|---|---|"]
        out += [app_row("**Client**", a) for a in ap["client_apps"]]
        if not ap["client_apps"]:
            out.append("| **Client** | _no app found_ | — | — | — | — | — |")
        out += [app_row(e["name"], a) for e in ap["competitor_apps"] for a in e["apps"]]
        m = ap.get("market") or {}
        if m.get("competitor_median_rating"):
            out += ["", f"Competitor median rating **{m['competitor_median_rating']:g}★**"
                        + (f"; client **{m['client_rating']:g}★** ({m['client_rating_count']:,} ratings)."
                           if m.get("client_rating") else ".")]
        rv = ap.get("reviews") or {}
        if rv.get("total"):
            sen = rv.get("sentiment", {})
            out += ["", f"**Recent client reviews ({rv['total']}, average {rv['average']:g}★):** "
                        f"{sen.get('negative', 0)} negative, {sen.get('neutral', 0)} neutral, "
                        f"{sen.get('positive', 0)} positive.", ""]
            for t in rv.get("themes", [])[:5]:
                quote = t["examples"][0]["quote"].replace("\n", " ") if t.get("examples") else ""
                out.append(f"- **{t['label']}**: {t['negative']} negative / {t['count']} mentions — "
                           f"_\"{quote}\"_{t['cite']}")
        if ap.get("requests"):
            out += ["", "**Customer feature requests:** " + "; ".join(
                f"{r['name']} ({r['count']} review(s)){r['cite']}" for r in ap["requests"])]
        out += ["", f"_{ap['method']}_"] + [f"_{n}_" for n in ap.get("notes", [])] + [""]

    # 4 ---------------------------------------------------------------------------------------
    out += ["## 4. Industry & Market Analysis"]
    if ind:
        basis = ind.get("basis") or {}
        rows = [("Industry", ind.get("industry"), basis.get("industry")),
                ("Market segment", ind.get("market_segment"), basis.get("market_segment")),
                ("Product category", ind.get("product_category"), basis.get("product_category")),
                ("Customer segment", ", ".join(ind.get("customer_segment") or []), basis.get("customer_segment")),
                ("Business model", ind.get("business_model"), basis.get("business_model"))]
        out += ["| Dimension | Value | Basis |", "|---|---|---|"]
        out += [f"| {k} | {_cell(v) or 'not publicly identified'} | {b or '—'} |" for k, v, b in rows]
        out += ["", f"Sources:{ind['cite'] or ' —'}", ""]
        for kind, label in (("trend", "Industry trends"), ("technology", "Emerging technology"),
                            ("ai_adoption", "AI adoption"), ("automation", "Automation trends")):
            items = [t for t in ind.get("trends", []) if t["kind"] == kind]
            if items:
                out += [f"**{label}**", _md_list([f"{t['statement']}{t['cite']}" for t in items]), ""]
        if not ind.get("trends"):
            out += ["_" + ((ind.get("notes") or ["No sourced market trends were found."])[0]) + "_", ""]
        m = ind.get("market") or {}
        if m.get("landscape_size"):
            pct = lambda v: "—" if v is None else f"{v:.0%}"  # noqa: E731
            out += [f"**Across the {m['landscape_size']} competitors analysed:** {pct(m.get('ai_adoption'))} offer an AI "
                    f"capability and {pct(m.get('automation_adoption'))} offer automation; the client publicly offers "
                    f"{pct(m.get('client_standard_coverage'))} of the {len(m.get('industry_standards', []))} industry-"
                    "standard capabilities.", ""]
    else:
        out += ["_Industry & market analysis did not run._", ""]
    pr = s.get("pricing") or {}
    if pr.get("competitors") or pr.get("client"):
        def money(v, cur):
            return f"{v:g} {cur or ''}".strip() if v else "—"

        def practices(p):
            bits = [", ".join(MODEL_LABELS.get(m, m) for m in p.get("models", []))]
            if p.get("free_trial"):
                days = p.get("trial_days")
                bits.append(f"free trial ({days}d)" if days else "free trial")
            if p.get("annual_discount_pct"):
                bits.append(f"{p['annual_discount_pct']}% annual discount")
            if p.get("enterprise_contact"):
                bits.append("enterprise tier")
            return "; ".join(b for b in bits if b) or "—"

        out += ["### Pricing", "", "| Company | Entry price / month | Highest / month | Model & practices | Evidence |",
                "|---|---|---|---|---|"]
        c = pr.get("client")
        if c:
            out.append(f"| **Client** | {money(c.get('entry_price_monthly'), c.get('currency'))} | "
                       f"{money(c.get('max_price_monthly'), c.get('currency'))} | {practices(c)} | "
                       f"{c['cite'].strip() or '—'} |")
        for r in pr.get("competitors", []):
            out.append(f"| {r['name']} | {money(r.get('entry_price_monthly'), r.get('currency'))} | "
                       f"{money(r.get('max_price_monthly'), r.get('currency'))} | {practices(r)} | "
                       f"{r['cite'].strip() or '—'} |")
        m = pr.get("market", {})
        if m.get("entry_price_median"):
            out += ["", f"Market entry price: median **{m['entry_price_median']:g} {m.get('currency') or ''}**/month "
                        f"(range {m['entry_price_min']:g}–{m['entry_price_max']:g}). Client position: "
                        f"**{pr.get('position', 'unknown')}** market."
                    + (f" {m['excluded_other_currency']} competitor(s) priced in another currency were excluded."
                       if m.get("excluded_other_currency") else "")]
        out.append("")

    # 5 ---------------------------------------------------------------------------------------
    out += ["## 5. Competitor Landscape",
            "Ranked by relevance to this client — capability overlap, product, industry, customers, geography and "
            "business model — not by company size.", "",
            "| # | Competitor | Type | Relevance | Why included | Evidence |", "|---|---|---|---|---|---|"]
    for c in s.get("landscape", []):
        deep = " **(deep analysis)**" if c.get("deep") else ""
        out.append(f"| {c['rank']} | [{_cell(c['name'])}]({c['url']}){deep} | {c['classification']} | "
                   f"{c['relevance']['overall']:.0%} | {_cell(c['reason'])} | {c['cite'].strip() or '—'} |")
    if not s.get("landscape"):
        out.append("| — | _No verified competitors_ | | | | |")
    rejected = s["market_analysis"]["rejected_candidates"]
    if rejected:
        out += ["", "**Candidates rejected during verification:** "
                + "; ".join(f"{_cell(r['name'])} ({_cell(r['reason'])[:120]})" for r in rejected[:10])]
    out.append("")

    # 6 ---------------------------------------------------------------------------------------
    out += ["## 6. Top 3 Competitor Deep Analysis"]
    for c in s.get("deep_competitors", []):
        out += [f"### #{c['rank']} [{c['name']}]({c['url']}) — {c['classification']}",
                f"{c.get('description') or ''}{c['cite']}", ""]
        facts = [f"**Target market:** {c['target_market']}" if c.get("target_market") else "",
                 f"**Pricing:** {c['pricing']}" if c.get("pricing") else "",
                 f"**Why it matters:** {c['rationale']}" if c.get("rationale") else "",
                 f"**Pages analysed:** {c['pages_analysed']}"
                 + (" (deep analysis failed; light profile shown)" if c.get("deep_error") else "")]
        out += [_md_list([f for f in facts if f]), ""]
        if c["capabilities"]:
            out += ["**Capabilities evidenced:** " + ", ".join(
                f"{x['name']}{' (partial)' if x['status'] == 'partial' else ''}{x['cite']}" for x in c["capabilities"]), ""]
    if not s.get("deep_competitors"):
        out += ["_No competitor was analysed in depth._", ""]

    # 7 ---------------------------------------------------------------------------------------
    fc = s["feature_comparison"]
    comps = fc.get("competitors", [])
    out += ["## 7. Feature Comparison Matrix", "",
            "| Capability | Client | " + " | ".join(_cell(c["name"]) for c in comps) + " | Top 3 | Top 10 | Market |",
            "|---|---|" + "---|" * len(comps) + "---|---|---|"]
    for row in fc.get("rows", []):
        cells = [STATUS_ICON[row["competitors"].get(c["id"], "unknown")] for c in comps]
        top10 = f"{row.get('top10_count', 0)}/{row['top10_total']}" if row.get("top10_total") else "—"
        out.append(f"| {row['feature_name']} | {STATUS_ICON[row['client']]} | " + " | ".join(cells)
                   + f" | {row.get('top3_count', 0)}/{row.get('top3_total', len(comps))} | {top10} | "
                   f"{CLASS_LABEL.get(row.get('market_class'), '—')} |")
    out += ["", STATUS_LEGEND,
            "_Not publicly identified means no public evidence was found; it does not mean the capability is absent._", ""]

    # 8 ---------------------------------------------------------------------------------------
    opps = {o["gap_id"]: o for o in s["opportunities"]}
    out += ["## 8. Feature Gap Analysis",
            "| Gap | Category | Priority | Offered by | Description | Confidence | Evidence |", "|---|---|---|---|---|---|---|"]
    names = {c["id"]: c["name"] for c in comps}
    for g in s["gap_analysis"]:
        o = opps.get(g["id"], {})
        est = " _(estimate)_" if g.get("basis") == "estimate" else ""
        offered = ", ".join(names.get(c, c) for c in g.get("competitors_with", [])) or "—"
        out.append(f"| {_cell(g['name'])} | {o.get('business_category') or g['gap_type']} | {o.get('priority', '—')} | "
                   f"{_cell(offered)} | {_cell(g['description'])}{est} | {g['confidence']:.0%} | {g['cite'].strip() or '—'} |")
    out.append("")

    # 9 ---------------------------------------------------------------------------------------
    cc = s.get("common_capabilities") or {}
    out += ["## 9. Common Competitor Features"]
    for cls in ("industry_standard", "emerging", "differentiator", "niche", "unique_to_client"):
        items = cc.get(cls, [])
        if not items:
            continue
        out += [f"**{CLASS_LABEL[cls]}**",
                _md_list([f"{i['name']} — top 3: {i['top3']}, top 10: {i['top10']}; client: "
                          f"{STATUS_LABELS.get(i['client'], i['client'])}" for i in items]), ""]
    if not any(cc.values()):
        out += ["_No common capabilities could be determined._", ""]

    # 10 --------------------------------------------------------------------------------------
    out += ["## 10. Business Cost-Reduction Opportunities"]
    if s.get("process_disclaimer"):
        out += [f"_{s['process_disclaimer']}_", ""]
    for p in s.get("processes", []):
        out += [f"### {p['area']}: {p['name']}" + (f" — {p['priority']} priority" if p.get("priority") else ""),
                _md_list([f"**Observed:** {'; '.join(p['observed'])}{p['cite']}",
                          f"**Likely current process _(assumption)_:** {p['current_process']}",
                          f"**Inefficiency _(assumption)_:** {p['inefficiency']}",
                          f"**Recommended improvement:** {p['proposed_solution']} _(estimate)_",
                          f"**Operational benefit:** resource saving {p['cost_reduction']}, processing time "
                          f"{p['time_reduction']}, errors/rework {p['error_reduction']} (qualitative)",
                          f"**Complexity:** {p['complexity']}/5 · **Confidence:** {p['confidence']:.0%}"]), ""]
    if not s.get("processes"):
        out += ["_No business process with an open improvement was observed._", ""]

    # 11 --------------------------------------------------------------------------------------
    out += ["## 11. AI & Automation Opportunities",
            "AI and automation are recommended only where an observed business process or competitor evidence "
            "justifies them.", ""]
    ai_proc = [p for p in s.get("processes", []) if p.get("ai")]
    auto_proc = [p for p in s.get("processes", []) if p.get("automation") and not p.get("ai")]
    for label, items in (("AI opportunities", ai_proc), ("Automation opportunities", auto_proc)):
        if not items:
            continue
        out += [f"### {label}", "| Opportunity | Business problem | How it works | Benefit | Customer impact | "
                "Revenue | Complexity | Priority |", "|---|---|---|---|---|---|---|---|"]
        out += [f"| {_cell(p['name'])} | {_cell(p['business_problem'])} | {_cell(p['how_it_works'])} | "
                f"cost {p['cost_reduction']}, productivity {p['productivity']} | {_cell(p['customer_impact'])} | "
                f"{_cell(p['revenue_opportunity'])} | {p['complexity']}/5 | {p.get('priority') or '—'} |" for p in items]
        out.append("")
    if s.get("ai_gaps"):
        out += ["### AI capabilities competitors offer",
                "| Capability | Evidence of need | Opportunity | Complexity | Priority | Evidence |", "|---|---|---|---|---|---|"]
        out += [f"| {_cell(g['name'])} | {_cell(g['description'])}{' _(estimate)_' if g.get('basis') == 'estimate' else ''} | "
                f"{_cell(g['opportunity'])} | {g.get('complexity') or '—'} | {g.get('priority') or '—'} | "
                f"{g['cite'].strip() or '—'} |" for g in s["ai_gaps"]]
        out.append("")

    # 12 --------------------------------------------------------------------------------------
    out += ["## 12. Prioritized Recommendations _(estimates)_"]
    recs = s.get("recommendations", [])
    for level in ("high", "medium", "low"):
        items = [r for r in recs if r.get("priority", "medium") == level]
        out += [f"### {level.title()} priority"]
        if not items:
            out += ["- _None_", ""]
            continue
        out += ["| Recommendation | Category | Why it matters | Expected value | Complexity | Horizon | Score |",
                "|---|---|---|---|---|---|---|"]
        out += [f"| {_cell(r['feature'])} | {r.get('business_category') or '—'} | {_cell(r['why'])} | "
                f"{_cell(r['business_impact'])} | {r['complexity']} | {PHASE_LABELS[r['phase']].split(' — ')[1]} | "
                f"{r['score']['total']} |" for r in items]
        out.append("")
    w = s.get("scoring_weights", {})
    out += ["Score = Σ weight × factor (0–5) for benefits, scaled by evidence confidence, − weight × (complexity, risk). "
            "A recommendation is High only with confidence ≥ 50%. Weights: " + ", ".join(f"{k} {v}" for k, v in w.items()), ""]

    # 13 --------------------------------------------------------------------------------------
    quick = [r for r in recs if r["phase"] == "phase_1_quick_wins"]
    out += ["## 13. Quick Wins", "Improvements that can be delivered relatively quickly (0-4 weeks) for immediate value.", ""]
    out += [_md_list([f"**{r['feature']}** ({r.get('priority', 'medium')} priority) — {r['expected_outcome']}{r['cite']}"
                      for r in quick]), ""]

    # 14 --------------------------------------------------------------------------------------
    out += ["## 14. Strategic Roadmap"]
    efforts = {p["recommendation_id"]: p.get("estimated_effort") for p in s["patch_plans"]}
    for label, phases in HORIZONS:
        out += [f"### {label}"]
        items = [r for ph in phases for r in s["roadmap"][ph]]
        if not items:
            out += ["- _No items_", ""]
            continue
        out += ["| Initiative | Priority | Complexity / effort | Dependencies | Expected impact | Evidence |",
                "|---|---|---|---|---|---|"]
        for r in items:
            effort = efforts.get(r["id"])
            out.append(f"| {_cell(r['feature'])} | {r.get('priority', '—')} | {r['complexity']}{f' ({effort})' if effort else ''} | "
                       f"{_cell(', '.join(r['dependencies'])) or '—'} | {_cell(r['expected_outcome'])} | "
                       f"{r['cite'].strip() or '—'} |")
        out.append("")
    arch = s.get("architecture")
    if arch:
        out += ["### Architecture: current vs. target",
                f"Derived from {arch['based_on']}. Components added by the roadmap are highlighted.", ""]
        for key, label in (("current", "Current architecture"), ("target", "Target architecture (after roadmap)")):
            out += [f"**{label}**", "", f"<!-- architecture-diagram:{key} -->", "```mermaid",
                    arch[f"mermaid_{key}"], "```", "<!-- /architecture-diagram -->", ""]
        if arch.get("new_components"):
            out += ["**New components**",
                    _md_list([f"{c['label']} ({c['layer']}) — for {c['for']}" for c in arch["new_components"]]), ""]
    # 15 --------------------------------------------------------------------------------------
    out += ["## 15. Business Impact Summary _(estimates)_",
            "| Dimension | Expected impact | Main drivers |", "|---|---|---|"]
    for d in s.get("business_impact", []):
        out.append(f"| {d['dimension']} | {d['level']} | {_cell(', '.join(d['drivers'])) or '—'} |")
    out += ["", "_Impact levels are qualitative estimates from the scored recommendations; validate them with the client._", ""]

    # Appendices ------------------------------------------------------------------------------
    out += ["## Appendix A. Technical Patch Plan _(estimates)_"]
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

    if q:
        m = q["metrics"]
        r = q.get("reproducibility", {})
        fr = m.get("source_freshness", {})
        cov = m.get("evidence_coverage")
        out += ["## Appendix B. Analysis Quality & Reproducibility",
                f"**Status:** {q['label']} · **Evidence coverage:** {'—' if cov is None else f'{cov:.0%}'} of "
                f"{m['significant_findings']} significant findings · **Low-confidence findings:** {m['low_confidence_findings']} · "
                f"**Inferred / assumption-based:** {m['inferred_findings']} / {m['assumption_findings']} · "
                f"**Sources:** {m['evidence_items']} ({fr.get('fresh', 0)} fresh, {fr.get('aging', 0)} aging, "
                f"{fr.get('stale', 0)} stale) · **Competitors:** {m['landscape_size']} ranked, {m['competitors_deep']} deep-analysed", ""]
        if m.get("stages_missing"):
            out += [f"**Stages not completed:** {', '.join(m['stages_missing'])}", ""]
        if q.get("conflicts"):
            out += ["**Conflicting information**", _md_list([f"{c['topic']}: {c['detail']}{c['cite']}" for c in q["conflicts"]]), ""]
        if q.get("issues"):
            out += ["**Quality issues**", _md_list([f"{i['severity']}: {i['message']}" for i in q["issues"]]), ""]
        out += ["**Reproducibility**", _md_list([
            f"Model: {r.get('model')}" + (f" (temperature {r['llm_temperature']})" if r.get("llm_temperature") is not None else ""),
            f"Prompt version {r.get('prompt_version')} · taxonomy {r.get('taxonomy_version')} · process catalog "
            f"{r.get('process_catalog_version')}",
            f"Research window: {(r.get('research_from') or '')[:19]} – {(r.get('research_to') or '')[:19]} UTC",
            "Settings: " + ", ".join(f"{k} {v}" for k, v in (r.get("settings") or {}).items()),
        ]), ""]
    out += ["## Appendix C. Evidence Appendix", "| Ref | Claim | Source | Type | Confidence | Collected |",
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
    after = ("enhancement_planning", "quality_assurance")
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
                SUMMARY_PROMPT,
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
