"""Agent contract and shared run context."""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from cip.config import Settings, get_settings
from cip.connectors.research.search import NullSearchProvider, SearchProvider
from cip.connectors.research.web import WebFetcher
from cip.core.evidence import EvidenceLedger
from cip.core.llm import LLMClient, NullLLM
from cip.core.schemas import AgentResult, NormalizedRecord
from cip.core.scoring import ScoringConfig
from cip.core.taxonomy import Taxonomy, load_taxonomy
from cip.core.usage import UsageMeter

# (provider, host) -> decrypted access token or None. Resolved lazily so tokens
# are only held in memory for the duration of an API call and never land in
# agent results, evidence or prompts.
TokenResolver = Callable[[str, str], Awaitable[str | None]]


async def _no_token(provider: str, host: str) -> str | None:
    return None


@dataclass
class ApprovalRequest:
    gate: str
    title: str
    what: str
    why: str
    target: str
    data_analyzed: str


@dataclass
class RunContext:
    run_id: str
    project_id: str
    record: NormalizedRecord
    ledger: EvidenceLedger
    outputs: dict[str, AgentResult] = field(default_factory=dict)
    approvals: set[str] = field(default_factory=set)
    llm: LLMClient = field(default_factory=NullLLM)
    taxonomy: Taxonomy = field(default_factory=load_taxonomy)
    settings: Settings = field(default_factory=get_settings)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    fetcher: WebFetcher = field(default_factory=WebFetcher)
    search: SearchProvider = field(default_factory=NullSearchProvider)
    token_resolver: TokenResolver = _no_token
    # Injectable source-control factory (tests use fakes).
    source_control_factory: Callable[..., Any] | None = None
    # Injectable in-browser UX auditor (see connectors/research/ux.py); built from settings when None.
    ux_auditor: Any = None
    # The organization's approved knowledge-base records (see services/knowledge.py). Internal data:
    # used for deterministic matching only, never sent to the LLM or written to the evidence ledger.
    knowledge: list[dict] = field(default_factory=list)
    # Human-review overrides applied to this run version (services/review.py).
    review: list[dict] = field(default_factory=list)
    # LLM / web usage and budgets of this run (core/usage.py).
    usage: UsageMeter = field(default_factory=lambda: UsageMeter.from_settings(get_settings()))

    def data(self, agent: str) -> dict[str, Any]:
        result = self.outputs.get(agent)
        return result.data if result else {}


class AwaitingApproval(Exception):
    """Raised by an agent that discovers at runtime it needs human approval."""

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__(request.title)
        self.request = request


class Agent(abc.ABC):
    name: str
    description: str = ""
    # Hard dependencies: if any is failed/skipped this agent is skipped.
    requires: tuple[str, ...] = ()
    # Soft dependencies: must be finished (in any terminal state) first.
    after: tuple[str, ...] = ()
    max_attempts: int = 2

    def approval_needed(self, ctx: RunContext) -> ApprovalRequest | None:
        """Return an approval request if this agent must not run without one."""
        return None

    @abc.abstractmethod
    async def run(self, ctx: RunContext) -> AgentResult: ...
