"""Detailed feature and workflow extraction from a company's own pages (client and competitors alike).

Root causes this addresses: one prompt used to share a 30,000-character budget across every page (so a
features or docs page contributed a sliver), and the schema asked for a name and one sentence. Here:

* only pages that can describe the offering are read, best first (features, product, docs, help,
  integrations, pricing, home…), in **batches of whole pages**; features found in several batches are merged;
* every **factual** detail — how it works, key functionality, inputs, outputs, platforms, integrations,
  prerequisites, limitations — must carry a verbatim quote that is found in the cited page
  (``Grounder.ground_verified``); details whose quote is not found are dropped, never shown as fact;
* the analyst's **interpretation** — purpose, intended users, use cases, benefits — is kept separate and
  labelled as interpretation, never as an observed fact;
* a feature whose own quote is not found in its source is kept as ``unverified``;
* workflows are built from **documented steps** (each grounded) and say how much of them is documented;
  steps the model had to infer are labelled; a workflow with no documented step is discarded.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from cip.connectors.research.pages import CAPABILITY_KINDS, PRIORITY, kind_of
from cip.core.evidence import EvidenceLedger
from cip.core.grounding import Grounder, SourceDoc, pages_to_prompt
from cip.core.llm import LLMClient, LLMError, LLMUnavailable
from cip.core.taxonomy import Taxonomy

BATCH_CHARS = 30_000
FACT_FIELDS = ("how_it_works", "key_functionality", "inputs", "outputs", "platforms", "integrations",
               "prerequisites", "limitations")
INTERPRETATION_FIELDS = ("purpose", "intended_users", "use_cases", "benefits")
MAX_FEATURES = 40


class _Quoted(BaseModel):
    text: str
    source_url: str = ""
    quote: str = ""


class _LLMFeature(BaseModel):
    name: str
    feature_id: str | None = None
    category: str = ""
    description: str = ""
    status: str = "available"
    source_url: str = ""
    quote: str = ""
    how_it_works: list[_Quoted] = Field(default_factory=list)
    key_functionality: list[_Quoted] = Field(default_factory=list)
    inputs: list[_Quoted] = Field(default_factory=list)
    outputs: list[_Quoted] = Field(default_factory=list)
    platforms: list[_Quoted] = Field(default_factory=list)
    integrations: list[_Quoted] = Field(default_factory=list)
    prerequisites: list[_Quoted] = Field(default_factory=list)
    limitations: list[_Quoted] = Field(default_factory=list)
    purpose: str = ""
    intended_users: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)


class _LLMFeatures(BaseModel):
    features: list[_LLMFeature] = Field(default_factory=list)


class _LLMActor(BaseModel):
    role: str
    responsibilities: str = ""


class _LLMStep(BaseModel):
    action: str
    actor: str = ""
    kind: str = "user"  # user | system
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    decision: str = ""
    source_url: str = ""
    quote: str = ""
    inferred: bool = False


class _LLMWorkflow(BaseModel):
    name: str
    objective: str = ""
    trigger: str = ""
    actors: list[_LLMActor] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    steps: list[_LLMStep] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    automation: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    outcome: str = ""
    business_value: str = ""
    related_features: list[str] = Field(default_factory=list)


class _LLMWorkflows(BaseModel):
    workflows: list[_LLMWorkflow] = Field(default_factory=list)


FEATURE_PROMPT = """You are a senior product analyst documenting a company's offering from its own web pages.
List every product feature or service capability the sources describe — not marketing slogans.
For each feature give:
- name; the matching taxonomy feature_id if one fits (else null); a category (e.g. Scheduling, Billing);
- description: 2-4 sentences on what it does and how, using only what the sources say;
- status: "available", or "partial" when the sources state a limitation or a basic version;
- source_url and a short verbatim quote proving the feature exists;
- FACT lists — how_it_works, key_functionality, inputs, outputs, platforms (web, iOS, Android, API…),
  integrations (third-party services), prerequisites, limitations — each item with the source_url and a short
  verbatim quote copied exactly from that source. Leave a list empty when the sources do not say it;
- INTERPRETATION — purpose (the business problem it addresses), intended_users, use_cases, benefits: your
  analysis of the sources; keep it specific to this company, no generic marketing language.
Never invent features, integrations, platforms or limitations. Sources may be in any language: copy quotes
verbatim in the source's language and write values in English."""

WORKFLOW_PROMPT = """You are a business analyst documenting how a company's product is used, from its own web
pages (product, features, documentation, help). Describe the major workflows the product supports, e.g. how a
customer books, pays, onboards or gets support. For each workflow: name, business objective, trigger, actors
(role and responsibilities), prerequisites, ordered steps, integrations, automation, exceptions/error
handling, final outcome, business value and related features.
For every step: the action, who does it (actor), kind ("user" or "system"), inputs, outputs, any decision
point, and the source_url with a short verbatim quote copied exactly from that source. If a step is needed to
connect documented steps but is not described in the sources, set inferred=true and leave the quote empty.
Only describe workflows the sources actually document; never describe internal implementation the sources do
not disclose. Sources may be in any language: copy quotes verbatim and write values in English."""


def capability_docs(pages: list[dict]) -> list[SourceDoc]:
    """The pages that can describe the offering, best first (features, product, docs…), as source documents."""
    kept = [p for p in pages if kind_of(p.get("url", "")) in CAPABILITY_KINDS and p.get("text")]
    kept.sort(key=lambda p: -PRIORITY.get(kind_of(p["url"]), 3))
    return [SourceDoc(p["url"], f"{p.get('title', '')}\n{' / '.join(p.get('headings', []))}\n{p.get('text', '')}",
                      "website") for p in kept]


def batches(docs: list[SourceDoc], max_batches: int, budget: int = BATCH_CHARS) -> list[list[SourceDoc]]:
    out: list[list[SourceDoc]] = [[]]
    used = 0
    for d in docs:
        size = min(len(d.text), budget)
        if out[-1] and used + size > budget:
            if len(out) == max_batches:
                break
            out.append([])
            used = 0
        out[-1].append(d)
        used += size
    return [b for b in out if b]


def _key(name: str, feature_id: str | None) -> str:
    return feature_id or re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _merge_list(a: list[dict], b: list[dict]) -> list[dict]:
    out, seen = list(a), {x["text"].lower() for x in a}
    for x in b:
        if x["text"].lower() not in seen:
            seen.add(x["text"].lower())
            out.append(x)
    return out


async def extract_features(llm: LLMClient, ledger: EvidenceLedger, docs: list[SourceDoc], company: str,
                           product: str, taxonomy: Taxonomy, *, max_batches: int = 3, context: str = ""
                           ) -> tuple[list[dict], list[str]]:
    """Detailed, grounded feature inventory. Returns (features, errors); raises LLMUnavailable without an LLM."""
    grounder = Grounder(ledger, docs)
    merged: dict[str, dict] = {}
    errors: list[str] = []
    for batch in batches(docs, max_batches):
        try:
            result = await llm.complete_json(
                FEATURE_PROMPT,
                f"Company: {company}\nProduct: {product}\n{context}\nTaxonomy:\n{taxonomy.describe_for_prompt()}\n\n"
                f"{pages_to_prompt(batch, BATCH_CHARS)}", _LLMFeatures)
        except LLMUnavailable:
            raise
        except LLMError as exc:
            errors.append(f"Feature extraction failed for one batch of pages: {exc}")
            continue
        for f in result.features:
            item = _feature_item(f, grounder, company, taxonomy)
            key = _key(item["name"], item["feature_id"])
            if key not in merged:
                merged[key] = item
                continue
            m = merged[key]
            if len(item["description"]) > len(m["description"]):
                m["description"] = item["description"]
            for f_ in FACT_FIELDS:
                m["facts"][f_] = _merge_list(m["facts"][f_], item["facts"][f_])
            for f_ in ("intended_users", "use_cases", "benefits"):
                m["interpretation"][f_] = list(dict.fromkeys(m["interpretation"][f_] + item["interpretation"][f_]))
            m["interpretation"]["purpose"] = m["interpretation"]["purpose"] or item["interpretation"]["purpose"]
            m["evidence_ids"] = list(dict.fromkeys(m["evidence_ids"] + item["evidence_ids"]))
            m["sources"] = list(dict.fromkeys(m["sources"] + item["sources"]))
            if item["verification"] == "documented":
                m["verification"] = "documented"
            if item["status"] == "available" and item["verification"] == "documented":
                m["status"] = "available"
            m["dropped_details"] += item["dropped_details"]
    return list(merged.values())[:MAX_FEATURES], errors


def _feature_item(f: _LLMFeature, grounder: Grounder, company: str, taxonomy: Taxonomy) -> dict:
    ev = grounder.ground_verified(f"{company} offers {f.name}", f.source_url, f.quote, 0.85)
    facts: dict[str, list[dict]] = {}
    dropped = 0
    for field in FACT_FIELDS:
        facts[field] = []
        for d in getattr(f, field):
            e = grounder.ground_verified(f"{f.name} — {field.replace('_', ' ')}: {d.text}", d.source_url, d.quote,
                                         0.8)
            if e and d.text.strip():
                facts[field].append({"text": d.text.strip(), "evidence_id": e.id, "source_url": d.source_url})
            else:
                dropped += 1  # a detail whose quote is not in the source is never shown as fact
    detail_ev = [x["evidence_id"] for v in facts.values() for x in v]
    fid = f.feature_id if f.feature_id in taxonomy else None
    return {
        "name": f.name.strip(), "feature_id": fid, "category": f.category or (taxonomy.get(fid).category_name
                                                                              if fid else ""),
        "description": f.description.strip(), "status": "partial" if f.status == "partial" else "available",
        "verification": "documented" if ev else "unverified",
        "facts": facts,
        "interpretation": {"purpose": f.purpose.strip(), "intended_users": f.intended_users,
                           "use_cases": f.use_cases, "benefits": f.benefits},
        "evidence_ids": ([ev.id] if ev else []) + detail_ev,
        "sources": list(dict.fromkeys([u for u in [f.source_url, *(x["source_url"] for v in facts.values() for x in v)]
                                       if u])),
        "dropped_details": dropped,
        # kept for older consumers (report, review): first user type and the purpose
        "user_type": f.intended_users[0] if f.intended_users else "", "business_purpose": f.purpose.strip(),
        "technology": [], "basis": "evidence" if ev else "inferred",
    }


async def extract_workflows(llm: LLMClient, ledger: EvidenceLedger, docs: list[SourceDoc], company: str,
                            product: str) -> tuple[list[dict], list[str]]:
    """Workflows from documented steps. Returns (workflows, errors); raises LLMUnavailable without an LLM."""
    if not docs:
        return [], []
    grounder = Grounder(ledger, docs)
    batch = batches(docs, 1)[0]
    try:
        result = await llm.complete_json(WORKFLOW_PROMPT,
                                         f"Company: {company}\nProduct: {product}\n\n{pages_to_prompt(batch, BATCH_CHARS)}",
                                         _LLMWorkflows)
    except LLMUnavailable:
        raise
    except LLMError as exc:
        return [], [f"Workflow extraction failed: {exc}"]
    out = []
    for w in result.workflows:
        steps = []
        for i, s in enumerate(w.steps, 1):
            e = None if s.inferred else grounder.ground_verified(f"{w.name}, step {i}: {s.action}", s.source_url,
                                                                 s.quote, 0.8)
            steps.append({"order": i, "action": s.action, "actor": s.actor, "kind": "system" if s.kind == "system"
                          else "user", "inputs": s.inputs, "outputs": s.outputs, "decision": s.decision,
                          "documented": bool(e), "evidence_id": e.id if e else None,
                          "source_url": s.source_url if e else None})
        documented = sum(1 for s in steps if s["documented"])
        if not documented:
            continue  # nothing of it is in the sources: not shown
        share = documented / len(steps)
        out.append({
            "name": w.name, "objective": w.objective, "trigger": w.trigger,
            "actors": [a.model_dump() for a in w.actors], "prerequisites": w.prerequisites, "steps": steps,
            "integrations": w.integrations, "automation": w.automation, "exceptions": w.exceptions,
            "outcome": w.outcome, "business_value": w.business_value, "related_features": w.related_features,
            "documented_steps": documented,
            "verification": "documented" if share >= 0.6 and documented >= 2 else "partial",
            "evidence_ids": [s["evidence_id"] for s in steps if s["evidence_id"]],
        })
    return out, []
