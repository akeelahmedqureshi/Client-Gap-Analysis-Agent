"""Human review of a run: overrides and decisions (BRS 17, PRD 10.34).

Reviewers can correct what the analysis concluded:

* ``capability_status`` — the client's status of a capability (available, partial, not publicly
  identified, **confirmed missing**). A reviewer's knowledge is the only way a capability becomes
  *confirmed missing*;
* ``competitor`` — exclude a competitor that is not relevant;
* ``gap`` — reject a gap, approve it, or request rework;
* ``recommendation`` — change its priority, roadmap phase, business category or business impact.

Every override is keyed by a **stable label** (capability id, competitor domain, gap name,
recommendation name), because ids change whenever a stage is re-run. Applying the pending overrides
creates a new run version (``api/routes/review.py``): reused stages get the corrections written into
their copied results (``patch``), and the agents that are re-run apply the same overrides to their own
output (``apply_to``), so the corrections also survive any later refresh. The matrix, gaps, scores,
roadmap, sales summary, outreach and report therefore all reflect the review.
"""

from __future__ import annotations

from cip.connectors.research.web import registrable_domain
from cip.core.categories import CATEGORIES
from cip.core.schemas import FeatureStatus
from cip.core.scoring import PHASE_LABELS
from cip.core.taxonomy import Taxonomy, load_taxonomy

KINDS = ("capability_status", "competitor", "gap", "recommendation")
RECOMMENDATION_FIELDS = {
    "priority": ("high", "medium", "low"),
    "phase": tuple(PHASE_LABELS),
    "business_category": CATEGORIES,
    "business_impact": None,  # free text
}
# Stages re-run when an override of each kind is applied (plus everything downstream).
STAGES = {
    # Not industry_market: re-running it would re-run competitor discovery for a status correction.
    "capability_status": ("business_process", "feature_comparison"),
    "competitor": ("pricing_analysis", "app_store", "ux_review", "feature_comparison"),
    "gap.reject": ("opportunity_prioritization",),
    "gap": ("quality_assurance",),
    "recommendation": ("enhancement_planning", "capability_matching"),
}


class ReviewError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def resolve(kind: str, target_id: str, field: str, value: str, results: dict[str, dict],
            taxonomy: Taxonomy | None = None) -> str:
    """Validate an override against the run's results and return its stable label."""
    data = lambda agent: (results.get(agent) or {}).get("data", {})  # noqa: E731
    if kind == "capability_status":
        if target_id not in (taxonomy or load_taxonomy()):
            raise ReviewError(422, f"Unknown capability '{target_id}'")
        if value not in {s.value for s in FeatureStatus}:
            raise ReviewError(422, "Status must be available, partial, unknown (not publicly identified) or missing")
        return target_id
    if kind == "competitor":
        if value != "exclude":
            raise ReviewError(422, "A competitor can only be excluded")
        research = data("competitor_research")
        comp = next((c for c in research.get("landscape", []) + research.get("competitors", [])
                     if c["id"] == target_id), None)
        if comp is None:
            raise ReviewError(404, "Competitor not found in this run")
        return registrable_domain(comp.get("url") or "") or comp["name"].lower()
    if kind == "gap":
        if value not in ("reject", "approve", "rework"):
            raise ReviewError(422, "A gap decision must be reject, approve or rework")
        gap = next((g for g in data("gap_analysis").get("gaps", []) if g["id"] == target_id), None)
        if gap is None:
            raise ReviewError(404, "Gap not found in this run")
        return gap["name"]
    if kind == "recommendation":
        if field not in RECOMMENDATION_FIELDS:
            raise ReviewError(422, f"Field must be one of {sorted(RECOMMENDATION_FIELDS)}")
        allowed = RECOMMENDATION_FIELDS[field]
        if allowed is not None and value not in allowed:
            raise ReviewError(422, f"{field} must be one of {list(allowed)}")
        rec = next((r for r in data("opportunity_prioritization").get("recommendations", [])
                    if r["id"] == target_id), None)
        if rec is None:
            raise ReviewError(404, "Recommendation not found in this run")
        return rec["feature"]
    raise ReviewError(422, f"Kind must be one of {list(KINDS)}")


def stages_for(overrides: list[dict]) -> list[str]:
    out: list[str] = []
    for o in overrides:
        key = "gap.reject" if o["kind"] == "gap" and o["value"] == "reject" else o["kind"]
        out += [s for s in STAGES[key] if s not in out]
    return out


def _by(overrides: list[dict], kind: str) -> list[dict]:
    return [o for o in overrides if o["kind"] == kind]


def apply_to(agent: str, data: dict, overrides: list[dict], taxonomy: Taxonomy | None = None) -> dict:
    """Apply the reviewers' overrides relevant to ``agent`` to its output data (in place)."""
    if not overrides:
        return data
    tax = taxonomy or load_taxonomy()
    if agent == "product_features":
        obs, inv = data.setdefault("observations", {}), data.setdefault("inventory", [])
        for o in _by(overrides, "capability_status"):
            fid, status = o["label"], o["value"]
            if fid not in tax:
                continue
            ob = obs.setdefault(fid, {"feature_id": fid, "evidence_ids": [], "confidence": 0.9})
            ob.update(status=status, confidence=0.95, basis="evidence", reviewed=True,
                      notes=f"Set by reviewer {o.get('by') or ''}: {o.get('note') or 'no note'}".replace("  ", " "))
            inv[:] = [i for i in inv if i.get("feature_id") != fid]
            if status in ("available", "partial"):
                inv.append({"name": tax.get(fid).name, "feature_id": fid, "description": o.get("note") or "",
                            "status": status, "user_type": "", "business_purpose": "",
                            "evidence_ids": ob.get("evidence_ids", []), "technology": [], "basis": "evidence",
                            "reviewed": True})
    elif agent == "competitor_research":
        excluded = {o["label"]: o for o in _by(overrides, "competitor")}
        if excluded:
            def key(c: dict) -> str:
                return registrable_domain(c.get("url") or "") or c["name"].lower()

            gone = {key(c): c for c in data.get("landscape", []) + data.get("competitors", []) if key(c) in excluded}
            data["competitors"] = [c for c in data.get("competitors", []) if key(c) not in excluded]
            data["landscape"] = [c for c in data.get("landscape", []) if key(c) not in excluded]
            for i, c in enumerate(data["landscape"], 1):
                c["rank"] = i
            ranks = {c["id"]: c["rank"] for c in data["landscape"]}
            for c in data["competitors"]:
                c["rank"] = ranks.get(c["id"], c.get("rank"))
            for k, c in gone.items():
                data.setdefault("rejected", []).append({
                    "name": c["name"], "url": c.get("url"),
                    "rationale": f"Excluded by reviewer: {excluded[k].get('note') or 'not relevant'}"})
    elif agent == "gap_analysis":
        decisions = {o["label"]: o for o in _by(overrides, "gap")}
        kept = []
        for g in data.get("gaps", []):
            d = decisions.get(g["name"])
            if d and d["value"] == "reject":
                data.setdefault("rejected_by_review", []).append({"name": g["name"], "note": d.get("note", ""),
                                                                  "by": d.get("by")})
                continue
            if d:
                g["review"] = {"decision": d["value"], "note": d.get("note", ""), "by": d.get("by")}
            kept.append(g)
        data["gaps"] = kept
    elif agent == "opportunity_prioritization":
        fields = _by(overrides, "recommendation")
        if fields:
            opps = {o["name"]: o for o in data.get("opportunities", [])}
            for r in data.get("recommendations", []):
                for o in (x for x in fields if x["label"] == r["feature"]):
                    r[o["field"]] = o["value"]
                    r.setdefault("review", []).append({"field": o["field"], "value": o["value"],
                                                       "note": o.get("note", ""), "by": o.get("by")})
                    opp = opps.get(r["feature"])
                    if opp and o["field"] in ("priority", "business_category"):
                        opp[o["field"]] = o["value"]
            roadmap: dict[str, list[str]] = {k: [] for k in PHASE_LABELS}
            for r in data.get("recommendations", []):
                roadmap.setdefault(r["phase"], []).append(r["id"])
            data["roadmap"] = roadmap
    return data


def patch(agent: str, result: dict, overrides: list[dict], taxonomy: Taxonomy | None = None) -> dict:
    """Write the overrides into a copied (reused) agent result; the reviewed run is never changed."""
    result["data"] = apply_to(agent, result.get("data") or {}, overrides, taxonomy)
    if agent == "gap_analysis":
        rejected = {r["name"] for r in result["data"].get("rejected_by_review", [])}
        result["findings"] = [f for f in result.get("findings", []) if f.get("title") not in rejected]
    return result
