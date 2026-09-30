"""Relational model. Every tenant-owned row carries ``org_id`` for data isolation:

    Organization ─▶ Client ─▶ Project ─▶ AnalysisRun ─▶ AgentExecution / Evidence / Approval / Report
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=_id("usr"))
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="analyst")  # admin | analyst | viewer
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    client: Mapped[Client] = relationship(back_populates="projects")


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
