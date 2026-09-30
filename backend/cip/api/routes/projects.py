from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.db.models import AnalysisRun, Client, Project, User
from cip.db.session import get_session

router = APIRouter(prefix="/api", tags=["clients & projects"])


class ProjectOut(BaseModel):
    id: str
    client_id: str
    client_name: str
    name: str
    url: str | None
    description: str | None
    record: dict
    created_at: datetime
    latest_run: dict | None = None


class ClientOut(BaseModel):
    id: str
    name: str
    domain: str | None
    industry: str | None
    project_count: int
    created_at: datetime


async def _latest_runs(session: AsyncSession, org_id: str) -> dict[str, dict]:
    runs = (await session.execute(select(AnalysisRun).where(AnalysisRun.org_id == org_id)
                                  .order_by(AnalysisRun.created_at))).scalars().all()
    return {r.project_id: {"id": r.id, "status": r.status, "created_at": r.created_at.isoformat()} for r in runs}


@router.get("/clients", response_model=list[ClientOut])
async def list_clients(user: User = Depends(require_role("viewer")),
                       session: AsyncSession = Depends(get_session)) -> list[ClientOut]:
    clients = (await session.execute(select(Client).where(Client.org_id == user.org_id)
                                     .order_by(Client.name))).scalars().all()
    projects = (await session.execute(select(Project.client_id).where(Project.org_id == user.org_id))).scalars().all()
    counts: dict[str, int] = {}
    for cid in projects:
        counts[cid] = counts.get(cid, 0) + 1
    return [ClientOut(id=c.id, name=c.name, domain=c.domain, industry=c.industry, project_count=counts.get(c.id, 0),
                      created_at=c.created_at) for c in clients]


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(client_id: str | None = None, user: User = Depends(require_role("viewer")),
                        session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    q = select(Project, Client).join(Client).where(Project.org_id == user.org_id)
    if client_id:
        q = q.where(Project.client_id == client_id)
    rows = (await session.execute(q.order_by(Project.created_at.desc()))).all()
    latest = await _latest_runs(session, user.org_id)
    return [ProjectOut(id=p.id, client_id=c.id, client_name=c.name, name=p.name, url=p.url,
                       description=p.description, record=p.record, created_at=p.created_at,
                       latest_run=latest.get(p.id)) for p, c in rows]


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, user: User = Depends(require_role("viewer")),
                      session: AsyncSession = Depends(get_session)) -> ProjectOut:
    p = await session.get(Project, project_id)
    if not p or p.org_id != user.org_id:
        raise not_found("Project")
    c = await session.get(Client, p.client_id)
    latest = await _latest_runs(session, user.org_id)
    return ProjectOut(id=p.id, client_id=c.id, client_name=c.name, name=p.name, url=p.url,
                      description=p.description, record=p.record, created_at=p.created_at,
                      latest_run=latest.get(p.id))
