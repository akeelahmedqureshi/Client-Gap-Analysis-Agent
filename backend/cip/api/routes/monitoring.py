"""Monitoring (scheduled re-analysis per project), alerts and run-to-run changes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.config import get_settings
from cip.core.scoring import ALL_FACTORS
from cip.core.security.crypto import TokenCipher
from cip.db.models import Alert, AnalysisRun, Client, Monitor, Project, User
from cip.db.session import get_session
from cip.services import audit, notify
from cip.services.access import project_for, run_for, visible_projects
from cip.services.changes import summarize
from cip.services.monitoring import (
    FINISHED,
    IN_FLIGHT,
    STANDING_GATES,
    approver_still_valid,
    compute_changes,
    create_monitored_run,
    next_run_after,
)
from cip.services.runner import runner

router = APIRouter(prefix="/api", tags=["monitoring & alerts"])

Severity = Literal["critical", "warning", "info"]


class MonitorIn(BaseModel):
    enabled: bool = True
    frequency: Literal["daily", "weekly", "monthly"] = "weekly"
    # Gates granted in advance for scheduled runs (recorded as approvals by the caller).
    standing_approvals: list[str] = Field(default_factory=list)
    min_severity: Severity = "warning"
    notify_emails: list[EmailStr] = Field(default_factory=list, max_length=10)
    # None keeps the stored webhook, "" removes it.
    webhook_url: str | None = Field(None, max_length=2000)
    scoring_weights: dict[str, float] = Field(default_factory=dict)
    run_now: bool = False


class MonitorOut(BaseModel):
    id: str
    project_id: str
    project_name: str
    client_name: str
    enabled: bool
    frequency: str
    standing_approvals: list[str]
    approvals_valid: bool
    approved_by: str | None
    min_severity: str
    notify_emails: list[str]
    webhook: str | None  # masked
    email_enabled: bool  # false when the server has no SMTP configuration
    scoring_weights: dict
    next_run_at: datetime | None
    last_run_id: str | None
    last_run_status: str | None
    last_triggered_at: datetime | None
    created_at: datetime


class AlertOut(BaseModel):
    id: str
    project_id: str
    project_name: str
    run_id: str
    monitor_id: str | None
    kind: str
    severity: str
    title: str
    summary: str
    changes: list[dict]
    notifications: list[dict]
    read_at: datetime | None
    created_at: datetime


async def _monitor_out(session: AsyncSession, m: Monitor, project: Project) -> MonitorOut:
    client = await session.get(Client, project.client_id)
    last = await session.get(AnalysisRun, m.last_run_id) if m.last_run_id else None
    webhook = TokenCipher().decrypt(m.encrypted_webhook_url) if m.encrypted_webhook_url else None
    return MonitorOut(
        id=m.id, project_id=project.id, project_name=project.name, client_name=client.name if client else "",
        enabled=m.enabled, frequency=m.frequency, standing_approvals=m.standing_approvals or [],
        approvals_valid=not m.standing_approvals or await approver_still_valid(session, m, project),
        approved_by=m.approved_by, min_severity=m.min_severity, notify_emails=m.notify_emails or [],
        webhook=notify.mask_webhook_url(webhook), email_enabled=bool(get_settings().smtp_host),
        scoring_weights=m.scoring_weights or {},
        next_run_at=m.next_run_at, last_run_id=m.last_run_id, last_run_status=last.status if last else None,
        last_triggered_at=m.last_triggered_at, created_at=m.created_at)


async def _monitor_for(session: AsyncSession, user: User, project_id: str) -> tuple[Monitor, Project]:
    project = await project_for(session, user, project_id)
    m = (await session.execute(select(Monitor).where(Monitor.project_id == project.id,
                                                     Monitor.org_id == user.org_id))).scalar_one_or_none()
    if m is None:
        raise not_found("Monitor")
    return m, project


@router.get("/monitors", response_model=list[MonitorOut])
async def list_monitors(user: User = Depends(require_role("viewer")),
                        session: AsyncSession = Depends(get_session)) -> list[MonitorOut]:
    rows = (await session.execute(
        select(Monitor, Project).join(Project, Project.id == Monitor.project_id)
        .where(Monitor.org_id == user.org_id, visible_projects(user)).order_by(Monitor.created_at))).all()
    return [await _monitor_out(session, m, p) for m, p in rows]


@router.get("/projects/{project_id}/monitor", response_model=MonitorOut)
async def get_monitor(project_id: str, user: User = Depends(require_role("viewer")),
                      session: AsyncSession = Depends(get_session)) -> MonitorOut:
    m, project = await _monitor_for(session, user, project_id)
    return await _monitor_out(session, m, project)


@router.put("/projects/{project_id}/monitor", response_model=MonitorOut)
async def upsert_monitor(project_id: str, body: MonitorIn, request: Request,
                         user: User = Depends(require_role("analyst")),
                         session: AsyncSession = Depends(get_session)) -> MonitorOut:
    project = await project_for(session, user, project_id)
    unknown = set(body.standing_approvals) - set(STANDING_GATES)
    if unknown:
        raise HTTPException(422, f"Unknown approval gates: {sorted(unknown)}")
    bad_factors = set(body.scoring_weights) - set(ALL_FACTORS)
    if bad_factors:
        raise HTTPException(422, f"Unknown scoring factors: {sorted(bad_factors)}")
    webhook_url = None
    if body.webhook_url:
        try:
            webhook_url = notify.validate_webhook_url(body.webhook_url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    m = (await session.execute(select(Monitor).where(Monitor.project_id == project.id))).scalar_one_or_none()
    created = m is None
    now = datetime.now(timezone.utc)
    if created:
        m = Monitor(org_id=user.org_id, project_id=project.id, created_by=user.id)
        session.add(m)
    reschedule = created or m.frequency != body.frequency or (body.enabled and not m.enabled)
    m.enabled = body.enabled
    m.frequency = body.frequency
    # Whoever saves the standing approvals is recorded as having granted them.
    m.standing_approvals = sorted(set(body.standing_approvals))
    m.approved_by = user.id if m.standing_approvals else None
    m.min_severity = body.min_severity
    m.notify_emails = [str(e) for e in dict.fromkeys(body.notify_emails)]
    m.scoring_weights = body.scoring_weights
    if body.webhook_url is not None:
        m.encrypted_webhook_url = TokenCipher().encrypt(webhook_url) if webhook_url else None
    if reschedule or m.next_run_at is None:
        m.next_run_at = next_run_after(m.frequency, now)
    audit.record(session, request, user, "monitor.created" if created else "monitor.updated", "project", project.id,
                 enabled=m.enabled, frequency=m.frequency, standing_approvals=m.standing_approvals,
                 min_severity=m.min_severity, emails=len(m.notify_emails),
                 webhook="set" if m.encrypted_webhook_url else "none")
    await session.flush()
    run_id = None
    if body.run_now and m.enabled:
        run_id = (await _start_now(session, m, project)).id
    await session.commit()
    if run_id:
        runner.start(run_id)
    return await _monitor_out(session, m, project)


async def _start_now(session: AsyncSession, m: Monitor, project: Project) -> AnalysisRun:
    busy = (await session.execute(select(AnalysisRun.id).where(
        AnalysisRun.monitor_id == m.id, AnalysisRun.status.in_(IN_FLIGHT)))).first()
    if busy is not None:
        raise HTTPException(409, f"The previous monitored run ({busy[0]}) has not finished yet")
    m.next_run_at = next_run_after(m.frequency, datetime.now(timezone.utc))
    m.version = (m.version or 0) + 1  # invalidate any claim a scheduler read before this
    return await create_monitored_run(session, m, project)


@router.post("/projects/{project_id}/monitor/run-now")
async def run_monitor_now(project_id: str, request: Request, user: User = Depends(require_role("analyst")),
                          session: AsyncSession = Depends(get_session)) -> dict:
    m, project = await _monitor_for(session, user, project_id)
    run = await _start_now(session, m, project)
    audit.record(session, request, user, "monitor.run_now", "run", run.id, project_id=project.id)
    await session.commit()
    runner.start(run.id)
    return {"run_id": run.id}


@router.post("/projects/{project_id}/monitor/test")
async def test_notification(project_id: str, request: Request, user: User = Depends(require_role("analyst")),
                            session: AsyncSession = Depends(get_session)) -> dict:
    """Send a sample alert to the monitor's channels so the webhook/email set-up can be verified."""
    m, project = await _monitor_for(session, user, project_id)
    webhook = TokenCipher().decrypt(m.encrypted_webhook_url) if m.encrypted_webhook_url else None
    if not webhook and not m.notify_emails:
        raise HTTPException(422, "No webhook or email recipients configured")
    sample = {"id": "test", "kind": "test", "severity": "info", "title": "Test notification",
              "summary": "Monitoring alerts for this project will arrive here.",
              "run_id": m.last_run_id or "none", "project_id": project.id, "changes": []}
    results = await notify.deliver(sample, project.name, webhook, list(m.notify_emails or []))
    audit.record(session, request, user, "monitor.test_notification", "project", project.id,
                 results=[{k: v for k, v in r.items() if k != "error"} for r in results])
    await session.commit()
    return {"results": results}


@router.delete("/projects/{project_id}/monitor", status_code=204)
async def delete_monitor(project_id: str, request: Request, user: User = Depends(require_role("analyst")),
                         session: AsyncSession = Depends(get_session)) -> Response:
    m, project = await _monitor_for(session, user, project_id)
    await session.delete(m)
    audit.record(session, request, user, "monitor.deleted", "project", project.id)
    await session.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- alerts


def _alerts_query(user: User):
    return (select(Alert, Project.name).join(Project, Project.id == Alert.project_id)
            .where(Alert.org_id == user.org_id, visible_projects(user)))


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(unread_only: bool = False, project_id: str | None = None,
                      limit: int = Query(50, ge=1, le=200),
                      user: User = Depends(require_role("viewer")),
                      session: AsyncSession = Depends(get_session)) -> list[AlertOut]:
    q = _alerts_query(user)
    if unread_only:
        q = q.where(Alert.read_at.is_(None))
    if project_id:
        q = q.where(Alert.project_id == project_id)
    rows = (await session.execute(q.order_by(Alert.created_at.desc()).limit(limit))).all()
    return [AlertOut(id=a.id, project_id=a.project_id, project_name=name, run_id=a.run_id, monitor_id=a.monitor_id,
                     kind=a.kind, severity=a.severity, title=a.title, summary=a.summary, changes=a.changes or [],
                     notifications=a.notifications or [], read_at=a.read_at, created_at=a.created_at)
            for a, name in rows]


@router.get("/alerts/unread-count")
async def unread_count(user: User = Depends(require_role("viewer")),
                       session: AsyncSession = Depends(get_session)) -> dict:
    q = (select(func.count(Alert.id)).join(Project, Project.id == Alert.project_id)
         .where(Alert.org_id == user.org_id, visible_projects(user), Alert.read_at.is_(None)))
    return {"count": (await session.execute(q)).scalar_one()}


@router.post("/alerts/{alert_id}/read", status_code=204)
async def mark_read(alert_id: str, user: User = Depends(require_role("viewer")),
                    session: AsyncSession = Depends(get_session)) -> Response:
    row = (await session.execute(_alerts_query(user).where(Alert.id == alert_id))).first()
    if row is None:
        raise not_found("Alert")
    alert = row[0]
    if alert.read_at is None:
        alert.read_at = datetime.now(timezone.utc)
        alert.read_by = user.id
        await session.commit()
    return Response(status_code=204)


@router.post("/alerts/read-all", status_code=204)
async def mark_all_read(user: User = Depends(require_role("viewer")),
                        session: AsyncSession = Depends(get_session)) -> Response:
    visible = select(Project.id).where(visible_projects(user))
    await session.execute(update(Alert).where(Alert.org_id == user.org_id, Alert.project_id.in_(visible),
                                              Alert.read_at.is_(None))
                          .values(read_at=datetime.now(timezone.utc), read_by=user.id))
    await session.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- run changes


@router.get("/runs/{run_id}/changes")
async def run_changes(run_id: str, user: User = Depends(require_role("viewer")),
                      session: AsyncSession = Depends(get_session)) -> dict:
    """What changed since the previous completed run of the same project."""
    run = await run_for(session, user, run_id)
    baseline_id, changes = run.baseline_run_id, run.changes
    if changes is None and run.status in FINISHED:
        baseline_id, changes = await compute_changes(session, run)  # runs finished before this feature existed
    baseline = await session.get(AnalysisRun, baseline_id) if baseline_id else None
    return {"run_id": run.id, "status": run.status, "baseline_run_id": baseline_id,
            "baseline_created_at": baseline.created_at.isoformat() if baseline else None,
            "changes": changes or [], "summary": summarize(changes or [])}

