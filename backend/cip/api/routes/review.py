"""Human review of a finished run (BRS 17, PRD 10.34). Rules in ``services/review.py``."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.db.models import AgentExecution, ReviewOverride, User
from cip.db.session import get_session
from cip.core import review as rv
from cip.services import audit, configuration
from cip.services.access import run_for
from cip.services.rerun import FINISHED, RerunError, create_rerun
from cip.services.runner import runner

router = APIRouter(prefix="/api/runs", tags=["human review"])


class OverrideIn(BaseModel):
    kind: str = Field(max_length=30)
    target_id: str = Field(min_length=1, max_length=80)
    field: str = Field("", max_length=40)
    value: str = Field(min_length=1, max_length=2000)
    note: str = Field("", max_length=2000)


class OverrideOut(BaseModel):
    id: str
    kind: str
    target_id: str
    target_label: str
    field: str
    value: str
    note: str
    status: str
    applied_run_id: str | None
    created_by_email: str | None
    created_at: datetime


def _out(o: ReviewOverride) -> OverrideOut:
    return OverrideOut(id=o.id, kind=o.kind, target_id=o.target_id, target_label=o.target_label, field=o.field, value=o.value, note=o.note,
                       status=o.status, applied_run_id=o.applied_run_id, created_by_email=o.created_by_email,
                       created_at=o.created_at)


async def _results(session: AsyncSession, run_id: str) -> dict[str, dict]:
    rows = (await session.execute(select(AgentExecution).where(AgentExecution.run_id == run_id))).scalars().all()
    return {e.agent: e.result or {} for e in rows if e.status == "completed"}


async def _overrides(session: AsyncSession, run_id: str, org_id: str) -> list[ReviewOverride]:
    return list((await session.execute(select(ReviewOverride).where(
        ReviewOverride.run_id == run_id, ReviewOverride.org_id == org_id)
        .order_by(ReviewOverride.created_at))).scalars())


@router.get("/{run_id}/review", response_model=list[OverrideOut])
async def list_review(run_id: str, user: User = Depends(require_role("viewer")),
                      session: AsyncSession = Depends(get_session)) -> list[OverrideOut]:
    run = await run_for(session, user, run_id)
    return [_out(o) for o in await _overrides(session, run.id, run.org_id)]


@router.put("/{run_id}/review", response_model=OverrideOut)
async def set_review(run_id: str, body: OverrideIn, request: Request, user: User = Depends(require_role("analyst")),
                     session: AsyncSession = Depends(get_session)) -> OverrideOut:
    run = await run_for(session, user, run_id)
    if run.status not in FINISHED:
        raise HTTPException(409, "Review a run once it has finished")
    try:
        label = rv.resolve(body.kind, body.target_id, body.field, body.value, await _results(session, run.id),
                           await configuration.run_taxonomy(session, run))
    except rv.ReviewError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    o = (await session.execute(select(ReviewOverride).where(
        ReviewOverride.run_id == run.id, ReviewOverride.kind == body.kind, ReviewOverride.target_id == body.target_id,
        ReviewOverride.field == body.field))).scalar_one_or_none()
    if o is None:
        o = ReviewOverride(org_id=run.org_id, run_id=run.id, kind=body.kind, target_id=body.target_id,
                           field=body.field, value=body.value)
        session.add(o)
    elif o.status == "applied":
        raise HTTPException(409, "This override was already applied; review the new run version instead")
    o.value, o.note, o.target_label = body.value, body.note, label
    o.created_by, o.created_by_email = user.id, user.email
    audit.record(session, request, user, "review.override", "run", run.id, kind=body.kind, target=body.target_id,
                 field=body.field or None, value=body.value[:200])
    await session.commit()
    await session.refresh(o)
    return _out(o)


@router.delete("/{run_id}/review/{override_id}", status_code=204)
async def delete_review(run_id: str, override_id: str, request: Request, user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> None:
    run = await run_for(session, user, run_id)
    o = await session.get(ReviewOverride, override_id)
    if o is None or o.run_id != run.id or o.org_id != run.org_id:
        raise not_found("Override")
    if o.status == "applied":
        raise HTTPException(409, "An applied override cannot be removed")
    await session.delete(o)
    audit.record(session, request, user, "review.override_removed", "run", run.id, kind=o.kind, target=o.target_id)
    await session.commit()


@router.post("/{run_id}/review/apply", status_code=201)
async def apply_review(run_id: str, request: Request, user: User = Depends(require_role("analyst")),
                       session: AsyncSession = Depends(get_session)) -> dict:
    """Create a new run version with the pending overrides applied and the affected stages re-run."""
    run = await run_for(session, user, run_id)
    overrides = await _overrides(session, run.id, run.org_id)
    pending = [o for o in overrides if o.status == "pending"]
    if not pending:
        raise HTTPException(409, "There are no pending review changes")
    as_items = lambda rows: [{"kind": o.kind, "label": o.target_label, "field": o.field, "value": o.value,  # noqa: E731
                              "note": o.note, "by": o.created_by_email} for o in rows]
    items = as_items(pending)
    every = as_items(overrides)  # earlier applied overrides keep applying to reused stages
    taxonomy = await configuration.run_taxonomy(session, run)
    try:
        new = await create_rerun(session, run, rv.stages_for(items), user,
                                 patch=lambda agent, result: rv.patch(agent, result, every, taxonomy))
    except RerunError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    for o in pending:
        o.status, o.applied_run_id = "applied", new.id
        session.add(ReviewOverride(org_id=o.org_id, run_id=new.id, kind=o.kind, target_id=o.target_id,
                                   target_label=o.target_label, field=o.field,
                                   value=o.value, note=o.note, status="applied", applied_run_id=new.id,
                                   created_by=o.created_by, created_by_email=o.created_by_email))
    audit.record(session, request, user, "review.applied", "run", new.id, parent_run_id=run.id, overrides=len(pending))
    await session.commit()
    runner.start(new.id)
    return {"run_id": new.id, "applied": len(pending), "stages": new.rerun_stages}
