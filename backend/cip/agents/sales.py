"""Sales Intelligence and Outreach agents (BRS 7.19-7.20, PRD 10.20-10.21, 10.50).

**Sales Intelligence** turns the run's structured state into a concise, sales-ready summary: pain
points, top three competitive gaps, top three improvements, the best AI / automation / cost-saving /
revenue opportunity, a conversation angle, relevant capabilities and case studies, a contact and a next
step. It is assembled deterministically from the same gaps, scores and matches as the report, so the
two never disagree. Claim safety:

* only gaps with confidence >= ``MIN_CLAIM_CONFIDENCE`` and real competitor evidence lead the summary;
  weaker ones are listed under ``claim_safety`` instead of driving the conversation;
* security issues and pricing opinions are internal-only pain points;
* only approved, client-facing knowledge-base matches are offered for client use.

**Outreach** writes the personalised email from the summary's claim-safe facts (see
``core/outreach.py``). Every draft is claim-checked; people edit, regenerate and approve it through the
API (``api/routes/sales.py``).
"""

from __future__ import annotations

from cip.agents.base import Agent, RunContext
from cip.core.outreach import Fact, OutreachInput, generate_email
from cip.core.schemas import AgentResult, Basis, Finding

MIN_CLAIM_CONFIDENCE = 0.5
CLIENT_GAP_TYPES = ("missing", "partial", "ai", "ux", "pricing")
AUTOMATION_FEATURES = {
    "ai.automation", "workflow.automation", "workflow.scheduling", "comm.sms", "comm.push", "comm.email",
    "billing.invoicing", "ai.document_processing", "platform.webhooks", "platform.crm_integration",
}
COST_SAVING_FEATURES = {
    "ai.assistant", "ai.automation", "workflow.automation", "ai.document_processing", "billing.invoicing",
    "ux.self_service_portal", "ux.onboarding", "workflow.scheduling",
}
PHASE_LABEL = {"phase_1_quick_wins": "quick win", "phase_2_growth": "medium term",
               "phase_3_major": "major initiative", "phase_4_strategic": "strategic"}
PREFERRED_MAILBOXES = ("sales", "hello", "info", "contact", "partnerships", "business", "office")


def _pick_contact(ctx: RunContext, profile: dict) -> dict | None:
    if ctx.record.client.email:
        return {"email": ctx.record.client.email, "source": "CSV"}
    emails = [c for c in profile.get("contacts", []) if c.get("type") == "email"]
    emails.sort(key=lambda c: next((i for i, p in enumerate(PREFERRED_MAILBOXES)
                                    if c["value"].split("@")[0].lower() == p), 99))
    return {"email": emails[0]["value"], "source": emails[0].get("source")} if emails else None


def _names(ids: list[str], comp_names: dict[str, str]) -> list[str]:
    return [comp_names[i] for i in ids if i in comp_names]


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def _opp_view(o: dict, rec: dict | None, gap: dict) -> dict:
    return {"gap_id": o["gap_id"], "name": o["name"], "summary": o.get("business_opportunity", ""),
            "impact": rec.get("business_impact", "") if rec else "",
            "revenue": o.get("revenue_opportunity", ""), "score": o["score"]["total"],
            "phase": PHASE_LABEL.get(rec["phase"]) if rec else None,
            "expected_outcome": rec.get("expected_outcome") if rec else None,
            "evidence_ids": (rec or gap).get("evidence_ids", [])[:5], "basis": o.get("basis", "estimate")}


class SalesIntelligenceAgent(Agent):
    name = "sales_intelligence"
    description = "Build a concise, sales-ready summary from the analysis"
    after = ("capability_matching",)

    async def run(self, ctx: RunContext) -> AgentResult:
        rec_ = ctx.record
        profile = ctx.data("client_research").get("profile", {})
        client = profile.get("name") or rec_.client.name
        product = rec_.project.name
        industry = profile.get("industry") or rec_.client.industry
        competitors = ctx.data("competitor_research").get("competitors", [])
        comp_names = {c["id"]: c["name"] for c in competitors}
        n_comp = len(competitors)
        gaps = {g["id"]: g for g in ctx.data("gap_analysis").get("gaps", [])}
        prio = ctx.data("opportunity_prioritization")
        opps = sorted(prio.get("opportunities", []), key=lambda o: -o["score"]["total"])
        recs = {r["gap_id"]: r for r in prio.get("recommendations", [])}
        recommendations = sorted(prio.get("recommendations", []), key=lambda r: -r["score"]["total"])
        matching = ctx.data("capability_matching")
        matches_by_gap = {m["gap_id"]: m["matches"] for m in matching.get("matches", [])}

        # --- pain points --------------------------------------------------------------------
        pains: list[dict] = []
        reviews = ctx.data("app_store").get("client_reviews") or {}
        for t in reviews.get("themes", []):
            if t.get("negative", 0) >= 2 and t.get("share_negative", 0) >= 0.3:
                pains.append({"text": f"{t['negative']} of {reviews['total']} recent app-store reviews complain about "
                                      f"{t['label'].lower()}", "source": "app_reviews",
                              "evidence_ids": t.get("evidence_ids", [])[:3], "confidence": 0.8, "internal_only": False})
        for r in ctx.data("app_store").get("requests", []):
            if r.get("count", 0) >= 2:
                pains.append({"text": f"{r['count']} app-store reviews ask for {r['name']}", "source": "app_reviews",
                              "evidence_ids": r.get("evidence_ids", [])[:3], "confidence": 0.75,
                              "internal_only": False})
        for i in ctx.data("ux_review").get("issues", []):
            if i.get("severity") == "high" and i.get("category") in ("conversion", "mobile"):
                pages = len(i.get("pages") or [i.get("page")])
                pains.append({"text": f"{i['title']} on the public website ({pages} page{'s' if pages != 1 else ''} checked)",
                              "source": "ux", "evidence_ids": [i["evidence_id"]] if i.get("evidence_id") else [],
                              "confidence": 0.7, "internal_only": False})
        pricing = ctx.data("pricing_analysis")
        if pricing.get("position") == "above":
            pains.append({"text": "Entry price is above the market median of the priced competitors",
                          "source": "pricing", "evidence_ids": [], "confidence": 0.6, "internal_only": True})
        security = [g for g in gaps.values() if g["gap_type"] == "security"]
        if security:
            pains.append({"text": f"{len(security)} website security finding(s) — discuss only after engagement",
                          "source": "security", "evidence_ids": [e for g in security[:3] for e in g["evidence_ids"][:1]],
                          "confidence": 0.7, "internal_only": True})

        # --- top competitive gaps (claim-safe) -----------------------------------------------
        top_gaps, excluded = [], []
        ranked = [gaps[o["gap_id"]] for o in opps if o["gap_id"] in gaps]
        for g in ranked:
            if g["gap_type"] not in CLIENT_GAP_TYPES or not g.get("competitors_with"):
                continue
            if g["confidence"] < MIN_CLAIM_CONFIDENCE or g.get("basis") == "estimate":
                excluded.append({"name": g["name"], "confidence": g["confidence"],
                                 "reason": "confidence below the client-facing threshold"})
                continue
            names = _names(g["competitors_with"], comp_names)
            top_gaps.append({
                "gap_id": g["id"], "name": g["name"], "competitors": names,
                "coverage": f"{len(g['competitors_with'])} of {n_comp}",
                "statement": f"{_join(names)} offer{'s' if len(names) == 1 else ''} {g['name']} "
                             f"({len(g['competitors_with'])} of {n_comp} competitors analysed); it was not publicly "
                             f"identified for {product}.",
                "why": (recs.get(g["id"]) or {}).get("business_impact", ""),
                "evidence_ids": g["evidence_ids"][:5], "confidence": g["confidence"],
            })
            if len(top_gaps) == 3:
                break

        # --- opportunities -------------------------------------------------------------------
        def best(pred) -> dict | None:
            for o in opps:
                g = gaps.get(o["gap_id"], {})
                if g and g["gap_type"] != "security" and pred(o, g):
                    return _opp_view(o, recs.get(o["gap_id"]), g)
            return None

        ai_opp = best(lambda o, g: g["gap_type"] == "ai" or (g.get("feature_id") or "").startswith("ai."))
        automation_opp = best(lambda o, g: g.get("feature_id") in AUTOMATION_FEATURES
                              or "automat" in g["name"].lower())
        has_cost_factor = any("cost_saving" in o.get("factors", {}) for o in opps)
        if has_cost_factor:
            cost_opp = best(lambda o, g: o["factors"].get("cost_saving", 0) >= 3)
        else:
            cost_opp = best(lambda o, g: g.get("feature_id") in COST_SAVING_FEATURES)
        revenue_opp = None
        by_revenue = sorted(opps, key=lambda o: (-o.get("factors", {}).get("revenue_potential", 0), -o["score"]["total"]))
        for o in by_revenue:
            g = gaps.get(o["gap_id"])
            if g and g["gap_type"] != "security":
                revenue_opp = _opp_view(o, recs.get(o["gap_id"]), g)
                break

        improvements = [{"recommendation_id": r["id"], "gap_id": r["gap_id"], "feature": r["feature"],
                         "phase": PHASE_LABEL.get(r["phase"], r["phase"]), "outcome": r["expected_outcome"],
                         "business_impact": r["business_impact"], "score": r["score"]["total"],
                         "evidence_ids": r["evidence_ids"][:5], "basis": r.get("basis", "estimate")}
                        for r in recommendations if gaps.get(r["gap_id"], {}).get("gap_type") != "security"][:3]

        # --- our capabilities ------------------------------------------------------------------
        focus = [g["gap_id"] for g in top_gaps] + [i["gap_id"] for i in improvements] + \
            [o["gap_id"] for o in (ai_opp, automation_opp, cost_opp) if o]
        client_facing, internal, case_studies, seen = [], [], [], set()
        for gid in dict.fromkeys(focus):
            for m in matches_by_gap.get(gid, []):
                if m["record_id"] in seen:
                    continue
                seen.add(m["record_id"])
                need = gaps.get(gid, {}).get("name", "")
                entry = {"record_id": m["record_id"], "title": m["title"], "kind": m["kind"], "need": need,
                         "confidence": m["confidence"], "reasons": m["reasons"][:3], "outcomes": m.get("outcomes", "")}
                (client_facing if m["client_facing"] else internal).append(entry)
                if m["kind"] == "case_study" and m.get("reference_allowed"):
                    case_studies.append({**entry, "customer": m.get("customer_name")})

        # --- angle and next step ---------------------------------------------------------------
        lead = top_gaps[0] if top_gaps else None
        # The idea to raise: prefer one that is not already one of the gaps we lead with.
        led = {g["gap_id"] for g in top_gaps[:2]}
        idea = next((o for o in (ai_opp, automation_opp) if o and o["gap_id"] not in led), ai_opp or automation_opp)
        angle_parts = []
        if lead:
            angle_parts.append(f"Lead with the competitive comparison: {lead['statement']}")
        pain = next((p for p in pains if not p["internal_only"]), None)
        if pain:
            angle_parts.append(f"Back it with customer evidence: {pain['text']}.")
        if improvements:
            angle_parts.append(f"Propose {improvements[0]['feature']} ({improvements[0]['phase']}) as the first step.")
        if idea:
            angle_parts.append(f"Then open the {'AI' if idea is ai_opp else 'automation'} conversation: {idea['name']}.")
        if client_facing:
            angle_parts.append(f"Credibility: {client_facing[0]['title']}.")
        angle = " ".join(angle_parts) or "Not enough evidence for a specific angle; start with a discovery call."
        topics = [g["name"] for g in top_gaps[:2]] or [i["feature"] for i in improvements[:2]]
        next_step = (f"Offer a 30-minute call to walk through the competitor comparison on {' and '.join(topics)}"
                     if topics else "Offer a 30-minute discovery call")
        next_step += f", and share “{case_studies[0]['title']}” as a reference." if case_studies else "."

        # --- outreach input (claim-safe facts only) -------------------------------------------
        facts: list[Fact] = []
        products = profile.get("products") or []
        if products and products[0].get("description"):
            facts.append(Fact("product", products[0]["description"], products[0].get("evidence_ids", [])[:2]))
        for g in top_gaps[:2]:
            both = "both " if len(g["competitors"]) == 2 else ""
            verb = "offers" if len(g["competitors"]) == 1 else "offer"
            facts.append(Fact("gap", g["statement"], g["evidence_ids"],
                              email_text=f"{_join(g['competitors'])} {both}{verb} {g['name']}, which we could not "
                                         f"find publicly for {product}."))
        facts += [Fact("pain", p["text"] + ".", p["evidence_ids"]) for p in pains if not p["internal_only"]][:2]
        if idea:
            value = (idea.get("impact") or "").rstrip(".")
            facts.append(Fact("opportunity", f"{idea['name']}" + (f" — {value}" if value else ""),
                              idea["evidence_ids"], estimate=True,
                              email_text=f"{idea['name']}" + (f" ({value[0].lower() + value[1:]})." if value else ".")))
        for c in client_facing[:1]:
            if any(cs["record_id"] == c["record_id"] for cs in case_studies):
                continue  # told as a case study below
            facts.append(Fact("capability", c["title"] + (f" ({c['outcomes']})" if c["outcomes"] else ""),
                              email_text=f"we have delivered {c['title']}"
                                         + (f", with results such as: {c['outcomes']}" if c["outcomes"] else "") + "."))
        for cs in case_studies[:1]:
            facts.append(Fact("case_study", f"{cs['title']} for {cs['customer']}"
                                            + (f": {cs['outcomes']}" if cs["outcomes"] else ""),
                              email_text=f"we delivered {cs['title']} for {cs['customer']}"
                                         + (f" — {cs['outcomes']}" if cs["outcomes"] else "") + "."))
        forbidden = [r["title"] for r in ctx.knowledge if r.get("visibility") != "client_facing"]
        forbidden += [r["customer_name"] for r in ctx.knowledge
                      if r.get("customer_name") and not (r.get("visibility") == "client_facing"
                                                        and r.get("reference_allowed"))]
        contact = _pick_contact(ctx, profile)
        outreach_input = OutreachInput(
            client=client, product=product, industry=industry, facts=facts,
            competitors_allowed=sorted({n for g in top_gaps[:2] for n in g["competitors"]}),
            competitors_all=sorted(comp_names.values()), forbidden_terms=sorted(set(forbidden)),
            top_gaps=[g["name"] for g in top_gaps[:2]], opportunity=idea["name"] if idea else None,
            capability=client_facing[0]["title"] if client_facing else None,
            recipient=contact["email"] if contact else None,
        )

        summary = {
            "client": client, "product": product, "industry": industry,
            "pain_points": pains, "top_gaps": top_gaps, "top_improvements": improvements,
            "ai_opportunity": ai_opp, "automation_opportunity": automation_opp,
            "cost_saving_opportunity": cost_opp, "revenue_opportunity": revenue_opp,
            "conversation_angle": angle, "next_step": next_step,
            "relevant_capabilities": client_facing, "internal_capabilities": internal, "case_studies": case_studies,
            "contact": contact,
            "claim_safety": {"min_confidence": MIN_CLAIM_CONFIDENCE, "excluded_gaps": excluded,
                             "notes": ["Cost-saving and revenue opportunities are estimates."]
                             + (["Security findings are internal-only."] if security else [])},
            "outreach_input": outreach_input.to_dict(),
        }
        findings = [Finding(category="sales", title=f"Top gap: {g['name']}", detail=g["statement"],
                            evidence_ids=g["evidence_ids"], confidence=g["confidence"], basis=Basis.INFERRED)
                    for g in top_gaps]
        findings.append(Finding(category="sales", title="Conversation angle", detail=angle, confidence=0.6,
                                basis=Basis.ESTIMATE))
        return AgentResult(confidence=0.75 if top_gaps else 0.45, findings=findings, data=summary,
                           next_actions=[next_step])


class OutreachAgent(Agent):
    name = "outreach"
    description = "Draft a personalised, claim-checked outreach email"
    requires = ("sales_intelligence",)

    async def run(self, ctx: RunContext) -> AgentResult:
        inp = OutreachInput.from_dict(ctx.data("sales_intelligence")["outreach_input"])
        email = await generate_email(ctx.llm, inp)
        return AgentResult(
            confidence=0.7 if not email.problems else 0.4,
            findings=[Finding(category="outreach", title=email.subject,
                              detail=f"Draft written by the {email.generated_by}; review and approve before sending.",
                              evidence_ids=[e for f in inp.facts for e in f.evidence_ids][:10],
                              confidence=0.6, basis=Basis.ESTIMATE)],
            data={"to": inp.recipient, "subject": email.subject, "body": email.body,
                  "generated_by": email.generated_by, "problems": email.problems, "notes": email.notes,
                  "facts": [f.to_dict() for f in inp.facts]},
            errors=email.notes if email.generated_by == "template" and email.notes else [],
        )
