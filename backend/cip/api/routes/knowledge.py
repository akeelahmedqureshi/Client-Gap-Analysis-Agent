"""Internal knowledge base: capabilities, solutions, projects and case studies (BRS 27, PRD 10.47).

Visibility and governance rules live in ``services/knowledge.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.db.models import KnowledgeRecord, KnowledgeRecordVersion, User
from cip.db.session import get_session
from cip.services import audit
from cip.services import knowledge as kb

router = APIRouter(prefix="/api/knowledge", tags=["knowledge base"])

Kind = Literal["capability", "solution", "project", "case_study"]
Status = Literal["draft", "in_review", "approved", "restricted", "archived"]
Visibility = Literal["internal", "client_facing"]
TagList = list[str]


class KnowledgeIn(BaseModel):
    kind: Kind
    title: str = Field(min_length=2, max_length=300)
    summary: str = Field("", max_length=4000)
    details: str = Field("", max_length=20000)
    outcomes: str = Field("", max_length=4000)
    customer_name: str | None = Field(None, max_length=300)
    industries: TagList = Field(default_factory=list, max_length=50)
    technologies: TagList = Field(default_factory=list, max_length=50)
    project_types: TagList = Field(default_factory=list, max_length=50)
    capability_tags: TagList = Field(default_factory=list, max_length=50)
    ai: bool = False
    automation: bool = False
    linked_ids: list[str] = Field(default_factory=list, max_length=50)
    visibility: Visibility = "internal"
    reference_allowed: bool = False
    status: Status = "draft"
    note: str = Field("", max_length=1000)


class KnowledgePatch(BaseModel):
    kind: Kind | None = None
    title: str | None = Field(None, min_length=2, max_length=300)
    summary: str | None = Field(None, max_length=4000)
    details: str | None = Field(None, max_length=20000)
    outcomes: str | None = Field(None, max_length=4000)
    customer_name: str | None = Field(None, max_length=300)
    industries: TagList | None = Field(None, max_length=50)
    technologies: TagList | None = Field(None, max_length=50)
    project_types: TagList | None = Field(None, max_length=50)
    capability_tags: TagList | None = Field(None, max_length=50)
    ai: bool | None = None
    automation: bool | None = None
    linked_ids: list[str] | None = Field(None, max_length=50)
    visibility: Visibility | None = None
    reference_allowed: bool | None = None
    note: str = Field("", max_length=1000)


class StatusIn(BaseModel):
    status: Status
    note: str = Field("", max_length=1000)


class KnowledgeOut(BaseModel):
    id: str
    kind: str
    title: str
    summary: str
    details: str
    outcomes: str
    customer_name: str | None
    industries: list[str]
    technologies: list[str]
    project_types: list[str]
    capability_tags: list[str]
    ai: bool
    automation: bool
    linked_ids: list[str]
    status: str
    visibility: str
    reference_allowed: bool
    client_facing: bool
    version: int
    created_by: str | None
    updated_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class VersionOut(BaseModel):
    version: int
    change: str
    note: str
    changed_by_email: str | None
    changed_at: datetime
    snapshot: dict


def _out(r: KnowledgeRecord) -> KnowledgeOut:
    return KnowledgeOut(**kb.snapshot(r), client_facing=r.status == "approved" and r.visibility == "client_facing",
                        created_by=r.created_by, updated_by=r.updated_by, approved_by=r.approved_by,
                        approved_at=r.approved_at, created_at=r.created_at, updated_at=r.updated_at)


def _raise(exc: kb.KnowledgeError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _matches(r: KnowledgeRecord, q: str | None, industry: str | None, technology: str | None,
             tag: str | None) -> bool:
    def has(values: list[str] | None, wanted: str) -> bool:
        return any(wanted.lower() == v.lower() for v in values or [])

    if industry and not has(r.industries, industry):
        return False
    if technology and not has(r.technologies, technology):
        return False
    if tag and not has(r.capability_tags, tag):
        return False
    if q:
        hay = " ".join([r.title, r.summary, r.details, r.outcomes, r.customer_name or "",
                        *(r.industries or []), *(r.technologies or []), *(r.project_types or []),
                        *(r.capability_tags or [])]).lower()
        return all(word in hay for word in q.lower().split())
    return True


@router.get("", response_model=list[KnowledgeOut])
async def list_records(q: str | None = Query(None, max_length=200), kind: Kind | None = None,
                       status: Status | None = None, industry: str | None = None, technology: str | None = None,
                       tag: str | None = None, ai: bool | None = None, automation: bool | None = None,
                       include_archived: bool = False, user: User = Depends(require_role("viewer")),
                       session: AsyncSession = Depends(get_session)) -> list[KnowledgeOut]:
    query = kb.visible(select(KnowledgeRecord), user).order_by(KnowledgeRecord.updated_at.desc())
    if kind:
        query = query.where(KnowledgeRecord.kind == kind)
    if status:
        query = query.where(KnowledgeRecord.status == status)
    elif not include_archived:
        query = query.where(KnowledgeRecord.status != "archived")
    if ai is not None:
        query = query.where(KnowledgeRecord.ai.is_(ai))
    if automation is not None:
        query = query.where(KnowledgeRecord.automation.is_(automation))
    rows = (await session.execute(query)).scalars().all()
    return [_out(r) for r in rows if _matches(r, q, industry, technology, tag)]


@router.post("", response_model=KnowledgeOut, status_code=201)
async def create_record(body: KnowledgeIn, request: Request, user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> KnowledgeOut:
    r = KnowledgeRecord(org_id=user.org_id, kind=body.kind, title=body.title.strip(), summary=body.summary,
                        details=body.details, outcomes=body.outcomes, customer_name=body.customer_name or None,
                        ai=body.ai, automation=body.automation, visibility=body.visibility,
                        reference_allowed=body.reference_allowed, status="draft", version=1,
                        created_by=user.id, updated_by=user.id)
    for f in kb.LIST_FIELDS[:-1]:
        setattr(r, f, kb.clean_list(getattr(body, f)))
    try:
        r.linked_ids = await kb.check_links(session, user.org_id, body.linked_ids)
        kb.apply_status(r, body.status, user)
    except kb.KnowledgeError as exc:
        raise _raise(exc) from exc
    session.add(r)
    await session.flush()
    kb.add_version(session, r, user, "created", body.note)
    audit.record(session, request, user, "knowledge.created", "knowledge", r.id, kind=r.kind, status=r.status)
    await session.commit()
    return _out(r)


@router.get("/{record_id}", response_model=KnowledgeOut)
async def get_record(record_id: str, user: User = Depends(require_role("viewer")),
                     session: AsyncSession = Depends(get_session)) -> KnowledgeOut:
    r = await kb.get_visible(session, user, record_id)
    if r is None:
        raise not_found("Knowledge record")
    return _out(r)


@router.patch("/{record_id}", response_model=KnowledgeOut)
async def update_record(record_id: str, body: KnowledgePatch, request: Request,
                        user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> KnowledgeOut:
    r = await kb.get_visible(session, user, record_id)
    if r is None:
        raise not_found("Knowledge record")
    changes = body.model_dump(exclude_unset=True, exclude={"note"})
    if not changes:
        return _out(r)
    try:
        kb.check_editable(r, user)
        if "linked_ids" in changes:
            changes["linked_ids"] = await kb.check_links(session, user.org_id, changes["linked_ids"] or [], r.id)
    except kb.KnowledgeError as exc:
        raise _raise(exc) from exc
    changed = []
    for field, value in changes.items():
        if field in kb.LIST_FIELDS and field != "linked_ids":
            value = kb.clean_list(value)
        if field == "title" and value:
            value = value.strip()
        if field == "customer_name":
            value = value or None
        if value is None and field not in ("customer_name",):
            continue
        if getattr(r, field) != value:
            setattr(r, field, value)
            changed.append(field)
    if not changed:
        return _out(r)
    resubmitted = user.role != "admin" and r.status == "approved"
    if resubmitted:
        r.status = "in_review"  # client-facing content must be re-approved after an edit
    r.version += 1
    r.updated_by = user.id
    kb.add_version(session, r, user, "edited", body.note)
    audit.record(session, request, user, "knowledge.updated", "knowledge", r.id, fields=changed,
                 version=r.version, resubmitted_for_review=resubmitted or None)
    await session.commit()
    await session.refresh(r)
    return _out(r)


@router.post("/{record_id}/status", response_model=KnowledgeOut)
async def set_status(record_id: str, body: StatusIn, request: Request, user: User = Depends(require_role("analyst")),
                     session: AsyncSession = Depends(get_session)) -> KnowledgeOut:
    r = await kb.get_visible(session, user, record_id)
    if r is None:
        raise not_found("Knowledge record")
    old = r.status
    try:
        kb.apply_status(r, body.status, user)
    except kb.KnowledgeError as exc:
        raise _raise(exc) from exc
    if r.status == old:
        return _out(r)
    r.version += 1
    r.updated_by = user.id
    kb.add_version(session, r, user, f"status:{r.status}", body.note)
    audit.record(session, request, user, "knowledge.status", "knowledge", r.id, before=old, after=r.status)
    await session.commit()
    await session.refresh(r)
    return _out(r)


@router.get("/{record_id}/versions", response_model=list[VersionOut])
async def versions(record_id: str, user: User = Depends(require_role("analyst")),
                   session: AsyncSession = Depends(get_session)) -> list[VersionOut]:
    r = await kb.get_visible(session, user, record_id)
    if r is None:
        raise not_found("Knowledge record")
    rows = (await session.execute(select(KnowledgeRecordVersion).where(
        KnowledgeRecordVersion.record_id == r.id, KnowledgeRecordVersion.org_id == user.org_id)
        .order_by(KnowledgeRecordVersion.version.desc()))).scalars().all()
    return [VersionOut(version=v.version, change=v.change, note=v.note, changed_by_email=v.changed_by_email,
                       changed_at=v.changed_at, snapshot=v.snapshot) for v in rows]
