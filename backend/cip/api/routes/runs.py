"""Analysis runs, agent state, approvals (human gates), evidence and reports."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.agents.base import RunContext
from cip.agents.orchestrator import default_agents
from cip.api.deps import not_found, require_role
from cip.core.evidence import EvidenceLedger
from cip.core.schemas import NormalizedRecord
from cip.core.scoring import ALL_FACTORS
from cip.core.usage import total_usage
from cip.db.models import AgentExecution, AnalysisRun, Approval, EvidenceRecord, Project, Report, User
from cip.db.session import get_session
from cip.services import audit, governance
from cip.services.access import project_for, run_for, visible_projects
from cip.services.report_pdf import PdfUnavailable, render_pdf, report_html
from cip.services.rerun import ACTIVE, STAGE_PRESETS, RerunError, active_run, create_rerun
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


class BulkRunIn(BaseModel):
    project_ids: list[str] = Field(min_length=1, max_length=100)
    approve_gates: list[str] = []
    scoring_weights: dict[str, float] = {}


class RerunIn(BaseModel):
    stages: list[str] = Field(min_length=1, max_length=20)


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
    usage: dict | None = None


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
    monitor_id: str | None = None  # set for runs started by a monitoring schedule
    parent_run_id: str | None = None  # partial re-run: the run this version was derived from
    rerun_stages: list[str] | None = None
    usage: dict = {}  # totals across agents: LLM calls/tokens/cost/models, web requests, search queries
    created_by: str | None = None


class DecisionIn(BaseModel):
    approve: bool


async def _run_for(session: AsyncSession, run_id: str, user: User) -> AnalysisRun:
    return await run_for(session, user, run_id)


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
            evidence_count=len(res.get("evidence", [])), usage=res.get("usage"),
        ))
    return RunOut(run_id=run.id, project_id=run.project_id, status=run.status, error=run.error,
                  created_at=run.created_at, updated_at=run.updated_at,
                  agents={d.agent: d.status for d in details}, agent_details=details,
                  approvals=[_approval_out(a) for a in approvals], has_report=has_report,
                  monitor_id=run.monitor_id, parent_run_id=run.parent_run_id, rerun_stages=run.rerun_stages,
                  usage=total_usage([d.usage for d in details]), created_by=run.created_by)


@router.get("/approval-preview", response_model=list[ApprovalPreview])
async def approval_preview(project_id: str, user: User = Depends(require_role("viewer")),
                           session: AsyncSession = Depends(get_session)) -> list[ApprovalPreview]:
    """What each gated step will access — shown before an analysis is started."""
    project = await project_for(session, user, project_id)
    ctx = RunContext(run_id="preview", project_id=project.id,
                     record=NormalizedRecord.model_validate(project.record), ledger=EvidenceLedger())
    out = []
    for agent in default_agents():
        req = agent.approval_needed(ctx)
        if req:
            out.append(ApprovalPreview(agent=agent.name, **req.__dict__))
    return out


def _check_weights(weights: dict[str, float]) -> None:
    unknown = set(weights) - set(ALL_FACTORS)
    if unknown:
        raise HTTPException(422, f"Unknown scoring factors: {sorted(unknown)}")


@router.post("", response_model=RunOut, status_code=201)
async def start_run(body: StartRunIn, request: Request, user: User = Depends(require_role("analyst")),
                    session: AsyncSession = Depends(get_session)) -> RunOut:
    project = await project_for(session, user, body.project_id)
    _check_weights(body.scoring_weights)
    existing = await active_run(session, project.id)
    if existing:
        # Duplicate-job prevention (BRS 26.3): reuse the analysis that is already in progress.
        raise HTTPException(409, f"An analysis of this project is already in progress ({existing.id}).")
    run = await _create_run(session, request, user, project, body.approve_gates, body.scoring_weights)
    await session.commit()
    runner.start(run.id)
    return await _run_out(session, run)


@router.post("/bulk", status_code=201)
async def start_runs(body: BulkRunIn, request: Request, user: User = Depends(require_role("analyst")),
                     session: AsyncSession = Depends(get_session)) -> dict:
    """Start an independent analysis for each selected project (BRS 7.1, 7.21)."""
    _check_weights(body.scoring_weights)
    started, skipped = [], []
    for pid in dict.fromkeys(body.project_ids):
        try:
            project = await project_for(session, user, pid)
        except HTTPException:
            skipped.append({"project_id": pid, "reason": "not found"})
            continue
        existing = await active_run(session, project.id)
        if existing:
            skipped.append({"project_id": pid, "reason": "already in progress", "run_id": existing.id})
            continue
        started.append(await _create_run(session, request, user, project, body.approve_gates, body.scoring_weights))
    await session.commit()
    for run in started:
        runner.start(run.id)
    return {"runs": [await _run_out(session, r) for r in started], "skipped": skipped}


async def _create_run(session: AsyncSession, request: Request, user: User, project: Project,
                      approve_gates: list[str], scoring_weights: dict[str, float]) -> AnalysisRun:
    run = AnalysisRun(org_id=user.org_id, project_id=project.id, created_by=user.id, status="queued",
                      scoring_weights=scoring_weights)
    session.add(run)
    await session.flush()
    # Pre-approvals granted in the start dialog are recorded like any other decision (audit trail).
    ctx = RunContext(run_id=run.id, project_id=project.id,
                     record=NormalizedRecord.model_validate(project.record), ledger=EvidenceLedger())
    now = datetime.now(timezone.utc)
    for agent in default_agents():
        req = agent.approval_needed(ctx)
        if req and req.gate in approve_gates:
            session.add(Approval(run_id=run.id, agent=agent.name, gate=req.gate, title=req.title, what=req.what,
                                 why=req.why, target=req.target, data_analyzed=req.data_analyzed,
                                 status="approved", decided_by=user.id, decided_at=now))
    audit.record(session, request, user, "run.started", "run", run.id, project_id=project.id,
                 pre_approved=approve_gates or None, scoring_weights=scoring_weights or None)
    return run


@router.get("", response_model=list[RunOut])
async def list_runs(project_id: str | None = None, user: User = Depends(require_role("viewer")),
                    session: AsyncSession = Depends(get_session)) -> list[RunOut]:
    q = select(AnalysisRun).join(Project, Project.id == AnalysisRun.project_id).where(visible_projects(user))
    if project_id:
        q = q.where(AnalysisRun.project_id == project_id)
    runs = (await session.execute(q.order_by(AnalysisRun.created_at.desc()).limit(100))).scalars().all()
    return [await _run_out(session, r) for r in runs]


@router.get("/rerun-stages")
async def rerun_stages(user: User = Depends(require_role("viewer"))) -> dict:
    return {k: list(v) for k, v in STAGE_PRESETS.items()}


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
async def decide(run_id: str, approval_id: str, body: DecisionIn, request: Request, user: User = Depends(require_role("analyst")),
                 session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await _run_for(session, run_id, user)
    a = await session.get(Approval, approval_id)
    if not a or a.run_id != run.id:
        raise not_found("Approval")
    if a.status != "pending":
        raise HTTPException(409, f"Approval already {a.status}")
    if run.status == "cancelled":
        raise HTTPException(409, "The run was cancelled")
    a.status = "approved" if body.approve else "rejected"
    a.decided_by = user.id
    a.decided_at = datetime.now(timezone.utc)
    audit.record(session, request, user, f"approval.{a.status}", "run", run.id, gate=a.gate)
    await session.commit()
    pending = (await session.execute(select(Approval).where(Approval.run_id == run.id,
                                                            Approval.status == "pending"))).first()
    if pending is None and run.status not in ("completed", "completed_with_errors"):
        run.status = "queued"  # visible immediately, so clients keep polling
        await session.commit()
        runner.start(run.id)
    return await _run_out(session, run)


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel(run_id: str, request: Request, user: User = Depends(require_role("analyst")),
                 session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await _run_for(session, run_id, user)
    if run.status not in ACTIVE:
        raise HTTPException(409, f"Only a queued, running, paused or waiting run can be cancelled (it is {run.status}).")
    run.status = "cancelled"
    execs = {e.agent: e for e in (await session.execute(select(AgentExecution).where(
        AgentExecution.run_id == run.id))).scalars()}
    for agent in default_agents():
        e = execs.get(agent.name)
        if e is None:
            session.add(AgentExecution(run_id=run.id, agent=agent.name, status="skipped", error="run cancelled"))
        elif e.status in ("pending", "running", "awaiting_approval"):
            e.status, e.error = "skipped", "run cancelled"
    audit.record(session, request, user, "run.cancelled", "run", run.id)
    await session.commit()
    runner.cancel(run.id)
    return await _run_out(session, run)


@router.post("/{run_id}/pause", response_model=RunOut)
async def pause(run_id: str, request: Request, user: User = Depends(require_role("analyst")),
                session: AsyncSession = Depends(get_session)) -> RunOut:
    """Stop after the agents that are already running finish their current step; resume continues."""
    run = await _run_for(session, run_id, user)
    if run.status not in ("queued", "running"):
        raise HTTPException(409, f"Only a queued or running run can be paused (it is {run.status}).")
    run.status = "paused"
    audit.record(session, request, user, "run.paused", "run", run.id)
    await session.commit()
    return await _run_out(session, run)


@router.post("/{run_id}/rerun", response_model=RunOut, status_code=201)
async def rerun(run_id: str, body: RerunIn, request: Request, user: User = Depends(require_role("analyst")),
                session: AsyncSession = Depends(get_session)) -> RunOut:
    """Refresh selected stages as a new run version; completed stages that are not refreshed are kept."""
    run = await _run_for(session, run_id, user)
    try:
        new = await create_rerun(session, run, body.stages, user)
    except RerunError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit.record(session, request, user, "run.rerun", "run", new.id, parent_run_id=run.id, stages=new.rerun_stages)
    await session.commit()
    runner.start(new.id)
    return await _run_out(session, new)


@router.post("/{run_id}/resume", response_model=RunOut)
async def resume(run_id: str, request: Request, user: User = Depends(require_role("analyst")),
                 session: AsyncSession = Depends(get_session)) -> RunOut:
    run = await _run_for(session, run_id, user)
    if run.status in ("completed",):
        raise HTTPException(409, "Run already completed")
    if not await runner.is_active(run.id):
        # Failed agents are retried on resume; completed agents are not re-run.
        for e in (await session.execute(select(AgentExecution).where(
                AgentExecution.run_id == run.id, AgentExecution.status.in_(["failed", "skipped"])))).scalars():
            e.status = "pending"
        run.status = "queued"
        audit.record(session, request, user, "run.resumed", "run", run.id)
        await session.commit()
        runner.start(run.id)
    return await _run_out(session, run)


@router.get("/{run_id}/evidence")
async def list_evidence(run_id: str, source_type: str | None = None, q: str | None = Query(None, max_length=200),
                        user: User = Depends(require_role("viewer")),
                        session: AsyncSession = Depends(get_session)) -> list[dict]:
    await _run_for(session, run_id, user)
    stmt = select(EvidenceRecord).where(EvidenceRecord.run_id == run_id)
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


@router.get("/{run_id}/report.pdf")
async def get_report_pdf(run_id: str, request: Request, user: User = Depends(require_role("viewer")),
                         session: AsyncSession = Depends(get_session)) -> Response:
    """Client-ready PDF of the report (rendered offline in headless Chromium)."""
    await _run_for(session, run_id, user)
    await governance.require_export(session, user)
    r = (await session.execute(select(Report).where(Report.run_id == run_id))).scalar_one_or_none()
    if not r:
        raise not_found("Report")
    try:
        architecture = (r.content or {}).get("sections", {}).get("architecture")
        pdf = await render_pdf(report_html(r.markdown, r.title, architecture), r.title)
    except PdfUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    audit.record(session, request, user, "report.exported", "run", run_id, format="pdf")
    await session.commit()
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="report-{run_id}.pdf"'})


@router.get("/{run_id}/report.md", response_class=PlainTextResponse)
async def get_report_markdown(run_id: str, request: Request, user: User = Depends(require_role("viewer")),
                              session: AsyncSession = Depends(get_session)) -> PlainTextResponse:
    await _run_for(session, run_id, user)
    await governance.require_export(session, user)
    r = (await session.execute(select(Report).where(Report.run_id == run_id))).scalar_one_or_none()
    if not r:
        raise not_found("Report")
    audit.record(session, request, user, "report.exported", "run", run_id, format="markdown")
    await session.commit()
    return PlainTextResponse(r.markdown, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="report-{run_id}.md"'})
