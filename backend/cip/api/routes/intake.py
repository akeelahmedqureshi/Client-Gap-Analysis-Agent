"""Manual client entry and the client schema (shared with the CSV upload). Rules: services/intake.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import require_role
from cip.core import client_schema
from cip.db.models import User
from cip.db.session import get_session
from cip.services import intake

router = APIRouter(prefix="/api/intake", tags=["intake"])
ROW = 1  # the manual form is a one-row intake


class ManualIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    decision: intake.Decision | None = None


def _plan(body: ManualIn) -> intake.Plan:
    return intake.Plan(decisions={ROW: body.decision} if body.decision else {})


def _rows(body: ManualIn) -> list[intake.Incoming]:
    values = {k: v for k, v in body.values.items() if k in client_schema.BY_KEY}
    return intake.incoming([(ROW, values)], intake.Plan())


@router.get("/schema")
async def schema(user: User = Depends(require_role("viewer"))) -> dict:
    """The client fields: label, input kind, mandatory, CSV column names and help. The form and the CSV template
    are built from it."""
    return {"fields": client_schema.schema(), "required": list(client_schema.REQUIRED)}


@router.post("/review")
async def review_manual(body: ManualIn, user: User = Depends(require_role("analyst")),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Validate the form and compare it with existing clients; nothing is saved."""
    result = await intake.review(session, user, _rows(body), _plan(body))
    return {"row": result["rows"][0], "summary": result["summary"]}


@router.post("/clients")
async def add_manual(body: ManualIn, request: Request, user: User = Depends(require_role("analyst")),
                     session: AsyncSession = Depends(get_session)) -> dict:
    """Save the client with the confirmed decision (add, or update / replace / merge an existing client)."""
    try:
        out = await intake.apply(session, request, user, _rows(body), _plan(body))
    except intake.IntakeError as exc:
        await session.rollback()
        raise HTTPException(exc.status, {"message": exc.message,
                                         "row": exc.review["rows"][0] if exc.review else None}) from exc
    if out["failed"]:
        await session.rollback()
        raise HTTPException(409, {"message": out["failed"][0]["reason"]})
    await session.commit()
    return out
