"""Product Feature Agent: builds the client's evidence-backed feature inventory.

Signals are merged from three independent sources and mapped onto the
normalized taxonomy:

* website/documentation text (keyword matches + grounded LLM extraction)
* source code (dependency / path signals + grounded LLM code review)
* the client's own CSV record (existing features, description)
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from cip.agents.base import Agent, RunContext
from cip.core.evidence import snippet
from cip.core.grounding import Grounder, SourceDoc, pages_to_prompt
from cip.core.llm import LLMError, LLMUnavailable
from cip.core.schemas import AgentResult, Basis, FeatureObservation, FeatureStatus, Finding
from cip.core.taxonomy import Taxonomy


class _LLMFeature(BaseModel):
    name: str
    feature_id: str | None = None
    description: str = ""
    status: str = "available"
    user_type: str = ""
    business_purpose: str = ""
    source_url: str
    quote: str = ""


class _LLMFeatures(BaseModel):
    features: list[_LLMFeature] = Field(default_factory=list)


SYSTEM_PROMPT = """You are a product analyst. From the provided sources list the product features of the
client's project. For each feature: a short name, the matching taxonomy feature_id if one fits (else null),
a one-sentence description, status ('available' or 'partial'), the primary user type, the business purpose,
the SOURCE url it was found in and a verbatim quote from that source. Only include features the sources
actually state; never infer features that are not mentioned."""


def combine_signals(signals: list[dict], coverage: dict[str, bool]) -> dict[str, FeatureObservation]:
    """Merge per-source signals into one observation per taxonomy feature."""
    by_feature: dict[str, list[dict]] = defaultdict(list)
    for s in signals:
        by_feature[s["feature_id"]].append(s)
    out: dict[str, FeatureObservation] = {}
    for fid, sigs in by_feature.items():
        sources = {s["source"] for s in sigs}
        strong = [s for s in sigs if s["status"] == "available" and s["confidence"] >= 0.6]
        if strong or len(sources) >= 2:
            status = FeatureStatus.AVAILABLE
        else:
            status = FeatureStatus.PARTIAL
        best = max(s["confidence"] for s in sigs)
        conf = min(0.97, best + 0.08 * (len(sources) - 1))
        ev_ids = list(dict.fromkeys(s["evidence_id"] for s in sigs))
        out[fid] = FeatureObservation(
            feature_id=fid, status=status, evidence_ids=ev_ids, confidence=round(conf, 3),
            notes=f"signals: {', '.join(sorted(sources))}",
            basis=Basis.EVIDENCE if strong else Basis.INFERRED,
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

        # Website keyword matches ------------------------------------------
        for page in pages:
            text = " ".join([page.get("title", ""), page.get("description", ""), " ".join(page.get("headings", [])),
                             page.get("text", "")])
            for fid, kws in ctx.taxonomy.match_text(text).items():
                ev = ledger.add(f"Website mentions {ctx.taxonomy.get(fid).name}", page["url"], "website",
                                0.6 if len(kws) > 1 else 0.5, extracted_text=snippet(text, kws[0]))
                signals.append({"feature_id": fid, "status": "available", "evidence_id": ev.id,
                                "source": "website", "confidence": ev.confidence})

        # CSV-declared features ----------------------------------------------
        csv_source = f"csv://row/{rec.row_number}"
        for feat in rec.project.existing_features + ([rec.project.description] if rec.project.description else []):
            for fid in ctx.taxonomy.match_text(feat):
                ev = ledger.add(f"Client records list {ctx.taxonomy.get(fid).name}", csv_source, "csv", 0.7,
                                extracted_text=feat)
                signals.append({"feature_id": fid, "status": "available", "evidence_id": ev.id,
                                "source": "csv", "confidence": 0.7})

        # LLM feature inventory, grounded ------------------------------------
        inventory: list[dict] = []
        docs = [SourceDoc(p["url"], f"{p.get('title', '')}\n{' / '.join(p.get('headings', []))}\n{p.get('text', '')}",
                          "website") for p in pages]
        csv_text = "\n".join(filter(None, [rec.project.description, "; ".join(rec.project.existing_features),
                                           rec.project.notes]))
        if csv_text:
            docs.append(SourceDoc(csv_source, csv_text, "csv"))
        if docs:
            grounder = Grounder(ledger, docs)
            try:
                result = await ctx.llm.complete_json(
                    SYSTEM_PROMPT,
                    f"Client: {rec.client.name}\nProject: {rec.project.name}\n\nTaxonomy:\n"
                    f"{ctx.taxonomy.describe_for_prompt()}\n\n{pages_to_prompt(docs, 30_000)}",
                    _LLMFeatures,
                )
                for f in result.features:
                    ev = grounder.ground(f"{rec.project.name} feature: {f.name}", f.source_url, f.quote, 0.8)
                    if not ev:
                        continue
                    fid = f.feature_id if f.feature_id in ctx.taxonomy else None
                    inventory.append({"name": f.name, "feature_id": fid, "description": f.description,
                                      "status": "partial" if f.status == "partial" else "available",
                                      "user_type": f.user_type, "business_purpose": f.business_purpose,
                                      "evidence_ids": [ev.id], "technology": [], "basis": "evidence"})
                    if fid:
                        signals.append({"feature_id": fid, "status": "partial" if f.status == "partial" else "available",
                                        "evidence_id": ev.id, "source": "llm_extraction",
                                        "confidence": ev.confidence})
            except LLMUnavailable:
                pass
            except LLMError as exc:
                errors.append(f"LLM feature extraction failed: {exc}")

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
                inventory.append({"name": tf.name, "feature_id": fid, "description": "", "status": ob.status.value,
                                  "user_type": "", "business_purpose": "", "evidence_ids": ob.evidence_ids,
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
        return AgentResult(
            findings=findings,
            evidence=[ledger.get(e) for ob in observations.values() for e in ob.evidence_ids if ledger.get(e)],
            confidence=0.8 if sum(coverage.values()) >= 2 else 0.5,
            errors=errors,
            data={"observations": {k: v.model_dump() for k, v in observations.items()},
                  "inventory": inventory, "coverage": coverage, "status_counts": dict(counts)},
        )
