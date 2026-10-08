"""Data governance: organization settings, export permissions, deletion and retention (BRS 8, 31; PRD 10.44).

* **Settings** live on ``Organization.settings`` and are edited by admins only. Unknown keys are rejected.
* **Exports** (report PDF/Markdown, structured CSV/JSON, outreach .eml, sales summary) require the role in
  ``export_min_role`` and, when ``export_job_functions`` is non-empty, one of those job functions (admins are
  always allowed). Viewing results in the app is not an export and is not gated.
* **Deletion** removes a run, a project or a client together with everything derived from it (agent results,
  evidence, approvals, reports, sales documents, review changes, alerts, notifications). Runs that are still
  in progress cannot be deleted. Every deletion is written to the audit log, which is never purged.
* **Retention**: with ``retention_days`` set, finished runs older than that are purged automatically by the
  scheduler (and on demand by an admin). ``retention_keep_latest`` keeps each project's latest completed run
  so the portfolio and client pages keep working.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cip.core.security.auth import ROLES, role_at_least
from cip.db.models import (AgentExecution, Alert, AnalysisRun, Approval, Client, EvidenceRecord, Monitor,
                           Notification, Organization, Project, ProjectMember, Report, ReviewOverride, SalesDocument,
                           User)

JOB_FUNCTIONS = ("sales", "business_development", "product", "technical", "management")
ACTIVE = ("queued", "running", "awaiting_approval", "paused", "pending")
FINISHED = ("completed", "completed_with_errors", "failed", "cancelled")
DONE = ("completed", "completed_with_errors")
DEFAULTS: dict = {
    "export_min_role": "viewer",
    "export_job_functions": [],
    "retention_days": None,
    "retention_keep_latest": True,
}
MIN_RETENTION_DAYS, MAX_RETENTION_DAYS = 7, 3650


def org_settings(org: Organization | None) -> dict:
    stored = (org.settings if org else None) or {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS or k == "last_purge_at"}}


def validate_settings(patch: dict) -> dict:
    out: dict = {}
    for key, value in patch.items():
        if key not in DEFAULTS:
            raise HTTPException(422, f"Unknown setting '{key}'")
        if key == "export_min_role" and value not in ROLES:
            raise HTTPException(422, f"export_min_role must be one of {ROLES}")
        if key == "export_job_functions":
            if not isinstance(value, list) or any(v not in JOB_FUNCTIONS for v in value):
                raise HTTPException(422, f"export_job_functions must be a list drawn from {JOB_FUNCTIONS}")
            value = sorted(set(value))
        if key == "retention_days" and value is not None:
            if not isinstance(value, int) or isinstance(value, bool) or not MIN_RETENTION_DAYS <= value <= MAX_RETENTION_DAYS:
                raise HTTPException(422, f"retention_days must be empty or {MIN_RETENTION_DAYS}-{MAX_RETENTION_DAYS}")
        if key == "retention_keep_latest" and not isinstance(value, bool):
            raise HTTPException(422, "retention_keep_latest must be true or false")
        out[key] = value
    return out


def can_export(user: User, settings: dict) -> bool:
    if user.role == "admin":
        return True
    if not role_at_least(user.role, settings["export_min_role"]):
        return False
    allowed = settings.get("export_job_functions") or []
    return not allowed or user.job_function in allowed


async def require_export(session: AsyncSession, user: User) -> None:
    if not can_export(user, org_settings(await session.get(Organization, user.org_id))):
        raise HTTPException(403, "Your organization does not allow your role to export analysis output")


# --------------------------------------------------------------------------- deletion


async def delete_runs(session: AsyncSession, run_ids: list[str]) -> int:
    """Delete runs and everything derived from them (explicitly: SQLite does not enforce cascades)."""
    if not run_ids:
        return 0
    for model in (Notification, Alert, ReviewOverride, SalesDocument, Report, Approval, EvidenceRecord,
                  AgentExecution):
        await session.execute(delete(model).where(model.run_id.in_(run_ids)))
    # Later versions and comparisons keep working; they just lose the link to the deleted run.
    # (updated_at is kept: unlinking is not activity, and retention ages runs by it.)
    await session.execute(update(AnalysisRun).where(AnalysisRun.parent_run_id.in_(run_ids))
                          .values(parent_run_id=None, updated_at=AnalysisRun.updated_at))
    await session.execute(update(AnalysisRun).where(AnalysisRun.baseline_run_id.in_(run_ids))
                          .values(baseline_run_id=None, updated_at=AnalysisRun.updated_at))
    await session.execute(update(Monitor).where(Monitor.last_run_id.in_(run_ids)).values(last_run_id=None))
    result = await session.execute(delete(AnalysisRun).where(AnalysisRun.id.in_(run_ids)))
    return result.rowcount or 0


async def _ensure_idle(session: AsyncSession, condition) -> None:
    busy = (await session.execute(select(AnalysisRun.id).where(condition, AnalysisRun.status.in_(ACTIVE)))).first()
    if busy:
        raise HTTPException(409, f"An analysis is still in progress ({busy[0]}); cancel it before deleting.")


async def delete_run(session: AsyncSession, run: AnalysisRun) -> None:
    if run.status in ACTIVE:
        raise HTTPException(409, "The run is still in progress; cancel it before deleting.")
    await delete_runs(session, [run.id])


async def delete_projects(session: AsyncSession, project_ids: list[str]) -> dict:
    if not project_ids:
        return {"projects": 0, "runs": 0}
    await _ensure_idle(session, AnalysisRun.project_id.in_(project_ids))
    run_ids = list((await session.execute(select(AnalysisRun.id).where(
        AnalysisRun.project_id.in_(project_ids)))).scalars())
    runs = await delete_runs(session, run_ids)
    for model in (Notification, Alert, SalesDocument, Monitor, ProjectMember):
        await session.execute(delete(model).where(model.project_id.in_(project_ids)))
    projects = (await session.execute(delete(Project).where(Project.id.in_(project_ids)))).rowcount or 0
    return {"projects": projects, "runs": runs}


async def delete_client(session: AsyncSession, client: Client) -> dict:
    project_ids = list((await session.execute(select(Project.id).where(
        Project.client_id == client.id, Project.org_id == client.org_id))).scalars())
    counts = await delete_projects(session, project_ids)
    await session.execute(delete(Client).where(Client.id == client.id))
    return counts


# --------------------------------------------------------------------------- retention


async def expired_runs(session: AsyncSession, org_id: str, settings: dict, now: datetime | None = None) -> list[str]:
    days = settings.get("retention_days")
    if not days:
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    rows = (await session.execute(select(AnalysisRun.id, AnalysisRun.project_id, AnalysisRun.status,
                                         AnalysisRun.updated_at).where(AnalysisRun.org_id == org_id)
                                  .order_by(AnalysisRun.created_at.desc()))).all()
    keep: set[str] = set()
    if settings.get("retention_keep_latest", True):
        seen: set[str] = set()
        for run_id, project_id, status, _ in rows:
            if status in DONE and project_id not in seen:
                seen.add(project_id)
                keep.add(run_id)
    out = []
    for run_id, _, status, updated in rows:
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if status in FINISHED and run_id not in keep and updated < cutoff:
            out.append(run_id)
    return out


async def purge(session: AsyncSession, org: Organization, now: datetime | None = None, *,
                dry_run: bool = False) -> dict:
    """Apply the organization's retention policy. The caller commits (and audits)."""
    now = now or datetime.now(timezone.utc)
    settings = org_settings(org)
    run_ids = await expired_runs(session, org.id, settings, now)
    out = {"retention_days": settings["retention_days"], "runs": len(run_ids), "run_ids": run_ids[:100],
           "notifications": 0}
    if dry_run or not settings["retention_days"]:
        return out
    await delete_runs(session, run_ids)
    cutoff = now - timedelta(days=settings["retention_days"])
    out["notifications"] = (await session.execute(delete(Notification).where(
        Notification.org_id == org.id, Notification.created_at < cutoff))).rowcount or 0
    await session.execute(delete(Alert).where(Alert.org_id == org.id, Alert.created_at < cutoff))
    org.settings = {**(org.settings or {}), "last_purge_at": now.isoformat()}
    return out


async def purge_all(now: datetime | None = None, min_interval: timedelta = timedelta(hours=1)) -> dict[str, int]:
    """Scheduler hook: purge every organization with a retention policy, at most once per ``min_interval``."""
    from cip.db import session as db
    from cip.services import audit

    now = now or datetime.now(timezone.utc)
    purged: dict[str, int] = {}
    async with db.sessionmaker()() as s:
        orgs = (await s.execute(select(Organization))).scalars().all()
        for org in orgs:
            settings = org_settings(org)
            if not settings["retention_days"]:
                continue
            last = settings.get("last_purge_at")
            if last and now - datetime.fromisoformat(last) < min_interval:
                continue
            result = await purge(s, org, now)
            if result["runs"] or result["notifications"]:
                audit.record(s, None, None, "retention.purged", "organization", org.id, org_id=org.id,
                             email="scheduler", runs=result["runs"], notifications=result["notifications"],
                             retention_days=result["retention_days"])
            purged[org.id] = result["runs"]
        await s.commit()
    return purged
