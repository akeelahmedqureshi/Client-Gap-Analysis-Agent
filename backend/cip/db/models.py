"""Relational model. Every tenant-owned row carries ``org_id`` for data isolation:

    Organization ─▶ Client ─▶ Project ─▶ AnalysisRun ─▶ AgentExecution / Evidence / Approval / Report
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, false, true
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _id(prefix: str):
    return lambda: f"{prefix}_{uuid.uuid4().hex[:16]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("org"))
    name: Mapped[str] = mapped_column(String(200))
    # Admin-managed governance settings (export permission, retention); see services/governance.py.
    settings: Mapped[dict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("usr"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="analyst")  # admin | analyst | viewer
    # BRS job function (sales | business_development | product | technical | management); tailors the UI
    # and may gate exports. Permissions still come from ``role``.
    job_function: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # {event: {"in_app": bool, "email": bool}}; missing entries use services/notifications.DEFAULTS.
    notification_prefs: Mapped[dict] = mapped_column(default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # Brute-force protection: consecutive failures and temporary lock.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bumped on password change/reset/deactivation; JWTs carrying an older version are rejected.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SourceConnection(Base):
    """An organization's GitHub/GitLab credential. The token is Fernet-encrypted at rest."""

    __tablename__ = "source_connections"
    __table_args__ = (UniqueConstraint("org_id", "provider", "host"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("con"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    host: Mapped[str] = mapped_column(String(200))
    token_type: Mapped[str] = mapped_column(String(20), default="oauth")  # oauth | pat
    encrypted_token: Mapped[str] = mapped_column(Text)
    # OAuth refresh token (encrypted); used to renew expiring access tokens automatically.
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[str] = mapped_column(String(500), default="")
    account_login: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(80), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CsvUpload(Base):
    __tablename__ = "csv_uploads"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("upl"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    storage_key: Mapped[str] = mapped_column(String(500))
    column_mapping: Mapped[dict] = mapped_column(default=dict)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(default=list)
    warnings: Mapped[list] = mapped_column(default=list)
    # Row number -> domain check (services: connectors/research/domain.py), from "Check domains" in the preview.
    domain_checks: Mapped[dict] = mapped_column(default=dict)
    uploaded_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("org_id", "key"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("cli"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(300))  # normalized domain or name, for de-duplication
    name: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str | None] = mapped_column(String(300), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    projects: Mapped[list[Project]] = relationship(back_populates="client")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("prj"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    upload_id: Mapped[str | None] = mapped_column(ForeignKey("csv_uploads.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    record: Mapped[dict] = mapped_column(default=dict)  # NormalizedRecord
    # Restricted projects are visible only to admins and listed members.
    restricted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    client: Mapped[Client] = relationship(back_populates="projects")


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    added_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    """Append-only record of security-relevant user actions."""

    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    user_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    details: Mapped[dict] = mapped_column(default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("run"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_gates: Mapped[list] = mapped_column(default=list)
    scoring_weights: Mapped[dict] = mapped_column(default=dict)
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Set when the run was started by a monitor's schedule (not by a person).
    monitor_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # What changed since the previous completed run of the project (see services/changes.py).
    baseline_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    changes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Partial re-run (a new version of an earlier run): which run it was derived from and what was re-run.
    parent_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rerun_stages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentExecution(Base):
    __tablename__ = "agent_executions"
    __table_args__ = (UniqueConstraint("run_id", "agent"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceRecord(Base):
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("run_id", "id"),)
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(String(40), index=True)
    claim: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(2000))
    source_type: Mapped[str] = mapped_column(String(30))
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    repository_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    line_range: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("run_id", "gate"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("apr"))
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(60))
    gate: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    what: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    data_analyzed: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    decided_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("rpt"))
    org_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[dict] = mapped_column(default=dict)
    markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Monitor(Base):
    """Scheduled re-analysis of a project, with change alerts.

    ``standing_approvals`` are approval gates granted in advance for scheduled runs by
    ``approved_by`` (re-checked on every run: the approver must still be an active analyst/admin
    who can see the project). The webhook URL is a credential and is stored encrypted.
    """

    __tablename__ = "monitors"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("mon"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    frequency: Mapped[str] = mapped_column(String(20), default="weekly")  # daily | weekly | monthly
    standing_approvals: Mapped[list] = mapped_column(default=list)
    approved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scoring_weights: Mapped[dict] = mapped_column(default=dict)
    min_severity: Mapped[str] = mapped_column(String(20), default="warning")  # for external notifications
    notify_emails: Mapped[list] = mapped_column(default=list)
    encrypted_webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optimistic claim token: a scheduler instance starts a due run only if it bumps this first.
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("run_id", "kind"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("alr"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    monitor_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))  # changes | approval_needed | run_failed
    severity: Mapped[str] = mapped_column(String(20))  # critical | warning | info
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    changes: Mapped[list] = mapped_column(default=list)
    notifications: Mapped[list] = mapped_column(default=list)  # delivery results per channel
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class KnowledgeRecord(Base):
    """Internal knowledge base: what the organization has delivered (BRS 7.18, 27; PRD 10.18, 10.47).

    ``kind``: capability | solution | project | case_study.
    ``status``: draft | in_review | approved | restricted | archived. ``visibility``: internal |
    client_facing. Only *approved* records are ever matched; only approved *client_facing* records may
    appear in client-facing output (outreach, report). Restricted records are visible to admins only.
    Every change bumps ``version`` and stores a snapshot of the new state in ``knowledge_record_versions``.
    """

    __tablename__ = "knowledge_records"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("kb"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[str] = mapped_column(Text, default="")
    outcomes: Mapped[str] = mapped_column(Text, default="")  # results / metrics delivered
    customer_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    industries: Mapped[list] = mapped_column(default=list)
    technologies: Mapped[list] = mapped_column(default=list)
    project_types: Mapped[list] = mapped_column(default=list)
    capability_tags: Mapped[list] = mapped_column(default=list)  # taxonomy feature/category ids or free text
    ai: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    automation: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    linked_ids: Mapped[list] = mapped_column(default=list)  # e.g. a solution's case studies
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    visibility: Mapped[str] = mapped_column(String(20), default="internal")
    # A case study may be named to clients only when this is set (and the record is client-facing).
    reference_allowed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class KnowledgeRecordVersion(Base):
    __tablename__ = "knowledge_record_versions"
    __table_args__ = (UniqueConstraint("record_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("knowledge_records.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(default=dict)
    change: Mapped[str] = mapped_column(String(40))  # created | edited | status:<new status>
    note: Mapped[str] = mapped_column(Text, default="")
    changed_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    changed_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SalesDocument(Base):
    """Human-reviewed sales output of a run: the sales summary or the outreach email (BRS 7.19-7.20).

    Created from the agent result the first time it is opened; people then edit, regenerate and approve
    it. ``generated`` keeps the agent's original; ``history`` keeps every version (who, when, what).
    """

    __tablename__ = "sales_documents"
    __table_args__ = (UniqueConstraint("run_id", "kind"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("sd"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # sales_summary | outreach
    content: Mapped[dict] = mapped_column(default=dict)
    generated: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    history: Mapped[list] = mapped_column(default=list)
    updated_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ReviewOverride(Base):
    """A reviewer's correction or decision on a finished run (BRS 17, PRD 10.34).

    ``kind``: capability_status | competitor | gap | recommendation. Pending overrides are applied by
    creating a new run version (services/review.py) whose reused results carry the corrections and whose
    downstream stages are re-run, so matrix, gaps, scores, sales output and report all agree.
    """

    __tablename__ = "review_overrides"
    __table_args__ = (UniqueConstraint("run_id", "kind", "target_id", "field"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("rvw"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(80))
    # Stable key that survives re-runs: capability id, competitor domain, gap name or recommendation name.
    target_label: Mapped[str] = mapped_column(String(300), default="")
    field: Mapped[str] = mapped_column(String(40), default="")
    value: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | applied
    applied_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Notification(Base):
    """A per-user, in-app notification about a run (BRS 30; PRD 10.43). Email copies are recorded in
    ``delivery``. Recipients must still be able to see the project when it is created."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "run_id", "event"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("ntf"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=True,
                                               index=True)
    event: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, default="")
    in_app: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    delivery: Mapped[list] = mapped_column(default=list)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ResearchCacheEntry(Base):
    """A fetched public page shared by the agents and runs of one organization (PRD 21).

    Keyed by organization and URL; ``fetched_at`` is when it was really fetched, so evidence taken from it
    keeps its true age. Expired entries are deleted by the scheduler.
    """

    __tablename__ = "research_cache"
    __table_args__ = (UniqueConstraint("org_id", "url_hash"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    url_hash: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(2000))
    final_url: Mapped[str] = mapped_column(String(2000))
    status: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))  # html | pdf
    title: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text)  # HTML (rendered if it was rendered) or extracted PDF text
    rendered: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
