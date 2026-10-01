from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found, require_role
from cip.connectors.source_control import parse_repo_url
from cip.db.models import AgentExecution, AnalysisRun, Client, Project, ProjectMember, User
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


class RepositoriesIn(BaseModel):
    urls: list[str] = Field(default_factory=list, max_length=10)


class ClientDetailOut(BaseModel):
    client: ClientOut
    projects: list[ProjectOut]
    # From the most recent completed run (visible to the caller) that researched this client.
    profile: dict | None = None
    profile_run_id: str | None = None
    recommendations: list[dict] = Field(default_factory=list)


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


@router.get("/clients/{client_id}", response_model=ClientDetailOut)
async def get_client(client_id: str, user: User = Depends(require_role("viewer")),
                     session: AsyncSession = Depends(get_session)) -> ClientDetailOut:
    client = await session.get(Client, client_id)
    if not client or client.org_id != user.org_id:
        raise not_found("Client")
    rows = (await session.execute(select(Project).where(Project.client_id == client.id, visible_projects(user))
                                  .order_by(Project.created_at.desc()))).scalars().all()
    if not rows and user.role != "admin":
        raise not_found("Client")  # nothing visible to this user
    latest = await _latest_runs(session, [p.id for p in rows])
    detail = ClientDetailOut(
        client=ClientOut(id=client.id, name=client.name, domain=client.domain, industry=client.industry,
                         project_count=len(rows), created_at=client.created_at),
        projects=[_out(p, client, latest) for p in rows],
    )
    if rows:
        names = {p.id: p.name for p in rows}
        execs = (await session.execute(
            select(AgentExecution, AnalysisRun).join(AnalysisRun, AnalysisRun.id == AgentExecution.run_id)
            .where(AnalysisRun.project_id.in_(names), AgentExecution.status == "completed",
                   AgentExecution.agent.in_(["client_research", "opportunity_prioritization"]))
            .order_by(AnalysisRun.created_at.desc()))).all()
        seen_projects: set[str] = set()
        for ex, run in execs:
            data = (ex.result or {}).get("data", {})
            if ex.agent == "client_research" and detail.profile is None and data.get("profile"):
                detail.profile, detail.profile_run_id = data["profile"], run.id
            elif ex.agent == "opportunity_prioritization" and run.project_id not in seen_projects:
                seen_projects.add(run.project_id)  # latest run per project only
                for rec in data.get("recommendations", [])[:3]:
                    detail.recommendations.append({
                        "project_id": run.project_id, "project": names[run.project_id], "run_id": run.id,
                        "feature": rec["feature"], "phase": rec["phase"], "complexity": rec["complexity"],
                        "score": rec["score"]["total"], "opportunity": rec["opportunity"]})
    return detail


@router.put("/projects/{project_id}/repositories", response_model=ProjectOut)
async def set_repositories(project_id: str, body: RepositoriesIn, request: Request,
                           user: User = Depends(require_role("analyst")),
                           session: AsyncSession = Depends(get_session)) -> ProjectOut:
    """Replace the GitHub/GitLab repositories analysed for a project (applies to future runs)."""
    p = await project_for(session, user, project_id)
    github: list[str] = []
    gitlab: list[str] = []
    invalid = []
    for url in body.urls:
        ref = parse_repo_url(url.strip())
        if ref is None:
            invalid.append(url)
        elif ref.url not in github + gitlab:
            (github if ref.provider == "github" else gitlab).append(ref.url)
    if invalid:
        raise HTTPException(422, f"Not a GitHub/GitLab repository URL: {invalid}")
    record = dict(p.record)
    sources = dict(record.get("sources") or {})
    before = sources.get("github", []) + sources.get("gitlab", [])
    sources["github"], sources["gitlab"] = github, gitlab
    record["sources"] = sources
    p.record = record  # reassign so the JSON column change is persisted
    audit.record(session, request, user, "project.repositories_changed", "project", p.id,
                 before=before, after=github + gitlab)
    await session.commit()
    c = await session.get(Client, p.client_id)
    return _out(p, c, await _latest_runs(session, [p.id]))


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
