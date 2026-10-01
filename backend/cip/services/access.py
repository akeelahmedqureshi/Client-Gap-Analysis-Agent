"""Project-level authorization.

Within an organization, projects are visible to every member unless marked
``restricted``; restricted projects are visible only to admins and to users
listed in ``project_members``. Runs, evidence and reports inherit their
project's visibility. Anything not visible returns 404 (never 403) so its
existence is not revealed.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cip.api.deps import not_found
from cip.db.models import AnalysisRun, Project, ProjectMember, User


def is_admin(user: User) -> bool:
    return user.role == "admin"


def visible_projects(user: User) -> ColumnElement[bool]:
    """SQL condition selecting the projects ``user`` may see."""
    in_org = Project.org_id == user.org_id
    if is_admin(user):
        return in_org
    member_of = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
    return and_(in_org, or_(Project.restricted.is_(False), Project.id.in_(member_of)))


async def project_for(session: AsyncSession, user: User, project_id: str) -> Project:
    project = (await session.execute(select(Project).where(Project.id == project_id, visible_projects(user)))) \
        .scalar_one_or_none()
    if project is None:
        raise not_found("Project")
    return project


async def run_for(session: AsyncSession, user: User, run_id: str) -> AnalysisRun:
    run = (await session.execute(
        select(AnalysisRun).join(Project, Project.id == AnalysisRun.project_id)
        .where(AnalysisRun.id == run_id, visible_projects(user)))).scalar_one_or_none()
    if run is None:
        raise not_found("Run")
    return run
