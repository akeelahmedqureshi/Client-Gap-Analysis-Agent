"""Internal knowledge base: visibility rules, governance and versioning (BRS 27, PRD 10.47).

Who sees what:
    admin    every record, including *restricted* (confidential) ones
    analyst  everything except restricted records
    viewer   approved records only (never restricted)

Who changes what:
    analyst  creates records and edits drafts / in-review / approved records; submits them for review.
             Editing an approved record sends it back to *in review*, so client-facing content is always
             re-approved after a change.
    admin    everything, including approve, restrict, archive and restore.

Every change bumps ``version`` and stores a snapshot of the new state (who, when, what changed).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.db.models import KnowledgeRecord, KnowledgeRecordVersion, User

KINDS = ("capability", "solution", "project", "case_study")
STATUSES = ("draft", "in_review", "approved", "restricted", "archived")
VISIBILITIES = ("internal", "client_facing")
LIST_FIELDS = ("industries", "technologies", "project_types", "capability_tags", "linked_ids")
TEXT_FIELDS = ("kind", "title", "summary", "details", "outcomes", "customer_name", "ai", "automation",
               "visibility", "reference_allowed")
# Status changes an analyst may make; everything else needs an admin.
ANALYST_TRANSITIONS = {("draft", "in_review"), ("in_review", "draft")}


class KnowledgeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def snapshot(r: KnowledgeRecord) -> dict:
    return {
        "id": r.id, "kind": r.kind, "title": r.title, "summary": r.summary, "details": r.details,
        "outcomes": r.outcomes, "customer_name": r.customer_name, "industries": list(r.industries or []),
        "technologies": list(r.technologies or []), "project_types": list(r.project_types or []),
        "capability_tags": list(r.capability_tags or []), "ai": bool(r.ai), "automation": bool(r.automation),
        "linked_ids": list(r.linked_ids or []), "status": r.status, "visibility": r.visibility,
        "reference_allowed": bool(r.reference_allowed), "version": r.version,
    }


def visible(query: Select, user: User) -> Select:
    query = query.where(KnowledgeRecord.org_id == user.org_id)
    if user.role == "viewer":
        return query.where(KnowledgeRecord.status == "approved")
    if user.role != "admin":
        return query.where(KnowledgeRecord.status != "restricted")
    return query


async def get_visible(session: AsyncSession, user: User, record_id: str) -> KnowledgeRecord | None:
    return (await session.execute(visible(select(KnowledgeRecord), user)
                                  .where(KnowledgeRecord.id == record_id))).scalar_one_or_none()


def add_version(session: AsyncSession, r: KnowledgeRecord, user: User, change: str, note: str = "") -> None:
    session.add(KnowledgeRecordVersion(record_id=r.id, org_id=r.org_id, version=r.version, snapshot=snapshot(r),
                                       change=change, note=note, changed_by=user.id, changed_by_email=user.email))


def clean_list(values: list[str] | None, limit: int = 50) -> list[str]:
    out: list[str] = []
    for v in values or []:
        v = " ".join(str(v).split())[:120]
        if v and v.lower() not in {o.lower() for o in out}:
            out.append(v)
    return out[:limit]


async def check_links(session: AsyncSession, org_id: str, ids: list[str], self_id: str | None = None) -> list[str]:
    ids = [i for i in dict.fromkeys(ids) if i != self_id]
    if not ids:
        return []
    found = set((await session.execute(select(KnowledgeRecord.id).where(
        KnowledgeRecord.org_id == org_id, KnowledgeRecord.id.in_(ids)))).scalars())
    missing = [i for i in ids if i not in found]
    if missing:
        raise KnowledgeError(422, f"Linked records not found: {', '.join(missing)}")
    return ids


def apply_status(r: KnowledgeRecord, new: str, user: User) -> None:
    if new not in STATUSES:
        raise KnowledgeError(422, f"Unknown status '{new}'")
    if new == r.status:
        return
    if user.role != "admin" and (r.status, new) not in ANALYST_TRANSITIONS:
        raise KnowledgeError(403, "Only an admin can approve, restrict, archive or restore knowledge records")
    r.status = new
    if new == "approved":
        r.approved_by, r.approved_at = user.id, datetime.now(timezone.utc)


def check_editable(r: KnowledgeRecord, user: User) -> None:
    if user.role != "admin" and r.status in ("restricted", "archived"):
        raise KnowledgeError(403, "Only an admin can edit restricted or archived records")


async def load_for_run(session: AsyncSession, org_id: str) -> list[dict]:
    """Approved records for matching. Restricted, draft and archived records never reach a run."""
    rows = (await session.execute(select(KnowledgeRecord).where(
        KnowledgeRecord.org_id == org_id, KnowledgeRecord.status == "approved"))).scalars().all()
    return [snapshot(r) for r in rows]
