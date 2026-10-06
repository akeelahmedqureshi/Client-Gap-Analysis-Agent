"""UX Review Agent (passive): accessibility, mobile, page speed and conversion practices.

Audits the client's homepage and key pages (pricing, sign-up, contact, product) and each verified
competitor's homepage with the checks in ``connectors/research/ux.py``. Produces per-company scores,
issues with WCAG references and markup evidence, practice comparison, and UX gaps. Runs only when
external research was approved.
"""

from __future__ import annotations

import re
import statistics
from cip.core.urls import urlparse

from cip.agents.base import Agent, RunContext
from cip.connectors.research.ux import (
    PRACTICES,
    SEVERITY_ORDER,
    BrowserUxAuditor,
    UxIssue,
    browser_issues,
    conversion_issues,
    detect_practices,
    score,
    static_checks,
)
from cip.core.schemas import AgentResult, AgentStatus, Basis, Finding, Gap, GapType

KEY_PAGES = [("pricing", ("pricing", "plans")), ("signup", ("signup", "sign-up", "register", "get-started", "trial",
                                                            "demo")),
             ("product", ("product", "features", "solutions")), ("contact", ("contact",))]
QUALITY_GAPS = {
    "accessibility": ("Accessibility (WCAG 2.2 AA) fixes", "ux.accessibility"),
    "mobile": ("Mobile-friendly responsive layout", None),
    "performance": ("Page speed (Core Web Vitals)", None),
}
CTA_GAP = "Clear primary call to action"
PRACTICE_GAPS = {
    "self_serve_cta": "Self-serve sign-up entry point",
    "live_chat": "Live chat support on the website",
    "help": "Help center / FAQ",
    "trust": "Trust signals (customers, reviews, compliance)",
    "search": "Site search",
}
BELOW_GAP = "UX quality below competitors"
BELOW_MARGIN = 10


def _rank(sev: str) -> int:
    return SEVERITY_ORDER.index(sev)


class UxReviewAgent(Agent):
    name = "ux_review"
    description = "UX deep dive: accessibility (WCAG), mobile, page speed and conversion practices vs competitors"
    after = ("client_research", "competitor_research")

    def _client_pages(self, ctx: RunContext, home: str) -> list[str]:
        pages = [home]
        known = [p["url"] for p in ctx.data("client_research").get("pages", []) if p.get("url")]
        for _kind, words in KEY_PAGES:
            hit = next((u for u in known if any(w in urlparse(u).path.lower() for w in words) and u not in pages),
                       None)
            if hit:
                pages.append(hit)
        return pages[: max(1, ctx.settings.ux_max_pages)]

    async def _audit_company(self, ctx: RunContext, urls: list[str], auditor) -> dict:
        issues: list[UxIssue] = []
        audited, practices, browser_ok = [], None, False
        for i, url in enumerate(urls):
            got = await ctx.fetcher.fetch_with_html(url)
            if not got:
                continue
            page, html = got
            audited.append(page.url)
            issues += static_checks(page.url, html)
            if i == 0:
                practices = detect_practices(page.url, html)
                issues += conversion_issues(page.url, practices)
            if auditor is not None:
                result = await auditor.audit(page.url)
                if result:
                    browser_ok = True
                    issues += browser_issues(result)
        cats = {"accessibility", "mobile", "conversion"} | ({"performance"} if browser_ok else set())
        return {"pages": audited, "issues": issues, "practices": practices or {}, "browser": browser_ok,
                "score": score(issues, cats) if audited else None}

    async def run(self, ctx: RunContext) -> AgentResult:
        s = ctx.settings
        if not s.ux_review_enabled:
            return AgentResult(status=AgentStatus.SKIPPED, confidence=1.0, data={"enabled": False})
        research = ctx.outputs.get("client_research")
        if research is None or research.status != AgentStatus.COMPLETED:
            return AgentResult(status=AgentStatus.SKIPPED, confidence=1.0,
                               data={"enabled": True, "reason": "external research not approved or not run"})
        rec, ledger = ctx.record, ctx.ledger
        domain = (ctx.data("client_research").get("profile") or {}).get("domain") or rec.client.domain
        home = rec.project.url or (f"https://{domain}" if domain else None)
        if not home:
            return AgentResult(status=AgentStatus.SKIPPED, confidence=1.0,
                               data={"enabled": True, "reason": "no client website"})

        auditor, reason = ctx.ux_auditor, None
        if auditor is None:
            if not s.ux_browser_checks:
                reason = "disabled (CIP_UX_BROWSER_CHECKS=false)"
            elif not ctx.fetcher.live:
                reason = "the web is simulated in this environment"
            else:
                auditor = BrowserUxAuditor(s)
        if auditor is not None and not auditor.available:
            auditor, reason = None, "no headless browser installed (pip install '.[browser]')"
        notes = [] if auditor else [f"Browser checks (contrast, phone layout, page speed) did not run: {reason}. "
                                    "Static checks only."]

        competitors = ctx.data("competitor_research").get("competitors", [])[: s.max_competitors]
        if auditor is not None:
            async with auditor.session():
                client = await self._audit_company(ctx, self._client_pages(ctx, home), auditor)
                comps = [await self._audit_company(ctx, [c["url"]], auditor) if c.get("url") else None
                         for c in competitors]
        else:
            client = await self._audit_company(ctx, self._client_pages(ctx, home), None)
            comps = [await self._audit_company(ctx, [c["url"]], None) if c.get("url") else None for c in competitors]
        if not client["pages"]:
            return AgentResult(confidence=0.2, findings=[Finding(category="ux", title="Client website not reachable",
                                                                 confidence=0.9)],
                               data={"enabled": True, "reason": "client website not reachable", "notes": notes})

        # Evidence (one entry per check, listing every page it failed on) -------------
        def merged(issues: list[UxIssue]) -> list[tuple[UxIssue, list[str]]]:
            groups: dict[str, tuple[UxIssue, list[str]]] = {}
            for i in issues:
                if i.key not in groups:
                    groups[i.key] = (UxIssue(**{**i.to_dict(), "examples": list(i.examples)}), [i.page])
                    continue
                g, pages = groups[i.key]
                pages.append(i.page)
                g.count += i.count
                g.examples = (g.examples + i.examples)[:3]
                if _rank(i.severity) < _rank(g.severity):
                    g.severity, g.title = i.severity, i.title
            for g, pages in groups.values():
                if len(pages) > 1 and re.match(r"^\d+ ", g.title):
                    g.title = re.sub(r"^\d+", str(g.count), g.title)  # "1 image(s)…" -> total across pages
            return list(groups.values())

        def issue_out(owner: str, i: UxIssue, pages: list[str]) -> dict:
            where = ", ".join(urlparse(p).path or "/" for p in pages)
            ev = ledger.add(f"UX ({owner}): {i.title} — {where}", pages[0], "website", 0.85,
                            extracted_text=("; ".join(i.examples) or i.detail or "")[:900] or None)
            return {**i.to_dict(), "pages": pages, "evidence_id": ev.id}

        def practice_out(owner: str, url: str, practices: dict) -> dict:
            out = {}
            for key, snippet in practices.items():
                if snippet:
                    ev = ledger.add(f"{owner} website: {PRACTICES[key].lower()}", url, "website", 0.8,
                                    extracted_text=snippet[:500])
                    out[key] = ev.id
                else:
                    out[key] = None
            return out

        client_issues = sorted((issue_out(rec.client.name, i, pages) for i, pages in merged(client["issues"])),
                               key=lambda x: (_rank(x["severity"]), x["category"]))
        client_practices = practice_out(rec.client.name, client["pages"][0], client["practices"])
        companies = [{"name": rec.client.name, "is_client": True, "pages": client["pages"], "score": client["score"],
                      "browser": client["browser"], "practices": client_practices,
                      "issue_counts": {sv: sum(1 for i in client_issues if i["severity"] == sv) for sv in SEVERITY_ORDER}}]
        for c, res in zip(competitors, comps):
            if not res or not res["pages"]:
                continue
            issues = [issue_out(c["name"], i, pages) for i, pages in merged(res["issues"]) if _rank(i.severity) <= 1]
            companies.append({"name": c["name"], "is_client": False, "competitor_id": c["id"], "pages": res["pages"],
                              "score": res["score"], "browser": res["browser"],
                              "practices": practice_out(c["name"], res["pages"][0], res["practices"]),
                              "issue_counts": {sv: sum(1 for i in res["issues"] if i.severity == sv)
                                               for sv in SEVERITY_ORDER},
                              "top_issues": issues[:5]})
        rivals = [x for x in companies if not x["is_client"]]

        # Gaps ------------------------------------------------------------------
        gaps: list[Gap] = []
        for cat, (name, fid) in QUALITY_GAPS.items():
            hits = [i for i in client_issues if i["category"] == cat and i["severity"] in ("high", "medium")]
            if not hits:
                continue
            worst = min((i["severity"] for i in hits), key=_rank)
            wcag = sorted({i["wcag"] for i in hits if i.get("wcag")})
            gaps.append(Gap(
                feature_id=fid if fid and fid in ctx.taxonomy else None, name=name, category="UX", gap_type=GapType.UX,
                description=f"{len(hits)} {cat} issue(s) on the client's site (highest: {worst})"
                            + (f"; WCAG {', '.join(wcag)}" if wcag else "") + ": "
                            + "; ".join(i["title"] for i in hits[:4]) + ".",
                competitors_with=[], evidence_ids=[i["evidence_id"] for i in hits][:8],
                confidence=0.8 if worst == "high" else 0.7, basis=Basis.EVIDENCE))
        cta = next((i for i in client_issues if i["key"] == "primary-cta"), None)
        if cta:
            gaps.append(Gap(name=CTA_GAP, category="UX", gap_type=GapType.UX,
                            description="The homepage has no visible sign-up, trial or demo action.",
                            competitors_with=[x["competitor_id"] for x in rivals
                                              if x["practices"].get("self_serve_cta") or x["practices"].get("sales_cta")],
                            evidence_ids=[cta["evidence_id"]], confidence=0.75, basis=Basis.EVIDENCE))
        pricing_gap_names = {g["name"] for g in ctx.data("pricing_analysis").get("gaps", [])}
        for key, name in PRACTICE_GAPS.items():
            if client_practices.get(key) or not rivals:
                continue
            if key == "self_serve_cta" and ("Free trial" in pricing_gap_names or cta):
                continue  # already covered by the pricing gap / the missing-CTA gap
            having = [x for x in rivals if x["practices"].get(key)]
            if len(having) / len(rivals) >= 0.5:
                gaps.append(Gap(
                    name=name, category="UX", gap_type=GapType.UX,
                    description=f"{len(having)} of {len(rivals)} competitors offer this on their website "
                                f"({', '.join(x['name'] for x in having)}); not found on the client's homepage.",
                    competitors_with=[x["competitor_id"] for x in having],
                    evidence_ids=[x["practices"][key] for x in having][:6], confidence=0.6, basis=Basis.INFERRED))
        client_overall = client["score"]["overall"] if client["score"] else None
        rival_scores = [x["score"]["overall"] for x in rivals if x["score"] and x["browser"] == client["browser"]]
        market = {"client_score": client_overall,
                  "competitor_median_score": round(statistics.median(rival_scores)) if rival_scores else None,
                  "competitors_audited": len(rivals)}
        findings = []
        if client_overall is not None and rival_scores and client_overall <= market["competitor_median_score"] - BELOW_MARGIN:
            # A summary, not a roadmap item: the specific gaps above carry the work.
            findings.append(Finding(
                category="ux", title=f"{BELOW_GAP}: {client_overall}/100 vs median {market['competitor_median_score']}/100",
                detail="Same automated checks on the client's pages and competitors' homepages.",
                evidence_ids=[i["evidence_id"] for i in client_issues if i["severity"] == "high"][:6],
                confidence=0.65, basis=Basis.INFERRED))
        findings += [Finding(category=f"ux.{i['category']}", title=f"[{i['severity']}] {i['title']}",
                            detail=i["recommendation"], evidence_ids=[i["evidence_id"]], confidence=0.8)
                    for i in client_issues if i["severity"] != "info"]
        return AgentResult(
            findings=findings,
            evidence=[ledger.get(i["evidence_id"]) for i in client_issues if ledger.get(i["evidence_id"])],
            confidence=0.75 if client["browser"] else 0.6,
            data={"enabled": True, "client": companies[0], "issues": client_issues, "companies": companies,
                  "market": market, "gaps": [g.model_dump(mode="json") for g in gaps], "notes": notes,
                  "method": score([], set())["method"],
                  "disclaimer": "Automated checks catch only part of WCAG; confirm with manual testing and assistive "
                                "technology. Page speed is a lab measurement from the analysis server."},
        )
