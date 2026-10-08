"""Governance API: organization settings, deletion, retention, notifications and structured exports."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.db.models import AgentExecution, Client, EvidenceRecord, Notification, Organization, Project, User
from cip.db.session import get_session
from cip.services import audit, exports, governance, notifications
from cip.services.access import project_for, run_for, visible_projects

router = APIRouter(prefix="/api", tags=["governance"])


class NotificationOut(BaseModel):
    id: str
    event: str
    title: str
    body: str
    run_id: str | None
    project_id: str | None
    read_at: datetime | None
    created_at: datetime
    delivery: list


# --------------------------------------------------------------------------- organization settings


@router.get("/org/settings")
async def get_settings(user: User = Depends(require_role("viewer")),
                       session: AsyncSession = Depends(get_session)) -> dict:
    s = governance.org_settings(await session.get(Organization, user.org_id))
    return {"settings": s, "can_export": governance.can_export(user, s), "job_functions": list(governance.JOB_FUNCTIONS)}


@router.put("/org/settings")
async def put_settings(body: dict, request: Request, admin: User = Depends(require_role("admin")),
                       session: AsyncSession = Depends(get_session)) -> dict:
    org = await session.get(Organization, admin.org_id)
    patch = governance.validate_settings(body)
    before = governance.org_settings(org)
    changes = {k: [before.get(k), v] for k, v in patch.items() if before.get(k) != v}
    org.settings = {**(org.settings or {}), **patch}
    if changes:
        audit.record(session, request, admin, "org.settings_updated", "organization", org.id, changes=changes)
    await session.commit()
    return await get_settings(admin, session)


@router.post("/org/retention/purge")
async def purge_now(request: Request, dry_run: bool = False, admin: User = Depends(require_role("admin")),
                    session: AsyncSession = Depends(get_session)) -> dict:
    org = await session.get(Organization, admin.org_id)
    if not governance.org_settings(org)["retention_days"]:
        raise HTTPException(409, "Set a retention period first")
    result = await governance.purge(session, org, dry_run=dry_run)
    if not dry_run:
        audit.record(session, request, admin, "retention.purged", "organization", org.id,
                     runs=result["runs"], notifications=result["notifications"],
                     retention_days=result["retention_days"])
    await session.commit()
    return result


# --------------------------------------------------------------------------- deletion


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(run_id: str, request: Request, user: User = Depends(require_role("analyst")),
                     session: AsyncSession = Depends(get_session)) -> Response:
    """Admins may delete any visible run; analysts only runs they started."""
    run = await run_for(session, user, run_id)
    if user.role != "admin" and run.created_by != user.id:
        raise HTTPException(403, "Only an admin or the person who started the run can delete it")
    await governance.delete_run(session, run)
    audit.record(session, request, user, "run.deleted", "run", run_id, project_id=run.project_id, status=run.status)
    await session.commit()
    return Response(status_code=204)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request, admin: User = Depends(require_role("admin")),
                         session: AsyncSession = Depends(get_session)) -> Response:
    project = await project_for(session, admin, project_id)
    counts = await governance.delete_projects(session, [project.id])
    audit.record(session, request, admin, "project.deleted", "project", project_id, name=project.name,
                 runs=counts["runs"])
    await session.commit()
    return Response(status_code=204)


@router.delete("/clients/{client_id}", status_code=204)
async def delete_client(client_id: str, request: Request, admin: User = Depends(require_role("admin")),
                        session: AsyncSession = Depends(get_session)) -> Response:
    client = await session.get(Client, client_id)
    if client is None or client.org_id != admin.org_id:
        raise not_found("Client")
    counts = await governance.delete_client(session, client)
    audit.record(session, request, admin, "client.deleted", "client", client_id, name=client.name, **counts)
    await session.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- notifications


def _mine(user: User):
    # Only notifications for projects the user can still see (access may have changed since).
    visible = select(Project.id).where(visible_projects(user))
    return (Notification.user_id == user.id, Notification.org_id == user.org_id, Notification.in_app.is_(True),
            Notification.project_id.in_(visible))


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(unread_only: bool = False, limit: int = Query(50, ge=1, le=200),
                             user: User = Depends(require_role("viewer")),
                             session: AsyncSession = Depends(get_session)) -> list[NotificationOut]:
    stmt = select(Notification).where(*_mine(user))
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = (await session.execute(stmt.order_by(Notification.created_at.desc()).limit(limit))).scalars().all()
    return [NotificationOut.model_validate(n, from_attributes=True) for n in rows]


@router.get("/notifications/unread-count")
async def notifications_unread(user: User = Depends(require_role("viewer")),
                               session: AsyncSession = Depends(get_session)) -> dict:
    n = (await session.execute(select(func.count()).select_from(Notification).where(
        *_mine(user), Notification.read_at.is_(None)))).scalar_one()
    return {"count": n}


@router.post("/notifications/read-all", status_code=204)
async def notifications_read_all(user: User = Depends(require_role("viewer")),
                                 session: AsyncSession = Depends(get_session)) -> Response:
    await session.execute(update(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None)).values(read_at=datetime.now(timezone.utc)))
    await session.commit()
    return Response(status_code=204)


@router.post("/notifications/{notification_id}/read", status_code=204)
async def notification_read(notification_id: str, user: User = Depends(require_role("viewer")),
                            session: AsyncSession = Depends(get_session)) -> Response:
    n = await session.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise not_found("Notification")
    n.read_at = n.read_at or datetime.now(timezone.utc)
    await session.commit()
    return Response(status_code=204)


@router.get("/notifications/preferences")
async def get_preferences(user: User = Depends(require_role("viewer"))) -> dict:
    return {**notifications.preferences(user), "labels": notifications.EVENTS}


@router.put("/notifications/preferences")
async def put_preferences(body: dict, user: User = Depends(require_role("viewer")),
                          session: AsyncSession = Depends(get_session)) -> dict:
    try:
        patch = notifications.validate_preferences(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    current = notifications.preferences(user)
    events = {e: {**current["events"][e], **patch["events"].get(e, {})} for e in notifications.EVENTS}
    me = await session.get(User, user.id)
    me.notification_prefs = {"scope": patch.get("scope", current["scope"]), "events": events}
    await session.commit()
    return await get_preferences(me)


# --------------------------------------------------------------------------- structured exports

DATA_AGENTS = {"comparison": "feature_comparison", "gaps": "gap_analysis", "prioritization": "opportunity_prioritization"}


async def _evidence(session: AsyncSession, run_id: str) -> list[dict]:
    rows = (await session.execute(select(EvidenceRecord).where(EvidenceRecord.run_id == run_id)
                                  .order_by(EvidenceRecord.pk))).scalars().all()
    return [{"id": e.id, "claim": e.claim, "source_url": e.source_url, "source_type": e.source_type,
             "extracted_text": e.extracted_text, "repository_path": e.repository_path, "line_range": e.line_range,
             "confidence": e.confidence, "collected_at": e.collected_at.isoformat()} for e in rows]


@router.get("/runs/{run_id}/export/{name}")
async def export_run(run_id: str, name: str, request: Request, user: User = Depends(require_role("viewer")),
                     session: AsyncSession = Depends(get_session)) -> Response:
    """``matrix.csv``, ``gaps.csv``, ``opportunities.csv``, ``recommendations.csv``, ``evidence.csv`` or
    ``analysis.json`` (every agent result, the evidence ledger and run metadata)."""
    run = await run_for(session, user, run_id)
    kind, _, fmt = name.rpartition(".")
    if not ((fmt == "csv" and kind in exports.CSV_EXPORTS) or (fmt == "json" and kind == "analysis")):
        raise not_found("Export")
    await governance.require_export(session, user)
    execs = (await session.execute(select(AgentExecution).where(AgentExecution.run_id == run.id))).scalars().all()
    by_agent = {e.agent: e for e in execs}
    evidence = await _evidence(session, run.id)
    if fmt == "json":
        project = await session.get(Project, run.project_id)
        body = json.dumps({
            "run": {"id": run.id, "status": run.status, "project_id": run.project_id, "project": project.name,
                    "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat(),
                    "parent_run_id": run.parent_run_id, "rerun_stages": run.rerun_stages,
                    "scoring_weights": run.scoring_weights},
            "agents": {e.agent: {"status": e.status, "error": e.error, "result": e.result} for e in execs},
            "evidence": evidence,
            "note": "Internal export. Estimates are labelled basis=estimate; findings reference evidence ids.",
        }, indent=2, default=str)
        media, ext = "application/json", "json"
    else:
        data = {k: ((by_agent[a].result or {}).get("data", {}) if a in by_agent and by_agent[a].status == "completed"
                    else {}) for k, a in DATA_AGENTS.items()}
        body = "﻿" + exports.render_csv(kind, data, evidence)  # BOM: Excel opens UTF-8 correctly
        media, ext = "text/csv; charset=utf-8", "csv"
    audit.record(session, request, user, "run.exported", "run", run.id, export=name)
    await session.commit()
    return Response(body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{kind}-{run.id}.{ext}"'})
