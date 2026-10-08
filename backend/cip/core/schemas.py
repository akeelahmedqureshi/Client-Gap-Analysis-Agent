"""Structured data contracts shared by every agent.

Agents never exchange free-form text internally: everything that crosses an
agent boundary is one of these models. Each finding distinguishes whether it is
backed by collected evidence, inferred from evidence, or an AI estimate.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


SourceType = Literal[
    "website",
    "linkedin",
    "github",
    "gitlab",
    "documentation",
    "social",
    "marketplace",
    "search",
    "csv",
    "advisory",  # public vulnerability databases (OSV.dev)
    "app_store",  # Apple App Store / Google Play listings and reviews
]


class Basis(str, Enum):
    """How a claim is supported."""

    EVIDENCE = "evidence"  # directly observed in a source
    INFERRED = "inferred"  # deterministic inference from evidence
    ESTIMATE = "estimate"  # AI-generated estimate / hypothesis


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ev"))
    claim: str
    source_url: str
    source_type: SourceType
    extracted_text: str | None = None
    repository_path: str | None = None
    line_range: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    collected_at: datetime = Field(default_factory=utcnow)


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fd"))
    category: str
    title: str
    detail: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    basis: Basis = Basis.EVIDENCE


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"


class AgentResult(BaseModel):
    """The only shape an agent may return."""

    status: AgentStatus = AgentStatus.COMPLETED
    findings: list[Finding] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    next_actions: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# CSV intake
# --------------------------------------------------------------------------


class ClientRecord(BaseModel):
    name: str
    domain: str | None = None
    email: str | None = None
    industry: str | None = None


class ProjectRecord(BaseModel):
    name: str
    url: str | None = None
    description: str | None = None
    technology: list[str] = Field(default_factory=list)
    status: str | None = None
    start_date: str | None = None
    existing_features: list[str] = Field(default_factory=list)
    notes: str | None = None


class SourceLinks(BaseModel):
    github: list[str] = Field(default_factory=list)
    gitlab: list[str] = Field(default_factory=list)
    linkedin: list[str] = Field(default_factory=list)
    social: list[str] = Field(default_factory=list)
    websites: list[str] = Field(default_factory=list)


class NormalizedRecord(BaseModel):
    row_number: int
    client: ClientRecord
    project: ProjectRecord
    sources: SourceLinks = Field(default_factory=SourceLinks)
    issues: list[str] = Field(default_factory=list)
    duplicate_of_row: int | None = None


# --------------------------------------------------------------------------
# Research outputs
# --------------------------------------------------------------------------


class Contact(BaseModel):
    type: Literal[
        "email", "phone", "address", "contact_page", "linkedin", "twitter", "facebook",
        "instagram", "youtube", "github", "gitlab", "docs", "developer_portal",
        "community", "app_store", "google_play",
    ]
    value: str
    source: str
    confidence: float = Field(ge=0.0, le=1.0)


class ProductDiscovery(BaseModel):
    name: str
    kind: Literal["product", "service", "platform", "mobile_app", "saas", "api", "marketplace", "other"] = "product"
    description: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    source_url: str | None = None
    source_type: SourceType = "website"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CompanyProfile(BaseModel):
    name: str
    domain: str | None = None
    description: str | None = None
    industry: str | None = None
    headquarters: str | None = None
    locations: list[str] = Field(default_factory=list)
    founded_year: int | None = None
    company_size: str | None = None
    business_model: str | None = None
    legal_name: str | None = None
    revenue_model: str | None = None
    target_customers: list[str] = Field(default_factory=list)
    geographic_markets: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    subsidiaries: list[str] = Field(default_factory=list)
    divisions: list[str] = Field(default_factory=list)
    products: list[ProductDiscovery] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class TechSignal(BaseModel):
    category: Literal[
        "language", "frontend", "backend", "database", "cloud", "infrastructure", "ci_cd",
        "auth", "payments", "ai", "testing", "observability", "messaging", "mobile",
        "search", "security", "integration", "other",
    ]
    name: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class RepositoryProfile(BaseModel):
    provider: Literal["github", "gitlab"]
    url: str
    full_name: str
    default_branch: str | None = None
    private: bool | None = None
    description: str | None = None
    languages: dict[str, int] = Field(default_factory=dict)
    file_count: int = 0
    architecture: list[str] = Field(default_factory=list)
    technologies: list[TechSignal] = Field(default_factory=list)
    has_tests: bool = False
    has_ci: bool = False
    has_docker: bool = False
    has_iac: bool = False
    technical_debt_indicators: list[str] = Field(default_factory=list)
    skipped_sensitive_files: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Features, competitors, comparison
# --------------------------------------------------------------------------


class FeatureStatus(str, Enum):
    """Public-evidence state of a capability (BRS 7.9).

    UNKNOWN is "Not publicly identified": no reliable public evidence was found. It is never proof of
    absence. MISSING is "Not available / confirmed missing" and needs positive evidence of absence (for
    example a reviewer's override); it is never inferred from silence.
    """

    AVAILABLE = "available"
    PARTIAL = "partial"
    MISSING = "missing"
    UNKNOWN = "unknown"


STATUS_LABELS = {
    "available": "available",
    "partial": "partially available",
    "unknown": "not publicly identified",
    "missing": "confirmed missing",
}


class FeatureObservation(BaseModel):
    feature_id: str
    status: FeatureStatus
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: str = ""
    basis: Basis = Basis.EVIDENCE


CompetitorClass = Literal["direct", "indirect", "adjacent", "open_source", "enterprise", "emerging"]


class Competitor(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cmp"))
    name: str
    url: str | None = None
    classification: CompetitorClass = "direct"
    description: str = ""
    target_market: str | None = None
    pricing: str | None = None
    pricing_profile: dict | None = None  # structured plans/prices (see connectors/research/pricing.py)
    verified: bool = False
    rationale: str = ""
    features: list[FeatureObservation] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ComparisonRow(BaseModel):
    feature_id: str
    feature_name: str
    category: str
    client: FeatureStatus
    competitors: dict[str, FeatureStatus]  # competitor id -> status
    competitor_coverage: float  # share of compared competitors that have it


class GapType(str, Enum):
    MISSING = "missing"
    PARTIAL = "partial"
    TECHNOLOGY = "technology"
    UX = "ux"
    AI = "ai"
    PRICING = "pricing"
    SECURITY = "security"
    PROCESS = "process"  # cost-reduction / automation opportunity in a business process (BRS 7.12)


class Gap(BaseModel):
    id: str = Field(default_factory=lambda: new_id("gap"))
    feature_id: str | None = None
    name: str
    category: str
    gap_type: GapType
    description: str
    competitors_with: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    basis: Basis = Basis.INFERRED
    process_id: str | None = None  # set for process gaps (see agents/business_process.py)


class Opportunity(BaseModel):
    gap_id: str
    name: str
    business_opportunity: str
    potential_users: str = ""
    revenue_opportunity: str = ""
    # factor name -> score (0..5). Factors are defined by the scoring config.
    factors: dict[str, float]
    basis: Basis = Basis.ESTIMATE
    priority: Literal["high", "medium", "low"] = "medium"
    business_category: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    total: float
    contributions: dict[str, float]
    weights: dict[str, float]
    # Set when evidence confidence scaled the score (see core/scoring.py).
    raw_total: float | None = None
    confidence: float | None = None
    confidence_factor: float | None = None


Phase = Literal["phase_1_quick_wins", "phase_2_growth", "phase_3_major", "phase_4_strategic"]


class Recommendation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rec"))
    gap_id: str
    feature: str
    phase: Phase
    problem: str
    opportunity: str
    business_impact: str
    user_impact: str
    technical_approach: str
    dependencies: list[str] = Field(default_factory=list)
    complexity: Literal["low", "medium", "high", "very_high"]
    expected_outcome: str
    evidence_ids: list[str] = Field(default_factory=list)
    score: ScoreBreakdown
    basis: Basis = Basis.ESTIMATE
    priority: Literal["high", "medium", "low"] = "medium"
    business_category: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ImplementationPlan(BaseModel):
    recommendation_id: str
    feature: str
    objective: str
    architecture_impact: str
    frontend_changes: list[str] = Field(default_factory=list)
    backend_changes: list[str] = Field(default_factory=list)
    database_changes: list[str] = Field(default_factory=list)
    api_changes: list[str] = Field(default_factory=list)
    ai_changes: list[str] = Field(default_factory=list)
    infrastructure_changes: list[str] = Field(default_factory=list)
    security_changes: list[str] = Field(default_factory=list)
    testing_requirements: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    migration_requirements: list[str] = Field(default_factory=list)
    estimated_effort: str = ""
    recommended_team: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    basis: Basis = Basis.ESTIMATE
