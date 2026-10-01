"""Gap Analysis Agent: missing, partial, technology, UX and AI gaps."""

from __future__ import annotations

from cip.agents.base import Agent, RunContext
from cip.core.schemas import AgentResult, Basis, FeatureStatus, Finding, Gap, GapType

UX_CATEGORIES = {"experience"}

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
    description = "Identify missing, partial, technology, UX and AI gaps"
    after = ("feature_comparison", "code_analysis", "pricing_analysis", "security_review", "app_store")

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
                uncertain = client_status == "unknown"
                gaps.append(Gap(
                    feature_id=tf.id, name=tf.name, category=tf.category_name, gap_type=gtype,
                    description=(f"{', '.join(names)} offer{'s' if len(names) == 1 else ''} {tf.name}; "
                                 + ("the client's status could not be confirmed."
                                    if uncertain else "no implementation was found for the client.")),
                    competitors_with=with_it, evidence_ids=comp_ev + ([ab] if ab else []),
                    confidence=round((0.45 if uncertain else 0.7) * (0.6 + 0.4 * r["competitor_coverage"]), 3),
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
                    description=f"No AI capability was detected in the client's product. {f.name} is an emerging "
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
        # Mobile app gaps from the app-store analysis -----------------------------
        apps = ctx.data("app_store")
        for g in (Gap.model_validate(x) for x in apps.get("gaps", [])):
            same = next((x for x in gaps if g.feature_id and x.feature_id == g.feature_id), None)
            if same:  # e.g. "Native mobile app" already found by the comparison: merge the evidence
                same.evidence_ids = list(dict.fromkeys(same.evidence_ids + g.evidence_ids))
                same.competitors_with = list(dict.fromkeys(same.competitors_with + g.competitors_with))
            else:
                gaps.append(g)
        if apps.get("client_has_app") and "ux.mobile_app" in ctx.taxonomy:
            # A verified store listing proves the client has an app, whatever the website text says.
            gaps = [g for g in gaps if g.feature_id != "ux.mobile_app"]
            client_obs = {**client_obs, "ux.mobile_app": {
                "status": FeatureStatus.AVAILABLE.value,
                "evidence_ids": [a["evidence_id"] for a in apps["client_apps"]]}}

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
