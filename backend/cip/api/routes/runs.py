"""Analysis runs, agent state, approvals (human gates), evidence and reports."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.agents.base import RunContext
from cip.agents.orchestrator import default_agents
from cip.api.deps import not_found, require_role
from cip.core.evidence import EvidenceLedger
from cip.core.schemas import NormalizedRecord
from cip.core.scoring import ALL_FACTORS
from cip.db.models import AgentExecution, AnalysisRun, Approval, EvidenceRecord, Project, Report, User
from cip.db.session import get_session
from cip.services.runner import runner

router = APIRouter(prefix="/api/runs", tags=["analysis runs"])


class ApprovalPreview(BaseModel):
    gate: str
    agent: str
    title: str
    what: str
    why: str
    target: str
    data_analyzed: str


class StartRunIn(BaseModel):
    project_id: str
    approve_gates: list[str] = []
    scoring_weights: dict[str, float] = {}


class ApprovalOut(ApprovalPreview):
    id: str
    status: str
    decided_by: str | None
    decided_at: datetime | None
    requested_at: datetime


class AgentStateOut(BaseModel):
    agent: str
    description: str
    status: str
    attempts: int
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    confidence: float | None = None
    finding_count: int = 0
    evidence_count: int = 0


class RunOut(BaseModel):
    run_id: str
    project_id: str
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime
    agents: dict[str, str]
    agent_details: list[AgentStateOut]
    approvals: list[ApprovalOut]
    has_report: bool


class DecisionIn(BaseModel):
    approve: bool


async def _run_for(session: AsyncSession, run_id: str, user: User) -> AnalysisRun:
    run = await session.get(AnalysisRun, run_id)
    if not run or run.org_id != user.org_id:
        raise not_found("Run")
    return run


def _approval_out(a: Approval) -> ApprovalOut:
    return ApprovalOut(id=a.id, gate=a.gate, agent=a.agent, title=a.title, what=a.what, why=a.why, target=a.target,
                       data_analyzed=a.data_analyzed, status=a.status, decided_by=a.decided_by,
                       decided_at=a.decided_at, requested_at=a.requested_at)


async def _run_out(session: AsyncSession, run: AnalysisRun) -> RunOut:
    execs = {e.agent: e for e in (await session.execute(
        select(AgentExecution).where(AgentExecution.run_id == run.id))).scalars().all()}
    approvals = (await session.execute(select(Approval).where(Approval.run_id == run.id)
                                       .order_by(Approval.requested_at))).scalars().all()
    has_report = (await session.execute(select(Report.id).where(Report.run_id == run.id))).first() is not None
    details = []
    for agent in default_agents():
        e = execs.get(agent.name)
        res = (e.result or {}) if e else {}
        details.append(AgentStateOut(
            agent=agent.name, description=agent.description, status=e.status if e else "pending",
            attempts=e.attempts if e else 0, error=e.error if e else None,
            started_at=e.started_at if e else None, finished_at=e.finished_at if e else None,
            confidence=res.get("confidence"), finding_count=len(res.get("findings", [])),
            evidence_count=len(res.get("evidence", [])),
        ))
    return RunOut(run_id=run.id, project_id=run.project_id, status=run.status, error=run.error,
                  created_at=run.created_at, updated_at=run.updated_at,
                  agents={d.agent: d.status for d in details}, agent_details=details,
                  approvals=[_approval_out(a) for a in approvals], has_report=has_report)


@router.get("/approval-preview", response_model=list[ApprovalPreview])
async def approval_preview(project_id: str, user: User = Depends(require_role("viewer")),
                           session: AsyncSession = Depends(get_session)) -> list[ApprovalPreview]:
    """What each gated step will access — shown before an analysis is started."""
    project = await session.get(Project, project_id)
    if not project or project.org_id != user.org_id:
        raise not_found("Project")
    ctx = RunContext(run_id="preview", project_id=project.id,
                     record=NormalizedRecord.model_validate(project.record), ledger=EvidenceLedger())
    out = []
    for agent in default_agents():
        req = agent.approval_needed(ctx)
        if req:
            out.append(ApprovalPreview(agent=agent.name, **req.__dict__))
    return out


@router.post("", response_model=RunOut, status_code=201)
async def start_run(body: StartRunIn, user: User = Depends(require_role("analyst")),
                    session: AsyncSession = Depends(get_session)) -> RunOut:
    project = await session.get(Project, body.project_id)
    if not project or project.org_id != user.org_id:
        raise not_found("Project")
    unknown = set(body.scoring_weights) - set(ALL_FACTORS)
    if unknown:
        raise HTTPException(422, f"Unknown scoring factors: {sorted(unknown)}")
    run = AnalysisRun(org_id=user.org_id, project_id=project.id, created_by=user.id, status="queued",
                      scoring_weights=body.scoring_weights)
    session.add(run)
    await session.flush()
    # Pre-approvals granted in the start dialog are recorded like any other decision (audit trail).
    ctx = RunContext(run_id=run.id, project_id=project.id,
                     record=NormalizedRecord.model_validate(project.record), ledger=EvidenceLedger())
    now = datetime.now(timezone.utc)
    for agent in default_agents():
        req = agent.approval_needed(ctx)
        if req and req.gate in body.approve_gates:
            session.add(Approval(run_id=run.id, agent=agent.name, gate=req.gate, title=req.title, what=req.what,
                                 why=req.why, target=req.target, data_analyzed=req.data_analyzed,
                                 status="approved", decided_by=user.id, decided_at=now))
    await session.commit()
    runner.start(run.id)
    return await _run_out(session, run)


@router.get("", response_model=list[RunOut])
async def list_runs(project_id: str | None = None, user: User = Depends(require_role("viewer")),
                    session: AsyncSession = Depends(get_session)) -> list[RunOut]:
    q = select(AnalysisRun).where(AnalysisRun.org_id == user.org_id)
    if project_id:
        q = q.where(AnalysisRun.project_id == project_id)
    runs = (await session.execute(q.order_by(AnalysisRun.created_at.desc()).limit(100))).scalars().all()
    return [await _run_out(session, r) for r in runs]


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, user: User = Depends(require_role("viewer")),
                  session: AsyncSession = Depends(get_session)) -> RunOut:
    return await _run_out(session, await _run_for(session, run_id, user))


@router.get("/{run_id}/agents/{agent}")
async def get_agent_result(run_id: str, agent: str, user: User = Depends(require_role("viewer")),
                           session: AsyncSession = Depends(get_session)) -> dict:
    await _run_for(session, run_id, user)
    e = (await session.execute(select(AgentExecution).where(AgentExecution.run_id == run_id,
                                                            AgentExecution.agent == agent))).scalar_one_or_none()
    if not e:
        raise not_found("Agent result")
    return {"agent": e.agent, "status": e.status, "error": e.error, "result": e.result}


@router.post("/{run_id}/approvals/{approval_id}", response_model=RunOut)
async def decide(run_id: str, approval_id: str, body: DecisionIn, user: User = Depends(require_role("analyst")),
                 session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await _run_for(session, run_id, user)
    a = await session.get(Approval, approval_id)
    if not a or a.run_id != run.id:
        raise not_found("Approval")
    if a.status != "pending":
        raise HTTPException(409, f"Approval already {a.status}")
    a.status = "approved" if body.approve else "rejected"
    a.decided_by = user.id
    a.decided_at = datetime.now(timezone.utc)
    await session.commit()
    pending = (await session.execute(select(Approval).where(Approval.run_id == run.id,
                                                            Approval.status == "pending"))).first()
    if pending is None and run.status not in ("completed", "completed_with_errors"):
        run.status = "queued"  # visible immediately, so clients keep polling
        await session.commit()
        runner.start(run.id)
    return await _run_out(session, run)


@router.post("/{run_id}/resume", response_model=RunOut)
async def resume(run_id: str, user: User = Depends(require_role("analyst")),
                 session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await _run_for(session, run_id, user)
    if run.status in ("completed",):
        raise HTTPException(409, "Run already completed")
    if not runner.is_running(run.id):
        # Failed agents are retried on resume; completed agents are not re-run.
        for e in (await session.execute(select(AgentExecution).where(
                AgentExecution.run_id == run.id, AgentExecution.status.in_(["failed", "skipped"])))).scalars():
            e.status = "pending"
        run.status = "queued"
        await session.commit()
        runner.start(run.id)
    return await _run_out(session, run)


@router.get("/{run_id}/evidence")
async def list_evidence(run_id: str, source_type: str | None = None, q: str | None = Query(None, max_length=200),
                        user: User = Depends(require_role("viewer")),
                        session: AsyncSession = Depends(get_session)) -> list[dict]:
    await _run_for(session, run_id, user)
    stmt = select(EvidenceRecord).where(EvidenceRecord.run_id == run_id, EvidenceRecord.org_id == user.org_id)
    if source_type:
        stmt = stmt.where(EvidenceRecord.source_type == source_type)
    if q:
        stmt = stmt.where(EvidenceRecord.claim.ilike(f"%{q}%"))
    rows = (await session.execute(stmt.order_by(EvidenceRecord.pk).limit(2000))).scalars().all()
    return [{"id": e.id, "claim": e.claim, "source_url": e.source_url, "source_type": e.source_type,
             "extracted_text": e.extracted_text, "repository_path": e.repository_path, "line_range": e.line_range,
             "confidence": e.confidence, "collected_at": e.collected_at.isoformat()} for e in rows]


@router.get("/{run_id}/report")
async def get_report(run_id: str, user: User = Depends(require_role("viewer")),
                     session: AsyncSession = Depends(get_session)) -> dict:
    await _run_for(session, run_id, user)
    r = (await session.execute(select(Report).where(Report.run_id == run_id))).scalar_one_or_none()
    if not r:
        raise not_found("Report")
    return {"id": r.id, "title": r.title, "created_at": r.created_at.isoformat(), "content": r.content,
            "markdown": r.markdown}


@router.get("/{run_id}/report.md", response_class=PlainTextResponse)
async def get_report_markdown(run_id: str, user: User = Depends(require_role("viewer")),
                              session: AsyncSession = Depends(get_session)) -> PlainTextResponse:
    await _run_for(session, run_id, user)
    r = (await session.execute(select(Report).where(Report.run_id == run_id))).scalar_one_or_none()
    if not r:
        raise not_found("Report")
    return PlainTextResponse(r.markdown, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="report-{run_id}.md"'})
