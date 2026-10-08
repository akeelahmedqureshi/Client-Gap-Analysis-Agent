"""Deterministic matching of client needs to the internal knowledge base (BRS 7.18, PRD 10.19).

    Client gap -> required capability -> internal capability -> technology -> previous project -> case study

Matching is rule-based and runs locally: internal knowledge never leaves the platform (it is not sent
to the LLM). A record only matches when it is relevant to the *capability* itself (taxonomy id,
category or matching keywords); industry, technology and AI/automation overlap then raise the
confidence. Every match carries plain-language reasons, so a reviewer can see why it was suggested.

Only *approved* records are matched. ``client_facing`` on a match says whether it may be used in
client-facing output (approved + client-facing visibility); case studies may additionally be named
only when ``reference_allowed`` is set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cip.core.taxonomy import Taxonomy

MIN_CONFIDENCE = 0.35
AUTOMATION_HINTS = ("automation", "workflow", "automated", "orchestration", "scheduling", "reminder", "rpa")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"and", "or", "the", "a", "an", "of", "for", "to", "in", "with", "on", "by", "management", "system"}


@dataclass
class Need:
    """Something the client could use: a gap or recommendation, normalised for matching."""

    id: str
    name: str
    feature_id: str | None = None
    category_id: str | None = None
    ai: bool = False
    automation: bool = False
    text: str = ""
    # Further capabilities that satisfy the need (e.g. a process improvement's solution capabilities).
    feature_ids: tuple[str, ...] = ()


@dataclass
class Match:
    record_id: str
    version: int
    kind: str
    title: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    client_facing: bool = False
    reference_allowed: bool = False
    technologies: list[str] = field(default_factory=list)
    customer_name: str | None = None
    outcomes: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _tokens(text: str) -> set[str]:
    # A light stem (first 6 letters) so "automated" matches "automation" and "scheduling" matches "schedule".
    return {w[:6] for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOP}


def _norm(values: list[str] | None) -> set[str]:
    return {v.strip().lower() for v in values or [] if v and v.strip()}


def is_matchable(record: dict) -> bool:
    return record.get("status") == "approved"


def is_client_facing(record: dict) -> bool:
    return record.get("status") == "approved" and record.get("visibility") == "client_facing"


def need_from_gap(gap: dict, taxonomy: Taxonomy, process: dict | None = None) -> Need:
    """``process``: the business-process opportunity behind a process gap (agents/business_process.py)."""
    tf = taxonomy.get(gap["feature_id"]) if gap.get("feature_id") else None
    text = f"{gap.get('name', '')} {gap.get('description', '')}"
    extra: tuple[str, ...] = ()
    if process:
        from cip.agents.business_process import process_by_id

        definition = process_by_id(process["process_id"])
        extra = definition.solution_features if definition else ()
    return Need(
        id=gap.get("id", ""), name=gap.get("name", ""), feature_id=tf.id if tf else None,
        category_id=tf.category_id if tf else None,
        ai=bool(tf and tf.ai) or gap.get("gap_type") == "ai" or bool(process and process.get("ai")),
        automation=any(h in text.lower() for h in AUTOMATION_HINTS) or (tf is not None and tf.id == "ai.automation")
        or bool(process and process.get("automation")),
        text=text, feature_ids=extra,
    )


def score_record(need: Need, record: dict, taxonomy: Taxonomy, *, industry: str | None,
                 stack: set[str]) -> Match | None:
    tags = _norm(record.get("capability_tags"))
    reasons: list[str] = []
    relevance = 0.0
    tf = taxonomy.get(need.feature_id) if need.feature_id else None

    solution = next((f for f in need.feature_ids if f.lower() in tags), None)
    if need.feature_id and need.feature_id.lower() in tags:
        relevance += 0.55
        reasons.append(f"Tagged with the required capability ({tf.name if tf else need.feature_id})")
    elif solution:
        sf = taxonomy.get(solution)
        relevance += 0.45
        reasons.append(f"Tagged with a capability the improvement needs ({sf.name if sf else solution})")
    elif need.category_id and need.category_id.lower() in tags:
        relevance += 0.3
        reasons.append(f"Tagged with the capability area ({tf.category_name if tf else need.category_id})")

    # Keyword overlap between the need and the record's tags/title/summary.
    need_words = _tokens(need.name) | (_tokens(" ".join(tf.keywords)) if tf else set())
    record_words = _tokens(" ".join([*tags, record.get("title", ""), record.get("summary", "")]))
    shared = sorted(need_words & record_words)
    if shared:
        relevance += min(0.35, 0.12 * len(shared))
        words = [w for w in _WORD.findall(f"{record.get('title', '')} {' '.join(tags)}".lower()) if w[:6] in shared]
        reasons.append("Describes related work: " + ", ".join(dict.fromkeys(words or shared))[:80])
    if relevance == 0:
        return None  # not relevant to the capability itself: industry/tech overlap alone is not enough

    score = relevance
    if need.ai and record.get("ai"):
        score += 0.12
        reasons.append("Delivered AI capability")
    if need.automation and record.get("automation"):
        score += 0.12
        reasons.append("Delivered automation capability")
    if industry:
        ind_words = _tokens(industry)
        hits = [i for i in record.get("industries") or [] if _tokens(i) & ind_words]
        if hits:
            score += 0.15
            reasons.append(f"Same industry ({', '.join(hits[:2])})")
    techs = [t for t in record.get("technologies") or [] if t.strip().lower() in stack]
    if techs:
        score += min(0.15, 0.05 * len(techs))
        reasons.append(f"Uses the client's technology ({', '.join(techs[:3])})")
    if record.get("kind") == "case_study" and (record.get("outcomes") or "").strip():
        score += 0.05
        reasons.append("Case study with documented outcomes")

    return Match(
        record_id=record["id"], version=int(record.get("version", 1)), kind=record.get("kind", "capability"),
        title=record.get("title", ""), confidence=round(min(1.0, score), 3), reasons=reasons,
        client_facing=is_client_facing(record),
        reference_allowed=is_client_facing(record) and bool(record.get("reference_allowed")),
        technologies=list(record.get("technologies") or [])[:6],
        customer_name=record.get("customer_name") if record.get("reference_allowed") else None,
        outcomes=(record.get("outcomes") or "")[:500],
    )


def match_need(need: Need, records: list[dict], taxonomy: Taxonomy, *, industry: str | None = None,
               stack: set[str] | None = None, limit: int = 3) -> list[Match]:
    stack_l = {s.lower() for s in stack or set()}
    found = [m for r in records if is_matchable(r)
             if (m := score_record(need, r, taxonomy, industry=industry, stack=stack_l))
             and m.confidence >= MIN_CONFIDENCE]
    found.sort(key=lambda m: (-m.confidence, m.kind != "case_study", m.title))
    return found[:limit]
