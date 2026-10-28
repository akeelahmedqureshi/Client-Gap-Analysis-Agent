"""The effective capability data every tab uses: feature observations plus verified app-store listings.

A verified App Store / Google Play listing proves a native mobile app exists even when the website never says
so; the comparison, the gap analysis and the consistency checks all read capabilities through these functions
so they cannot disagree about it.
"""

from __future__ import annotations

MOBILE_APP = "ux.mobile_app"
STORE_NOTE = "verified App Store / Google Play listing"


def _with_app(ob: dict | None, fid: str, evidence: list[str]) -> dict:
    ob = dict(ob or {"feature_id": fid, "status": "unknown", "evidence_ids": [], "confidence": 0.0, "notes": ""})
    if ob.get("status") not in ("available", "partial"):
        ob.update(status="available", verification="evidenced", basis="evidence", notes=STORE_NOTE,
                  confidence=max(ob.get("confidence", 0), 0.9))
    ob["evidence_ids"] = list(dict.fromkeys(list(ob.get("evidence_ids", [])) + evidence))
    return ob


def client_observations(features: dict, apps: dict) -> dict:
    obs = dict(features)
    ev = [a["evidence_id"] for a in (apps or {}).get("client_apps", []) if a.get("evidence_id")]
    if ev:
        obs[MOBILE_APP] = _with_app(obs.get(MOBILE_APP), MOBILE_APP, ev)
    return obs


def competitor_observations(comp: dict, apps: dict) -> list[dict]:
    feats = list(comp.get("features", []))
    entry = next((e for e in (apps or {}).get("competitor_apps", []) if e.get("competitor_id") == comp.get("id")), None)
    ev = [a["evidence_id"] for a in (entry or {}).get("apps", []) if a.get("evidence_id")]
    if not ev:
        return feats
    current = next((o for o in feats if o["feature_id"] == MOBILE_APP), None)
    merged = _with_app(current, MOBILE_APP, ev)
    return [o for o in feats if o["feature_id"] != MOBILE_APP] + [merged]
