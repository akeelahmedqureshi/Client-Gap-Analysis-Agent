"""Portfolio & cross-client intelligence (BRS 33, 35; PRD 10.39-10.41). Logic in ``services/portfolio.py``."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import require_role
from cip.db.models import User
from cip.db.session import get_session
from cip.api.deps import not_found
from cip.services import benchmarks, portfolio
from cip.services.access import run_for

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


@router.get("/benchmarks")
async def list_benchmarks(user: User = Depends(require_role("viewer")),
                          session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Industries with analysed companies (clients and their competitors)."""
    return await benchmarks.industries(session, user)


@router.get("/benchmarks/{industry}")
async def get_benchmark(industry: str, user: User = Depends(require_role("viewer")),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """Capability adoption across the industry, each client's position, predicted (estimate) and sourced trends."""
    out = await benchmarks.benchmark(session, user, industry)
    if out is None:
        raise not_found("Industry benchmark")
    return out


@router.get("/runs/{run_id}/benchmark")
async def run_benchmark(run_id: str, user: User = Depends(require_role("viewer")),
                        session: AsyncSession = Depends(get_session)) -> dict:
    """This run's client against its industry benchmark (internal: other clients are never named)."""
    run = await run_for(session, user, run_id)
    out = await benchmarks.for_run(session, user, run)
    if out is None:
        raise not_found("Industry benchmark")
    return out
