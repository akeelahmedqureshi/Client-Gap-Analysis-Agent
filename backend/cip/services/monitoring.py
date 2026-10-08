"""Continuous monitoring: scheduled re-analysis, change detection and alerts.

    scheduler tick ──▶ due monitor (claimed atomically) ──▶ new run with standing approvals
    run finishes  ──▶ diff vs previous completed run ──▶ alert (in-app) ──▶ webhook / email

Several API processes may run the scheduler: a monitor is claimed by bumping its
``version`` with a conditional UPDATE, so a due run starts exactly once.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cip.agents.base import RunContext
from cip.agents.orchestrator import default_agents
from cip.config import get_settings
from cip.connectors.source_control import parse_repo_url
from cip.core.evidence import EvidenceLedger
from cip.core.schemas import NormalizedRecord
from cip.core.security.auth import role_at_least
from cip.core.security.crypto import TokenCipher
from cip.db import session as db
from cip.db.models import (
    AgentExecution,
    Alert,
    AnalysisRun,
    Approval,
    Monitor,
    Project,
    ProjectMember,
    User,
)
from cip.services import audit, notify
from cip.services.changes import at_least, diff_outputs, summarize

log = logging.getLogger(__name__)

FREQUENCIES = {"daily": timedelta(days=1), "weekly": timedelta(days=7), "monthly": timedelta(days=30)}
STANDING_GATES = ("external_research", "repository_access", "client_report", "large_repository_scan")
FINISHED = ("completed", "completed_with_errors")
IN_FLIGHT = ("pending", "queued", "running", "awaiting_approval")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def next_run_after(frequency: str, after: datetime) -> datetime:
    return after + FREQUENCIES[frequency]


def expand_gates(standing: list[str], record: NormalizedRecord) -> list[str]:
    """Standing approvals -> concrete gate names (large-repo gates are per repository)."""
    gates = [g for g in standing if g != "large_repository_scan"]
    if "large_repository_scan" in standing:
        for url in record.sources.github + record.sources.gitlab:
            ref = parse_repo_url(url)
            if ref:
                gates.append(f"large_repository_scan:{ref.full_name}")
    return gates


async def approver_still_valid(session: AsyncSession, monitor: Monitor, project: Project) -> bool:
    """Standing approvals hold only while their approver is an active analyst/admin who can see the project."""
    if not monitor.approved_by:
        return False
    user = await session.get(User, monitor.approved_by)
    if user is None or not user.is_active or user.org_id != monitor.org_id or not role_at_least(user.role, "analyst"):
        return False
    if project.restricted and user.role != "admin":
        member = (await session.execute(select(ProjectMember.id).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user.id))).first()
        return member is not None
    return True


async def create_monitored_run(session: AsyncSession, monitor: Monitor, project: Project) -> AnalysisRun:
    """Create (not start) a scheduled run with the monitor's standing approvals recorded as decisions."""
    valid = await approver_still_valid(session, monitor, project)
    standing = list(monitor.standing_approvals or []) if valid else []
    record = NormalizedRecord.model_validate(project.record)
    gates = expand_gates(standing, record)
    run = AnalysisRun(org_id=monitor.org_id, project_id=project.id, status="queued", monitor_id=monitor.id,
                      created_by=monitor.approved_by if valid else None,
                      scoring_weights=monitor.scoring_weights or {},
                      approved_gates=[g for g in gates if g.startswith("large_repository_scan:")])
    session.add(run)
    await session.flush()
    ctx = RunContext(run_id=run.id, project_id=project.id, record=record, ledger=EvidenceLedger())
    now = _now()
    for agent in default_agents():
        req = agent.approval_needed(ctx)
        if req and req.gate in gates:
            session.add(Approval(run_id=run.id, agent=agent.name, gate=req.gate, title=req.title, what=req.what,
                                 why=req.why, target=req.target, data_analyzed=req.data_analyzed,
                                 status="approved", decided_by=monitor.approved_by, decided_at=now))
    monitor.last_run_id = run.id
    monitor.last_triggered_at = now
    audit.record(session, None, None, "monitor.run_started", "run", run.id, org_id=monitor.org_id,
                 email="scheduler", monitor_id=monitor.id, project_id=project.id, standing_approvals=gates or None,
                 approvals_suspended=None if valid or not monitor.standing_approvals else True)
    return run


async def tick(now: datetime | None = None, start=None) -> list[str]:
    """Start a run for every due monitor. Returns the started run ids."""
    from cip.services.runner import runner

    start = start or runner.start
    now = now or _now()
    started: list[str] = []
    async with db.sessionmaker()() as s:
        due = (await s.execute(select(Monitor.id, Monitor.version).where(
            Monitor.enabled.is_(True), Monitor.next_run_at.is_not(None), Monitor.next_run_at <= now))).all()
    for monitor_id, version in due:
        async with db.sessionmaker()() as s:
            monitor = await s.get(Monitor, monitor_id)
            claimed = await s.execute(
                update(Monitor).where(Monitor.id == monitor_id, Monitor.version == version)
                .values(version=version + 1, next_run_at=next_run_after(monitor.frequency, now)))
            if claimed.rowcount != 1:
                await s.rollback()
                continue  # another scheduler instance took it
            await s.refresh(monitor)
            project = await s.get(Project, monitor.project_id)
            busy = (await s.execute(select(AnalysisRun.id).where(
                AnalysisRun.monitor_id == monitor.id, AnalysisRun.status.in_(IN_FLIGHT)))).first()
            if busy is not None:
                # The previous scheduled run is still going (or waiting for approval): don't pile up runs.
                log.info("Monitor %s: previous run %s still in flight; skipping this cycle", monitor.id, busy[0])
                await s.commit()
                continue
            run = await create_monitored_run(s, monitor, project)
            await s.commit()
            started.append(run.id)
        start(run.id)
    return started


async def scheduler_loop() -> None:
    s = get_settings()
    while True:
        try:
            ids = await tick()
            if ids:
                log.info("Monitoring started runs: %s", ids)
            from cip.services.governance import purge_all

            purged = {k: v for k, v in (await purge_all()).items() if v}
            if purged:
                log.info("Retention purged runs: %s", purged)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the loop alive (e.g. database briefly unavailable)
            log.exception("Monitoring tick failed")
        await asyncio.sleep(s.monitor_poll_seconds)


# --------------------------------------------------------------------------- run completion


async def _outputs(session: AsyncSession, run_id: str) -> dict[str, dict]:
    rows = (await session.execute(select(AgentExecution).where(
        AgentExecution.run_id == run_id, AgentExecution.status == "completed"))).scalars().all()
    return {r.agent: (r.result or {}).get("data", {}) for r in rows}


async def compute_changes(session: AsyncSession, run: AnalysisRun) -> tuple[str | None, list[dict]]:
    baseline = (await session.execute(
        select(AnalysisRun).where(AnalysisRun.project_id == run.project_id, AnalysisRun.id != run.id,
                                  AnalysisRun.status.in_(FINISHED), AnalysisRun.created_at < run.created_at)
        .order_by(AnalysisRun.created_at.desc()).limit(1))).scalar_one_or_none()
    if baseline is None:
        return None, []
    return baseline.id, diff_outputs(await _outputs(session, baseline.id), await _outputs(session, run.id))


async def _add_alert(session: AsyncSession, run: AnalysisRun, kind: str, severity: str, title: str,
                     summary: str = "", changes: list | None = None) -> Alert | None:
    exists = (await session.execute(select(Alert.id).where(Alert.run_id == run.id, Alert.kind == kind))).first()
    if exists:
        return None
    alert = Alert(org_id=run.org_id, project_id=run.project_id, run_id=run.id, monitor_id=run.monitor_id,
                  kind=kind, severity=severity, title=title, summary=summary, changes=changes or [])
    session.add(alert)
    await session.flush()
    return alert


def alert_dict(a: Alert) -> dict:
    return {"id": a.id, "kind": a.kind, "severity": a.severity, "title": a.title, "summary": a.summary,
            "run_id": a.run_id, "project_id": a.project_id, "changes": a.changes or []}


async def on_run_finished(run_id: str, status: str) -> Alert | None:
    """Record what changed (every run) and raise alerts (scheduled runs only)."""
    async with db.sessionmaker()() as s:
        run = await s.get(AnalysisRun, run_id)
        if run is None:
            return None
        project = await s.get(Project, run.project_id)
        alert = None
        if status in FINISHED:
            run.baseline_run_id, run.changes = await compute_changes(s, run)
        monitor = await s.get(Monitor, run.monitor_id) if run.monitor_id else None
        if monitor is not None:
            if status in FINISHED and run.changes:
                summ = summarize(run.changes)
                counts = ", ".join(f"{n} {sev}" for sev, n in summ["counts"].items() if n)
                alert = await _add_alert(s, run, "changes", summ["highest"],
                                         f"{summ['total']} change(s) since the last analysis",
                                         f"{counts}.", run.changes)
            elif status == "awaiting_approval":
                pending = (await s.execute(select(Approval.title).where(
                    Approval.run_id == run.id, Approval.status == "pending"))).scalars().all()
                alert = await _add_alert(s, run, "approval_needed", "warning",
                                         "Scheduled analysis is waiting for approval",
                                         "Pending: " + "; ".join(pending) if pending else "")
            elif status == "failed":
                alert = await _add_alert(s, run, "run_failed", "critical", "Scheduled analysis failed",
                                         (run.error or "")[:500])
        await s.commit()
        if alert is None or monitor is None:
            return alert
        if at_least(alert.severity, monitor.min_severity):
            webhook = TokenCipher().decrypt(monitor.encrypted_webhook_url) if monitor.encrypted_webhook_url else None
            alert.notifications = await notify.deliver(alert_dict(alert), project.name, webhook,
                                                       list(monitor.notify_emails or []))
            await s.commit()
        return alert

