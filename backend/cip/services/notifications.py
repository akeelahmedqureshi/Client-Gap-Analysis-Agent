"""Run notifications for people (BRS 30; PRD 10.43): in-app inbox and email, per-user preferences.

Events: ``run_completed`` (the report is ready), ``run_failed``, ``approval_needed``, ``needs_review`` (the
QA agent wants a person to check the results) and ``outreach_ready`` (an outreach draft is waiting).

Recipients are the person who started the run (for scheduled runs, whoever set up the monitor) plus anyone
whose preferences say ``scope: "all"``. A recipient must be active and still able to see the project; a
restricted project never notifies people outside it. Each (user, run, event) is notified at most once.
Notification text carries names, statuses and a link only: no evidence, tokens or prompt content.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.config import get_settings
from cip.db import session as db
from cip.db.models import AgentExecution, AnalysisRun, Approval, Monitor, Notification, Project, User
from cip.services import notify
from cip.services.access import visible_projects

log = logging.getLogger(__name__)

EVENTS: dict[str, str] = {
    "run_completed": "Analysis completed (report ready)",
    "run_failed": "Analysis failed",
    "approval_needed": "Analysis waiting for your approval",
    "needs_review": "Analysis needs a human review",
    "outreach_ready": "Outreach draft ready for review",
}
DEFAULTS: dict[str, dict[str, bool]] = {
    "run_completed": {"in_app": True, "email": False},
    "run_failed": {"in_app": True, "email": True},
    "approval_needed": {"in_app": True, "email": True},
    "needs_review": {"in_app": True, "email": False},
    "outreach_ready": {"in_app": True, "email": False},
}
SCOPES = ("mine", "all")


def preferences(user: User) -> dict:
    stored = user.notification_prefs or {}
    events = {e: {**DEFAULTS[e], **{k: bool(v) for k, v in (stored.get("events", {}).get(e) or {}).items()
                                    if k in ("in_app", "email")}} for e in EVENTS}
    scope = stored.get("scope") if stored.get("scope") in SCOPES else "mine"
    return {"scope": scope, "events": events}


def validate_preferences(body: dict) -> dict:
    out: dict = {}
    if "scope" in body:
        if body["scope"] not in SCOPES:
            raise ValueError(f"scope must be one of {SCOPES}")
        out["scope"] = body["scope"]
    events = body.get("events") or {}
    if not isinstance(events, dict):
        raise ValueError("events must be an object")
    out["events"] = {}
    for event, channels in events.items():
        if event not in EVENTS or not isinstance(channels, dict):
            raise ValueError(f"Unknown event '{event}'; use one of {sorted(EVENTS)}")
        out["events"][event] = {k: bool(v) for k, v in channels.items() if k in ("in_app", "email")}
    return out


async def events_for(session: AsyncSession, run: AnalysisRun, status: str) -> list[tuple[str, str]]:
    """(event, detail) pairs raised by a run reaching ``status``."""
    if status == "awaiting_approval":
        pending = (await session.execute(select(Approval.title).where(
            Approval.run_id == run.id, Approval.status == "pending"))).scalars().all()
        return [("approval_needed", "Pending: " + "; ".join(pending) if pending else "")]
    if status == "failed":
        return [("run_failed", (run.error or "")[:300])]
    if status not in ("completed", "completed_with_errors"):
        return []
    results = {e.agent: (e.result or {}).get("data", {}) for e in (await session.execute(select(AgentExecution).where(
        AgentExecution.run_id == run.id, AgentExecution.status == "completed",
        AgentExecution.agent.in_(["quality_assurance", "outreach"])))).scalars()}
    qa = results.get("quality_assurance", {})
    out = [("run_completed", f"Quality: {qa['label']}." if qa.get("label") else
            ("Completed with errors in some stages." if status == "completed_with_errors" else ""))]
    if qa.get("state") == "needs_review":
        issues = [i.get("message", "") for i in qa.get("issues", []) if i.get("severity") == "blocking"][:3]
        out.append(("needs_review", "; ".join(issues)))
    if (results.get("outreach") or {}).get("body"):
        out.append(("outreach_ready", "A draft email is waiting for review and approval in the Sales tab."))
    return out


async def recipients(session: AsyncSession, run: AnalysisRun, project: Project) -> list[User]:
    owner_id = run.created_by
    if owner_id is None and run.monitor_id:
        monitor = await session.get(Monitor, run.monitor_id)
        owner_id = monitor.created_by if monitor else None
    users = (await session.execute(select(User).where(User.org_id == run.org_id, User.is_active.is_(True)))).scalars()
    out = []
    for u in users:
        if u.id != owner_id and preferences(u)["scope"] != "all":
            continue
        visible = (await session.execute(select(Project.id).where(Project.id == project.id,
                                                                  visible_projects(u)))).first()
        if visible:
            out.append(u)
    return out


async def notify_run(run_id: str, status: str) -> list[Notification]:
    """Create notifications for a run that reached ``status`` and send the email copies. Never raises."""
    created: list[tuple[Notification, User, Project]] = []
    async with db.sessionmaker()() as s:
        run = await s.get(AnalysisRun, run_id)
        if run is None:
            return []
        project = await s.get(Project, run.project_id)
        events = await events_for(s, run, status)
        if not events:
            return []
        people = await recipients(s, run, project)
        for event, detail in events:
            for u in people:
                prefs = preferences(u)["events"][event]
                if not (prefs["in_app"] or prefs["email"]):
                    continue
                exists = (await s.execute(select(Notification.id).where(
                    Notification.user_id == u.id, Notification.run_id == run.id, Notification.event == event))).first()
                if exists:
                    continue
                n = Notification(org_id=run.org_id, user_id=u.id, project_id=project.id, run_id=run.id, event=event,
                                 title=f"{EVENTS[event]}: {project.name}", body=detail, in_app=prefs["in_app"])
                s.add(n)
                created.append((n, u, project))
        await s.commit()
        settings = get_settings()
        for n, u, p in created:
            if not preferences(u)["events"][n.event]["email"]:
                continue
            link = notify.run_link(settings, run.id)
            body = "\n".join(x for x in (n.title, n.body, f"Open the analysis: {link}",
                                         "You can change which notifications you receive in Settings.") if x)
            try:
                n.delivery = [await notify.send_email([u.email], n.title, body, settings)]
            except Exception:  # noqa: BLE001 - delivery problems never affect the run
                log.exception("Notification email failed")
                n.delivery = [{"channel": "email", "ok": False, "error": "unexpected"}]
        await s.commit()
    return [n for n, _, _ in created]
