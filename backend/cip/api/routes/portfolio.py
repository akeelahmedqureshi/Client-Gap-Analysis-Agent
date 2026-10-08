"""Portfolio & cross-client intelligence (BRS 33, 35; PRD 10.39-10.41). Logic in ``services/portfolio.py``."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import require_role
from cip.db.models import User
from cip.db.session import get_session
from cip.services import portfolio

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("")
async def get_portfolio(q: str | None = Query(None, max_length=200), industry: str | None = None,
                        status: str | None = None, priority: Literal["high", "medium", "low"] | None = None,
                        sort: Literal["score", "date", "name", "confidence"] = "score",
                        user: User = Depends(require_role("viewer")),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Summary counts, the client/project list (searchable, filterable, sortable) and cross-client insights,
    from each visible project's latest completed analysis."""
    data = await portfolio.build(session, user)
    data["projects"] = portfolio.filter_projects(data["projects"], q=q, industry=industry, status=status,
                                                 priority=priority, sort=sort)
    return data
