"""Capability Matching agent: map prioritised client needs to the internal knowledge base.

Input: the run's recommendations (and the gaps behind them) plus the organization's *approved*
knowledge-base records, loaded into ``ctx.knowledge`` by the runner. Matching is deterministic
(see ``core/matching.py``); internal knowledge is never sent to the LLM.

Output: per recommendation, up to three matches with confidence and reasons, flagged
``client_facing`` when they may be used in outreach; a per-record demand view; and the knowledge-base
snapshot (record ids + versions) used, so the result stays reproducible after records change.
"""

from __future__ import annotations

from cip.agents.base import Agent, RunContext
from cip.core.matching import is_client_facing, is_matchable, match_need, need_from_gap
from cip.core.schemas import AgentResult, Basis, Finding


class CapabilityMatchingAgent(Agent):
    name = "capability_matching"
    description = "Match prioritised opportunities to our own capabilities, projects and case studies"
    after = ("opportunity_prioritization",)

    async def run(self, ctx: RunContext) -> AgentResult:
        records = [r for r in ctx.knowledge if is_matchable(r)]
        prio = ctx.data("opportunity_prioritization")
        recommendations = prio.get("recommendations", [])
        gaps = {g["id"]: g for g in ctx.data("gap_analysis").get("gaps", [])}
        snapshot = {"records_considered": len(records),
                    "client_facing_records": sum(1 for r in records if is_client_facing(r)),
                    "records": [{"id": r["id"], "version": r.get("version", 1)} for r in records]}
        if not records:
            return AgentResult(
                confidence=0.3,
                data={"knowledge_base": snapshot, "matches": [], "by_record": [], "unmatched": []},
                findings=[Finding(category="capability_matching", title="Knowledge base has no approved records",
                                  detail="Add and approve capabilities, projects and case studies under Knowledge "
                                         "Base to match opportunities to your delivery experience.",
                                  confidence=1.0, basis=Basis.INFERRED)],
                next_actions=["Add approved records to the knowledge base"],
            )

        profile = ctx.data("client_research").get("profile", {})
        industry = profile.get("industry") or ctx.record.client.industry
        stack = {t["name"] for p in ctx.data("code_analysis").get("profiles", []) for t in p.get("technologies", [])}
        stack |= set(ctx.record.project.technology)

        matches, unmatched, findings = [], [], []
        by_record: dict[str, dict] = {}
        for rec in recommendations:
            gap = gaps.get(rec["gap_id"], {"id": rec["gap_id"], "name": rec["feature"]})
            need = need_from_gap(gap, ctx.taxonomy)
            need.id = rec["id"]
            found = match_need(need, records, ctx.taxonomy, industry=industry, stack=stack)
            if not found:
                unmatched.append(rec["feature"])
                continue
            matches.append({"recommendation_id": rec["id"], "gap_id": rec["gap_id"], "need": rec["feature"],
                            "phase": rec.get("phase"), "matches": [m.to_dict() for m in found]})
            best = found[0]
            findings.append(Finding(
                category="capability_matching", title=f"{rec['feature']}: {best.title}",
                detail="; ".join(best.reasons) + ("" if best.client_facing else " (internal only)"),
                evidence_ids=rec.get("evidence_ids", [])[:3], confidence=best.confidence, basis=Basis.INFERRED))
            for m in found:
                entry = by_record.setdefault(m.record_id, {"record_id": m.record_id, "title": m.title,
                                                           "kind": m.kind, "client_facing": m.client_facing,
                                                           "needs": []})
                entry["needs"].append(rec["feature"])

        demand = sorted(by_record.values(), key=lambda e: (-len(e["needs"]), e["title"]))
        covered = len(matches) / len(recommendations) if recommendations else 0.0
        return AgentResult(
            confidence=round(0.4 + 0.5 * covered, 3),
            findings=findings,
            data={"knowledge_base": snapshot, "matches": matches, "by_record": demand, "unmatched": unmatched,
                  "coverage": round(covered, 3), "industry": industry},
            next_actions=[f"No internal capability matches: {', '.join(unmatched[:5])}"] if unmatched else [],
        )
