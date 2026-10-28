"""Product Feature Agent: builds the client's evidence-backed feature inventory.

Signals are merged from three independent sources and mapped onto the
normalized taxonomy:

* website/documentation text (keyword matches + grounded LLM extraction)
* source code (dependency / path signals + grounded LLM code review)
* the client's own CSV record (existing features, description)
"""

from __future__ import annotations

from collections import defaultdict

from cip.agents.base import Agent, RunContext
from cip.connectors.research.pages import CAPABILITY_KINDS, OFFERING_KINDS, kind_of
from cip.core import positioning
from cip.core.evidence import snippet
from cip.core import offering
from cip.core.grounding import SourceDoc
from cip.core.llm import LLMUnavailable
from cip.core.schemas import AgentResult, Basis, FeatureObservation, FeatureStatus, Finding
from cip.core.taxonomy import Taxonomy


def combine_signals(signals: list[dict], coverage: dict[str, bool]) -> dict[str, FeatureObservation]:
    """Merge per-source signals into one observation per taxonomy feature, graded by evidence strength.

    * documented — a grounded quote from the client's own pages (LLM extraction): its stated status;
    * evidenced  — code dependencies, the client's records, a match on a page where the company states its
      offering (home, product, features, pricing, docs, help, integrations), several keywords on one page or the
      same capability on two or more pages: available;
    * mentioned  — one keyword on one other page (about, customers…): "not publicly identified", with the
      mention kept as a note.
    """
    by_feature: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        by_feature[s["feature_id"]].append(s)
    out: dict[str, FeatureObservation] = {}
    for fid, sigs in by_feature.items():
        sources = {s["source"] for s in sigs}
        documented = [s for s in sigs if s["source"] == "llm_extraction"]
        web = [s for s in sigs if s["source"] == "website"]
        # A folder or file name ("path") alone is as weak as a single keyword; dependencies, code review, the
        # client's records and app listings are independent evidence.
        independent = sources - {"website", "llm_extraction", "path"}
        web_strong = (any(s.get("keywords", 1) >= 2 or kind_of(s.get("page") or "") in OFFERING_KINDS for s in web)
                      or len({s.get("page") for s in web}) >= 2 or (bool(web) and "path" in sources))
        if documented:
            grade = "documented"
            status = (FeatureStatus.AVAILABLE if any(s["status"] == "available" for s in documented) or independent
                      or web_strong else FeatureStatus.PARTIAL)
        elif independent or web_strong:
            grade, status = "evidenced", FeatureStatus.AVAILABLE
        else:
            grade, status = "mentioned", FeatureStatus.UNKNOWN
        best = max(s["confidence"] for s in sigs)
        conf = min(0.97, best + 0.08 * (len(sources) - 1))
        ev_ids = list(dict.fromkeys(s["evidence_id"] for s in sigs))
        note = f"signals: {', '.join(sorted(sources))}"
        if grade == "mentioned":
            note = "mentioned once on the website; not confirmed as an offered capability"
        out[fid] = FeatureObservation(
            feature_id=fid, status=status, evidence_ids=ev_ids, confidence=round(conf, 3), notes=note,
            basis=Basis.EVIDENCE if grade != "mentioned" else Basis.INFERRED, verification=grade,
        )
    return out


def fill_missing(obs: dict[str, FeatureObservation], taxonomy: Taxonomy, coverage: dict[str, bool]) -> None:
    """Features with no signal are "not publicly identified" — never "missing" (BRS 5.3, 7.3).

    The confidence says how thoroughly the client was inspected, so a gap built on a well-inspected
    absence ranks above one built on thin coverage; the status itself stays UNKNOWN either way.
    """
    inspected = [k for k, v in coverage.items() if v]
    for f in taxonomy.features:
        if f.id in obs:
            continue
        if len(inspected) >= 2 or coverage.get("code"):
            obs[f.id] = FeatureObservation(feature_id=f.id, status=FeatureStatus.UNKNOWN,
                                           confidence=0.6 if len(inspected) >= 2 else 0.45,
                                           notes=f"not found in the client's {', '.join(inspected)}",
                                           basis=Basis.INFERRED)
        else:
            obs[f.id] = FeatureObservation(feature_id=f.id, status=FeatureStatus.UNKNOWN, confidence=0.2,
                                           notes="insufficient coverage", basis=Basis.INFERRED)


class ProductFeatureAgent(Agent):
    name = "product_features"
    description = "Extract the client project's features from website, docs, code and records"
    after = ("client_research", "code_analysis")

    async def run(self, ctx: RunContext) -> AgentResult:
        rec = ctx.record
        research = ctx.data("client_research")
        pages = research.get("project_pages", []) + research.get("pages", [])
        code = ctx.data("code_analysis")
        signals: list[dict] = list(code.get("feature_signals", []))
        ledger = ctx.ledger
        errors: list[str] = []

        # Website keyword matches, only on pages that can describe the offering (not blog posts, job adverts
        # or legal pages: connectors/research/pages.py) ------------------------------------------------
        for page in pages:
            if kind_of(page["url"]) not in CAPABILITY_KINDS:
                continue
            text = " ".join([page.get("title", ""), page.get("description", ""), " ".join(page.get("headings", [])),
                             page.get("text", "")])
            for fid, kws in ctx.taxonomy.match_text(text).items():
                ev = ledger.add(f"Website mentions {ctx.taxonomy.get(fid).name}", page["url"], "website",
                                0.6 if len(kws) > 1 else 0.5, extracted_text=snippet(text, kws[0]))
                signals.append({"feature_id": fid, "status": "available", "evidence_id": ev.id,
                                "source": "website", "confidence": ev.confidence, "keywords": len(kws),
                                "page": page["url"]})

        # The company's own product announcements (client research found them on its blog / news pages) are
        # evidence of a capability; other blog text is not.
        for a in research.get("announcements", []):
            if a.get("is_product") and a.get("evidence_id"):
                for fid in ctx.taxonomy.match_text(a.get("title", "")):
                    signals.append({"feature_id": fid, "status": "available", "evidence_id": a["evidence_id"],
                                    "source": "announcement", "confidence": 0.75})

        # Language versions declared with hreflang = localization (multi-language analysis) ----------
        with_alt = next((p for p in pages if len({k.split("-")[0] for k in (p.get("alternates") or {})}) >= 2), None)
        if with_alt and "ux.localization" in ctx.taxonomy:
            langs = sorted({k.split("-")[0] for k in with_alt["alternates"]})
            ev = ledger.add(f"Website is published in {len(langs)} languages ({', '.join(langs)})", with_alt["url"],
                            "website", 0.85, extracted_text="hreflang: " + ", ".join(sorted(with_alt["alternates"])))
            signals.append({"feature_id": "ux.localization", "status": "available", "evidence_id": ev.id,
                            "source": "website", "confidence": 0.85, "keywords": 2, "page": with_alt["url"]})

        # CSV-declared features ----------------------------------------------
        csv_source = f"csv://row/{rec.row_number}"
        for feat in rec.project.existing_features + ([rec.project.description] if rec.project.description else []):
            for fid in ctx.taxonomy.match_text(feat):
                ev = ledger.add(f"Client records list {ctx.taxonomy.get(fid).name}", csv_source, "csv", 0.7,
                                extracted_text=feat)
                signals.append({"feature_id": fid, "status": "available", "evidence_id": ev.id,
                                "source": "csv", "confidence": 0.7})

        # Detailed feature inventory and documented workflows, grounded (core/offering.py) ----------------
        inventory: list[dict] = []
        documented_workflows: list[dict] = []
        docs = offering.capability_docs(pages)
        csv_text = "\n".join(filter(None, [rec.project.description, "; ".join(rec.project.existing_features),
                                           rec.project.notes]))
        if csv_text:
            docs.append(SourceDoc(csv_source, csv_text, "csv"))
        if docs:
            try:
                inventory, errs = await offering.extract_features(
                    ctx.llm, ledger, docs, rec.client.name, rec.project.name, ctx.taxonomy,
                    max_batches=ctx.settings.feature_extraction_batches)
                errors += errs
                documented_workflows, errs = await offering.extract_workflows(
                    ctx.llm, ledger, [d for d in docs if d.source_type == "website"], rec.client.name,
                    rec.project.name)
                errors += errs
            except LLMUnavailable:
                pass
            for item in inventory:
                if item["feature_id"] and item["verification"] == "documented":
                    signals.append({"feature_id": item["feature_id"], "status": item["status"],
                                    "evidence_id": item["evidence_ids"][0], "source": "llm_extraction",
                                    "confidence": 0.85})

        coverage = {"website": bool(pages), "code": bool(code.get("profiles")),
                    "csv": bool(rec.project.existing_features or rec.project.description)}
        observations = combine_signals(signals, coverage)
        fill_missing(observations, ctx.taxonomy, coverage)

        # Features only evidenced by signals get an inventory entry; dependency names
        # (e.g. "stripe" for payments) are listed as the implementing technology.
        known = {i["feature_id"] for i in inventory if i["feature_id"]}
        for fid, ob in observations.items():
            if ob.status in (FeatureStatus.AVAILABLE, FeatureStatus.PARTIAL) and fid not in known:
                tf = ctx.taxonomy.get(fid)
                techs = sorted({ledger.get(e).extracted_text for e in ob.evidence_ids
                                if ledger.get(e) and ledger.get(e).source_type in ("github", "gitlab")
                                and ledger.get(e).extracted_text and len(ledger.get(e).extracted_text) < 60})
                inventory.append({
                    "name": tf.name, "feature_id": fid, "category": tf.category_name, "description": "",
                    "status": ob.status.value, "verification": ob.verification,
                    "facts": {f: [] for f in offering.FACT_FIELDS},
                    "interpretation": {"purpose": "", "intended_users": [], "use_cases": [], "benefits": []},
                    "sources": list(dict.fromkeys(ledger.get(e).source_url for e in ob.evidence_ids if ledger.get(e))),
                    "dropped_details": 0, "user_type": "", "business_purpose": "", "evidence_ids": ob.evidence_ids,
                    "technology": techs, "basis": ob.basis.value})

        findings = [
            Finding(category="feature", title=f"{i['name']} ({i['status']})", detail=i["description"],
                    evidence_ids=i["evidence_ids"], confidence=0.8 if i["basis"] == "evidence" else 0.6,
                    basis=i["basis"])
            for i in inventory
        ]
        counts = defaultdict(int)
        for ob in observations.values():
            counts[ob.status.value] += 1

        # Positioning and workflows (BRS 7.2): verbatim from the client's own pages -----------------
        client = rec.client.name
        site_pages = research.get("pages", []) + research.get("project_pages", [])
        obs_dump = {k: v.model_dump(mode="json") for k, v in observations.items()}
        who = positioning.audiences([positioning._page_text(p) for p in site_pages])
        proposition = positioning.value_proposition(site_pages, ledger, client)
        cases = positioning.use_cases(site_pages, ledger, client, who)
        problems = positioning.customer_problems(site_pages, ledger, client)
        flows = positioning.workflows(site_pages, obs_dump, ctx.taxonomy, ledger, client)
        if proposition:
            findings.append(Finding(category="positioning", title=f"Value proposition: {proposition['statement'][:150]}",
                                    detail=proposition.get("supporting") or "", evidence_ids=proposition["evidence_ids"],
                                    confidence=0.85))
        for w in documented_workflows:
            findings.append(Finding(
                category="workflow", title=f"Workflow “{w['name']}”: {w['documented_steps']} of {len(w['steps'])} "
                                           "steps documented",
                detail=w["objective"] or w["outcome"], evidence_ids=w["evidence_ids"][:6], confidence=0.8,
                basis=Basis.EVIDENCE if w["verification"] == "documented" else Basis.INFERRED))
        for w in flows:
            findings.append(Finding(
                category="workflow", title=f"{w['actor'].title()} workflow “{w['name']}”: {w['supported']} of "
                                           f"{len(w['steps'])} steps supported",
                detail=("Not publicly identified: " + ", ".join(w["next_steps"])) if w["next_steps"] else "All steps visible.",
                evidence_ids=[e for s in w["steps"] for e in s["evidence_ids"]][:6], confidence=0.7,
                basis=Basis.INFERRED))
        return AgentResult(
            findings=findings,
            evidence=[ledger.get(e) for e in dict.fromkeys(
                [e for ob in observations.values() for e in ob.evidence_ids]
                + [e for i in inventory for e in i["evidence_ids"]]
                + [e for w in documented_workflows for e in w["evidence_ids"]]) if ledger.get(e)],
            confidence=0.8 if sum(coverage.values()) >= 2 else 0.5,
            errors=errors,
            data={"observations": {k: v.model_dump() for k, v in observations.items()},
                  "inventory": inventory, "coverage": coverage, "status_counts": dict(counts),
                  "positioning": {"value_proposition": proposition, "use_cases": cases, "customer_problems": problems,
                                  "audiences": who},
                  # catalogue workflows mapped onto the capabilities (inferred), and workflows the company's
                  # own pages document step by step
                  "workflows": flows, "documented_workflows": documented_workflows},
        )
