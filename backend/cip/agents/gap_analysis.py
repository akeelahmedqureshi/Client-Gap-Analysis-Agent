"""Gap Analysis Agent: missing, partial, technology, UX and AI gaps."""

from __future__ import annotations

from cip.agents.base import Agent, RunContext
from cip.core.schemas import AgentResult, Basis, FeatureStatus, Finding, Gap, GapType

UX_CATEGORIES = {"experience"}
CLASSIFICATIONS = {
    "competitive_feature": "Competitive feature gap", "product_functionality": "Product functionality gap",
    "customer_experience": "Customer experience gap", "operational_efficiency": "Operational efficiency gap",
    "ai_automation": "AI and automation opportunity", "pricing_packaging": "Pricing or packaging opportunity",
    "market_positioning": "Market positioning gap", "strategic_product": "Strategic product opportunity",
    "security_compliance": "Security and compliance (internal)",
}
RELEVANT_CLASSES = ("industry_standard", "emerging")


def classify(g: Gap) -> str:
    t = g.gap_type.value
    if t == "ai":
        return "ai_automation" if g.competitors_with or g.basis != Basis.ESTIMATE else "strategic_product"
    if t == "pricing":
        return "market_positioning" if "above market" in g.name.lower() else "pricing_packaging"
    return {"missing": "competitive_feature", "partial": "product_functionality", "ux": "customer_experience",
            "process": "operational_efficiency", "technology": "operational_efficiency",
            "security": "security_compliance"}.get(t, "product_functionality")


def assess(g: Gap, competitors: dict, client_obs: dict, coverage: dict, requests: dict) -> dict[str, dict]:
    """Checks 1-3 of the gap assessment: is the capability (or issue) verified, what does the client offer,
    and does it matter to the client's customers. Checks 4-6 (value, advantage, effort) are scored later."""
    out: dict[str, dict] = {}
    if g.competitors_with:
        grades = []
        for cid in g.competitors_with:
            ob = next((o for o in competitors.get(cid, {}).get("features", []) if o["feature_id"] == g.feature_id), None)
            grades.append((competitors.get(cid, {}).get("name", cid), (ob or {}).get("verification") or "evidenced"))
        strong = [n for n, v in grades if v in ("documented", "evidenced")]
        out["competitor_verified"] = {"passed": bool(strong), "detail": ", ".join(f"{n} ({v})" for n, v in grades)}
    else:
        out["competitor_verified"] = {"passed": g.basis == Basis.EVIDENCE and bool(g.evidence_ids),
                                      "detail": "no competitor involved; " + ("the issue is directly evidenced"
                                                                              if g.basis == Basis.EVIDENCE else
                                                                              "it rests on inference or assumptions")}
    ob = client_obs.get(g.feature_id or "", {})
    status, grade = ob.get("status"), ob.get("verification", "")
    inspected = [k for k, v in coverage.items() if v]
    if g.gap_type.value == "process":
        out["client_status"] = {"passed": False, "detail": "the client's internal process is an assumption to "
                                                           "validate with the client"}
    elif not g.feature_id:
        out["client_status"] = {"passed": g.basis == Basis.EVIDENCE, "detail": "observed in the client's own "
                                                                               "systems or pages"}
    elif status == "missing":
        out["client_status"] = {"passed": True, "detail": "the client's own pages say it is not offered"}
    elif status == "partial":
        out["client_status"] = {"passed": True, "detail": "the client offers a partial version"}
    elif grade == "mentioned":
        out["client_status"] = {"passed": False, "detail": "mentioned once on the client's site; may already exist"}
    elif ob.get("confidence", 0) >= 0.6:
        out["client_status"] = {"passed": True, "detail": f"not publicly identified after inspecting the client's "
                                                          f"{', '.join(inspected)}"}
    else:
        out["client_status"] = {"passed": False, "detail": "little of the client's product could be inspected"}
    share, asks = g.landscape_share, requests.get(g.feature_id or "")
    reasons = []
    if g.market_class in RELEVANT_CLASSES:
        reasons.append(g.market_class.replace("_", " "))
    if share is not None and share >= 0.3:
        reasons.append(f"{share:.0%} of the competitive landscape offers it")
    if asks:
        reasons.append(f"{asks} app-store review(s) ask for it")
    if g.gap_type.value in ("ux", "technology", "security", "pricing") and g.basis == Basis.EVIDENCE:
        reasons.append("observed issue affecting the client's customers or delivery")
    out["relevant"] = {"passed": bool(reasons), "detail": "; ".join(reasons) or "no evidence that the client's "
                                                                                  "customers expect it"}
    return out

# Technology gaps derived from code-analysis debt indicators.
TECH_GAP_RULES: list[tuple[str, str, str, str]] = [
    # (indicator prefix, gap name, category, description)
    ("No automated tests", "Automated test coverage", "Engineering quality",
     "No unit/integration test suites were found; changes carry regression risk."),
    ("No CI/CD", "CI/CD pipeline", "Engineering quality",
     "No continuous integration or deployment pipeline configuration was found."),
    ("No error monitoring", "Observability (error tracking, metrics, tracing)", "Operations",
     "No error monitoring, APM or metrics tooling was detected."),
    ("Dependency lockfile missing", "Reproducible builds (dependency locking)", "Engineering quality",
     "Dependencies are not locked, so builds are not reproducible."),
    ("Legacy dependency", "Framework modernization", "Architecture",
     "End-of-life or legacy framework versions were detected."),
    ("No README", "Developer documentation", "Engineering quality",
     "The repository has no README/developer documentation."),
]


class GapAnalysisAgent(Agent):
    name = "gap_analysis"
    description = "Identify missing, partial, technology, UX, AI, pricing, security and process gaps"
    after = ("feature_comparison", "code_analysis", "pricing_analysis", "security_review", "app_store",
             "ux_review", "business_process")

    async def run(self, ctx: RunContext) -> AgentResult:
        ledger = ctx.ledger
        rows = ctx.data("feature_comparison").get("rows", [])
        competitors = {c["id"]: c for c in ctx.data("competitor_research").get("competitors", [])}
        client_obs = ctx.data("product_features").get("observations", {})
        coverage = ctx.data("product_features").get("coverage", {})
        profiles = ctx.data("code_analysis").get("profiles", [])
        pages = ctx.data("client_research").get("pages") or ctx.data("client_research").get("project_pages") or []
        if profiles:
            absence_src, absence_type = profiles[0]["url"], profiles[0]["provider"]
        elif pages:
            absence_src, absence_type = pages[0]["url"], "website"
        else:
            absence_src, absence_type = f"csv://row/{ctx.record.row_number}", "csv"
        gaps: list[Gap] = []

        def absence_evidence(feature_name: str) -> str | None:
            inspected = [k for k, v in coverage.items() if v]
            if not inspected:
                return None
            ev = ledger.add(f"No evidence of {feature_name} found in client's {', '.join(inspected)}",
                            absence_src, absence_type, 0.5)
            return ev.id

        # Feature gaps vs competitors ----------------------------------------
        for r in rows:
            tf = ctx.taxonomy.get(r["feature_id"])
            with_it = [cid for cid, s in r["competitors"].items() if s in ("available", "partial")]
            comp_ev = [e for cid in with_it for o in competitors[cid]["features"]
                       if o["feature_id"] == r["feature_id"] for e in o["evidence_ids"][:2]]
            names = [competitors[c]["name"] for c in with_it]
            client_status = r["client"]
            if client_status in ("missing", "unknown") and with_it:
                gtype = GapType.AI if tf.ai else GapType.UX if tf.category_id in UX_CATEGORIES else GapType.MISSING
                ab = absence_evidence(tf.name)
                # "missing" only ever comes from positive evidence of absence; silence is "unknown",
                # and how well the client was inspected decides how confident the gap is.
                confirmed = client_status == "missing"
                uncertain = not confirmed and client_obs.get(tf.id, {}).get("confidence", 0) < 0.45
                gaps.append(Gap(
                    feature_id=tf.id, name=tf.name, category=tf.category_name, gap_type=gtype,
                    description=(f"{', '.join(names)} offer{'s' if len(names) == 1 else ''} {tf.name}; "
                                 + ("it is confirmed missing for the client." if confirmed else
                                    "the client's status could not be checked (little of its product was "
                                    "inspected)." if uncertain else
                                    "it is not publicly identified in the client's offering.")),
                    competitors_with=with_it, evidence_ids=comp_ev + ([ab] if ab else []),
                    confidence=round((0.45 if uncertain else 0.8 if confirmed else 0.7)
                                     * (0.6 + 0.4 * r["competitor_coverage"]), 3),
                ))
            elif client_status == "partial" and any(s == "available" for s in r["competitors"].values()):
                client_ev = client_obs.get(tf.id, {}).get("evidence_ids", [])[:2]
                gaps.append(Gap(
                    feature_id=tf.id, name=tf.name, category=tf.category_name,
                    gap_type=GapType.AI if tf.ai else GapType.PARTIAL,
                    description=f"Client has a basic/partial {tf.name}; {', '.join(names)} offer a fuller version.",
                    competitors_with=with_it, evidence_ids=client_ev + comp_ev, confidence=0.6,
                ))

        # AI gaps even without competitor coverage (emerging opportunity) ---------
        client_has_ai = any(client_obs.get(f.id, {}).get("status") in ("available", "partial")
                            for f in ctx.taxonomy.features if f.ai)
        gap_features = {g.feature_id for g in gaps}
        if not client_has_ai:
            ai_feats = sorted((f for f in ctx.taxonomy.features if f.ai and f.id not in gap_features),
                              key=lambda f: -(f.defaults.get("business_value", 0) + f.defaults.get("user_impact", 0)))
            for f in ai_feats[:3]:
                ab = absence_evidence(f.name)
                gaps.append(Gap(
                    feature_id=f.id, name=f.name, category=f.category_name, gap_type=GapType.AI,
                    description=f"No AI capability is publicly identified in the client's product. {f.name} is an emerging "
                                "opportunity (not yet evidenced among the analysed competitors).",
                    evidence_ids=[ab] if ab else [], confidence=0.4, basis=Basis.ESTIMATE,
                ))

        # Technology gaps -----------------------------------------------------
        for prof in profiles:
            debt = prof.get("technical_debt_indicators", [])
            for prefix, name, category, desc in TECH_GAP_RULES:
                hits = [d for d in debt if d.startswith(prefix)]
                if not hits:
                    continue
                ev_ids = [e.id for e in ledger.all()
                          if e.source_url.startswith(prof["url"]) and any(h in e.claim for h in hits)]
                gaps.append(Gap(name=name, category=category, gap_type=GapType.TECHNOLOGY,
                                description=f"{desc} ({prof['full_name']}: {'; '.join(hits)})",
                                evidence_ids=ev_ids[:3], confidence=0.7))
            if prof.get("skipped_sensitive_files"):
                ev_ids = [e.id for e in ledger.all() if "sensitive files committed" in e.claim.lower()
                          and e.source_url.startswith(prof["url"])]
                gaps.append(Gap(name="Secrets management", category="Security", gap_type=GapType.TECHNOLOGY,
                                description="Credential-like files are committed to the repository; adopt a secrets "
                                            "manager and rotate exposed credentials.",
                                evidence_ids=ev_ids[:2], confidence=0.75))
            techs = {t["name"] for t in prof.get("technologies", [])}
            if not prof.get("has_docker") and not ({"Vercel", "Netlify", "Heroku", "Serverless Framework"} & techs):
                ev = ledger.add("No containerization or managed deployment configuration detected", prof["url"],
                                prof["provider"], 0.6)
                gaps.append(Gap(name="Containerized, reproducible deployment", category="Operations",
                                gap_type=GapType.TECHNOLOGY,
                                description="No Dockerfile or platform deployment config was found.",
                                evidence_ids=[ev.id], confidence=0.55))

        # Pricing & packaging gaps from the pricing analysis --------------------
        gaps.extend(Gap.model_validate(g) for g in ctx.data("pricing_analysis").get("gaps", []))
        # Security gaps from the passive security review -------------------------
        gaps.extend(Gap.model_validate(g) for g in ctx.data("security_review").get("gaps", []))
        # Mobile-app and website UX gaps ------------------------------------------
        apps = ctx.data("app_store")
        extra = apps.get("gaps", []) + ctx.data("ux_review").get("gaps", [])
        for g in (Gap.model_validate(x) for x in extra):
            same = next((x for x in gaps if g.feature_id and x.feature_id == g.feature_id), None)
            if same:  # e.g. "Native mobile app" already found by the comparison: merge the evidence
                if same.basis != Basis.EVIDENCE and g.basis == Basis.EVIDENCE:
                    same.description = f"{same.description} {g.description}"
                same.evidence_ids = list(dict.fromkeys(same.evidence_ids + g.evidence_ids))
                same.competitors_with = list(dict.fromkeys(same.competitors_with + g.competitors_with))
            else:
                gaps.append(g)
        # Cost-reduction / automation opportunities in business processes (assumption-based) ----------
        for o in ctx.data("business_process").get("opportunities", []):
            gaps.append(Gap(
                name=o["name"], category="Operations", gap_type=GapType.PROCESS, process_id=o["process_id"],
                description=f"{o['area']}: {o['business_problem']} Improvement: {o['proposed_solution']} "
                            "(The current process and its inefficiency are assumptions.)",
                evidence_ids=o["evidence_ids"][:6], confidence=o["confidence"], basis=Basis.ESTIMATE))
        if apps.get("client_has_app") and "ux.mobile_app" in ctx.taxonomy:
            # A verified store listing proves the client has an app, whatever the website text says.
            gaps = [g for g in gaps if g.feature_id != "ux.mobile_app"]
            client_obs = {**client_obs, "ux.mobile_app": {
                "status": FeatureStatus.AVAILABLE.value,
                "evidence_ids": [a["evidence_id"] for a in apps["client_apps"]]}}

        # Market context from the comparison: how standard the capability is across the landscape.
        row_by_feature = {r["feature_id"]: r for r in rows}
        for g in gaps:
            r = row_by_feature.get(g.feature_id or "")
            if r:
                g.market_class = r.get("market_class")
                g.landscape_share = round(r["top10_count"] / r["top10_total"], 3) if r.get("top10_total") else None
        # Classification, the evidence on each side and checks 1-3 of the assessment.
        requests = {r["feature_id"]: r.get("count", 0) for r in apps.get("requests", []) if r.get("feature_id")}
        for g in gaps:
            g.classification = classify(g)
            if g.feature_id:
                g.client_evidence_ids = list(client_obs.get(g.feature_id, {}).get("evidence_ids", []))[:4]
                g.competitor_evidence = {
                    cid: [e for o in competitors.get(cid, {}).get("features", []) if o["feature_id"] == g.feature_id
                          for e in o["evidence_ids"][:3]] for cid in g.competitors_with}
            g.assessment = assess(g, competitors, client_obs, coverage, requests)
        by_type: dict[str, int] = {}
        for g in gaps:
            g.evidence_ids = ledger.validate_refs(g.evidence_ids)
            by_type[g.gap_type.value] = by_type.get(g.gap_type.value, 0) + 1
        existing = [
            {"feature_id": fid, "name": ctx.taxonomy.get(fid).name, "status": o["status"],
             "evidence_ids": o["evidence_ids"]}
            for fid, o in client_obs.items()
            if o["status"] in (FeatureStatus.AVAILABLE.value, FeatureStatus.PARTIAL.value) and fid in ctx.taxonomy
        ]
        return AgentResult(
            findings=[Finding(category=f"gap.{g.gap_type.value}", title=g.name, detail=g.description,
                              evidence_ids=g.evidence_ids, confidence=g.confidence, basis=g.basis) for g in gaps],
            confidence=0.7,
            data={"gaps": [g.model_dump() for g in gaps], "existing": existing, "counts": by_type},
        )
