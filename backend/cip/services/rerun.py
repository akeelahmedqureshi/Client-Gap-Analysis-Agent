"""Partial re-runs and lifecycle helpers for analysis runs (BRS 26.2, 18; PRD 10.31, 10.38).

A partial re-run never overwrites history: it creates a **new run** (a new analysis version) that
copies every completed stage that is not being refreshed — with its evidence and approval decisions —
and re-runs the selected stages plus everything downstream of them. The earlier run stays as it was,
so versions can be compared (the Changes tab compares a run with the previous completed one).
"""

from __future__ import annotations

import copy
from collections import deque
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.agents.orchestrator import default_agents
from cip.db.models import AgentExecution, AnalysisRun, Approval, EvidenceRecord, ReviewOverride, User

ACTIVE = ("queued", "running", "awaiting_approval", "paused")
FINISHED = ("completed", "completed_with_errors", "failed", "cancelled")
# Named refreshes (BRS 26.2); any agent name is accepted too.
STAGE_PRESETS: dict[str, tuple[str, ...]] = {
    "industry": ("industry_market",),
    "competitors": ("competitor_research",),
    "opportunities": ("business_process", "opportunity_prioritization"),
    "sales": ("sales_intelligence",),
    "outreach": ("outreach",),
    "report": ("report",),
}


class RerunError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def dependents() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {a.name: set() for a in default_agents()}
    for a in default_agents():
        for dep in (*a.requires, *a.after):
            out[dep].add(a.name)
    return out


def expand(stages: list[str]) -> tuple[list[str], set[str]]:
    """(selected agents, selected + everything downstream)."""
    names = {a.name for a in default_agents()}
    selected: list[str] = []
    for s in stages:
        group = STAGE_PRESETS.get(s) or ((s,) if s in names else None)
        if group is None:
            raise RerunError(422, f"Unknown stage '{s}'. Use one of {sorted(STAGE_PRESETS)} or an agent name.")
        selected += [g for g in group if g not in selected]
    graph, todo, out = dependents(), deque(selected), set(selected)
    while todo:
        for nxt in graph[todo.popleft()]:
            if nxt not in out:
                out.add(nxt)
                todo.append(nxt)
    return selected, out


async def active_run(session: AsyncSession, project_id: str) -> AnalysisRun | None:
    return (await session.execute(select(AnalysisRun).where(
        AnalysisRun.project_id == project_id, AnalysisRun.status.in_(ACTIVE))
        .order_by(AnalysisRun.created_at.desc()))).scalars().first()


async def create_rerun(session: AsyncSession, run: AnalysisRun, stages: list[str], user: User,
                       patch: Callable[[str, dict], dict] | None = None) -> AnalysisRun:
    """``patch(agent, result)`` may correct a reused stage's result before it is copied (human review)."""
    if run.status not in FINISHED:
        raise RerunError(409, "The run is still in progress; wait for it to finish or cancel it first.")
    existing = await active_run(session, run.project_id)
    if existing:
        raise RerunError(409, f"An analysis of this project is already in progress ({existing.id}).")
    selected, rerun = expand(stages)
    new = AnalysisRun(org_id=run.org_id, project_id=run.project_id, created_by=user.id, status="queued",
                      scoring_weights=dict(run.scoring_weights or {}), approved_gates=list(run.approved_gates or []),
                      parent_run_id=run.id, rerun_stages=selected,
                      config=dict(run.config) if run.config else None)  # same configuration versions
    session.add(new)
    await session.flush()
    execs = (await session.execute(select(AgentExecution).where(AgentExecution.run_id == run.id))).scalars().all()
    for e in execs:
        if e.agent in rerun or e.status != "completed":
            continue
        result = patch(e.agent, copy.deepcopy(e.result)) if patch and e.result else e.result
        session.add(AgentExecution(run_id=new.id, agent=e.agent, status=e.status, attempts=e.attempts,
                                   result=result, error=e.error, started_at=e.started_at, finished_at=e.finished_at))
    for ev in (await session.execute(select(EvidenceRecord).where(EvidenceRecord.run_id == run.id))).scalars():
        session.add(EvidenceRecord(id=ev.id, run_id=new.id, org_id=ev.org_id, claim=ev.claim, source_url=ev.source_url,
                                   source_type=ev.source_type, extracted_text=ev.extracted_text,
                                   repository_path=ev.repository_path, line_range=ev.line_range,
                                   confidence=ev.confidence, collected_at=ev.collected_at))
    # Earlier approval decisions carry over (an approved gate stays approved; a rejected one stays skipped).
    for a in (await session.execute(select(Approval).where(Approval.run_id == run.id,
                                                           Approval.status.in_(["approved", "rejected"])))).scalars():
        session.add(Approval(run_id=new.id, agent=a.agent, gate=a.gate, title=a.title, what=a.what, why=a.why,
                             target=a.target, data_analyzed=a.data_analyzed, status=a.status,
                             decided_by=a.decided_by, decided_at=a.decided_at))
    # Reviewers' applied overrides carry over to every later version (they key on stable labels).
    for o in (await session.execute(select(ReviewOverride).where(ReviewOverride.run_id == run.id,
                                                                 ReviewOverride.status == "applied"))).scalars():
        session.add(ReviewOverride(org_id=o.org_id, run_id=new.id, kind=o.kind, target_id=o.target_id,
                                   target_label=o.target_label, field=o.field, value=o.value, note=o.note,
                                   status="applied", applied_run_id=new.id, created_by=o.created_by,
                                   created_by_email=o.created_by_email, created_at=o.created_at))
    return new
