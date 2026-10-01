from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import require_role
from cip.db.models import AnalysisRun, Client, Project, ProjectMember, User
from cip.db.session import get_session
from cip.services import audit
from cip.services.access import project_for, visible_projects

router = APIRouter(prefix="/api", tags=["clients & projects"])


class ProjectOut(BaseModel):
    id: str
    client_id: str
    client_name: str
    name: str
    url: str | None
    description: str | None
    record: dict
    restricted: bool
    created_at: datetime
    latest_run: dict | None = None


class ClientOut(BaseModel):
    id: str
    name: str
    domain: str | None
    industry: str | None
    project_count: int
    created_at: datetime


class AccessOut(BaseModel):
    restricted: bool
    member_ids: list[str]


class AccessIn(BaseModel):
    restricted: bool
    member_ids: list[str] = []


async def _latest_runs(session: AsyncSession, project_ids: list[str]) -> dict[str, dict]:
    if not project_ids:
        return {}
    runs = (await session.execute(select(AnalysisRun).where(AnalysisRun.project_id.in_(project_ids))
                                  .order_by(AnalysisRun.created_at))).scalars().all()
    return {r.project_id: {"id": r.id, "status": r.status, "created_at": r.created_at.isoformat()} for r in runs}


def _out(p: Project, c: Client, latest: dict[str, dict]) -> ProjectOut:
    return ProjectOut(id=p.id, client_id=c.id, client_name=c.name, name=p.name, url=p.url,
                      description=p.description, record=p.record, restricted=p.restricted,
                      created_at=p.created_at, latest_run=latest.get(p.id))


@router.get("/clients", response_model=list[ClientOut])
async def list_clients(user: User = Depends(require_role("viewer")),
                       session: AsyncSession = Depends(get_session)) -> list[ClientOut]:
    project_clients = (await session.execute(select(Project.client_id).where(visible_projects(user)))).scalars().all()
    counts: dict[str, int] = {}
    for cid in project_clients:
        counts[cid] = counts.get(cid, 0) + 1
    clients = (await session.execute(select(Client).where(Client.org_id == user.org_id)
                                     .order_by(Client.name))).scalars().all()
    # Non-admins only see clients that have at least one project visible to them.
    return [ClientOut(id=c.id, name=c.name, domain=c.domain, industry=c.industry, project_count=counts.get(c.id, 0),
                      created_at=c.created_at) for c in clients if user.role == "admin" or c.id in counts]


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(client_id: str | None = None, user: User = Depends(require_role("viewer")),
                        session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    q = select(Project, Client).join(Client).where(visible_projects(user))
    if client_id:
        q = q.where(Project.client_id == client_id)
    rows = (await session.execute(q.order_by(Project.created_at.desc()))).all()
    latest = await _latest_runs(session, [p.id for p, _ in rows])
    return [_out(p, c, latest) for p, c in rows]


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, user: User = Depends(require_role("viewer")),
                      session: AsyncSession = Depends(get_session)) -> ProjectOut:
    p = await project_for(session, user, project_id)
    c = await session.get(Client, p.client_id)
    return _out(p, c, await _latest_runs(session, [p.id]))


@router.get("/projects/{project_id}/access", response_model=AccessOut)
async def get_access(project_id: str, admin: User = Depends(require_role("admin")),
                     session: AsyncSession = Depends(get_session)) -> AccessOut:
    p = await project_for(session, admin, project_id)
    members = (await session.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == p.id))) \
        .scalars().all()
    return AccessOut(restricted=p.restricted, member_ids=list(members))


@router.put("/projects/{project_id}/access", response_model=AccessOut)
async def set_access(project_id: str, body: AccessIn, request: Request, admin: User = Depends(require_role("admin")),
                     session: AsyncSession = Depends(get_session)) -> AccessOut:
    """Restrict a project to admins + the listed members, or open it to the whole organization."""
    p = await project_for(session, admin, project_id)
    member_ids = list(dict.fromkeys(body.member_ids))
    if member_ids:
        valid = set((await session.execute(select(User.id).where(User.org_id == admin.org_id,
                                                                 User.id.in_(member_ids)))).scalars())
        unknown = [m for m in member_ids if m not in valid]
        if unknown:
            raise HTTPException(422, f"Unknown users: {unknown}")
    p.restricted = body.restricted
    await session.execute(delete(ProjectMember).where(ProjectMember.project_id == p.id))
    for uid in member_ids:
        session.add(ProjectMember(project_id=p.id, user_id=uid, added_by=admin.id))
    audit.record(session, request, admin, "project.access_changed", "project", p.id,
                 restricted=body.restricted, members=member_ids)
    await session.commit()
    return AccessOut(restricted=p.restricted, member_ids=member_ids)
