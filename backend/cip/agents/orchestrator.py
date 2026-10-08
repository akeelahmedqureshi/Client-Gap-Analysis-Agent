"""Agent supervisor.

Agents never call each other. The orchestrator owns the dependency graph,
runs ready agents in parallel, enforces human-approval gates, retries failures
and persists every state transition through a ``RunStore`` so long-running
analyses are resumable (completed agents are never re-run).

    csv_intake
        ├── client_research ─────────┐
        └── repository ─ code_analysis ┤
                                      product_features
                                           │
                                  competitor_research
                                           │
                                  feature_comparison ─ gap_analysis ─ opportunity_prioritization
                                                                              │
                                                              enhancement_planning ─ report
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timezone
from typing import Protocol

from cip.agents.app_store import AppStoreAgent
from cip.agents.base import Agent, ApprovalRequest, AwaitingApproval, RunContext
from cip.agents.business_process import BusinessProcessAgent
from cip.agents.capability_matching import CapabilityMatchingAgent
from cip.agents.client_research import ClientResearchAgent
from cip.agents.code_analysis import CodeAnalysisAgent
from cip.agents.comparison import FeatureComparisonAgent
from cip.agents.competitor_research import CompetitorResearchAgent
from cip.agents.csv_intake import CsvIntakeAgent
from cip.agents.gap_analysis import GapAnalysisAgent
from cip.agents.industry_market import IndustryMarketAgent
from cip.agents.planning import EnhancementPlanningAgent
from cip.agents.pricing_analysis import PricingAnalysisAgent
from cip.agents.prioritization import PrioritizationAgent
from cip.agents.product_features import ProductFeatureAgent
from cip.agents.quality import QualityAssuranceAgent
from cip.agents.reporting import ReportAgent
from cip.agents.repository import RepositoryAgent
from cip.agents.sales import OutreachAgent, SalesIntelligenceAgent
from cip.agents.security_review import SecurityReviewAgent
from cip.agents.ux_review import UxReviewAgent
from cip.connectors.research.search import MeteredSearch
from cip.core import review, usage
from cip.core.schemas import AgentResult, AgentStatus, Evidence

log = logging.getLogger(__name__)

TERMINAL = {AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.SKIPPED}
SATISFIED = {AgentStatus.COMPLETED}


def default_agents() -> list[Agent]:
    return [
        CsvIntakeAgent(),
        ClientResearchAgent(),
        RepositoryAgent(),
        CodeAnalysisAgent(),
        SecurityReviewAgent(),
        ProductFeatureAgent(),
        IndustryMarketAgent(),
        CompetitorResearchAgent(),
        PricingAnalysisAgent(),
        AppStoreAgent(),
        UxReviewAgent(),
        BusinessProcessAgent(),
        FeatureComparisonAgent(),
        GapAnalysisAgent(),
        PrioritizationAgent(),
        EnhancementPlanningAgent(),
        CapabilityMatchingAgent(),
        SalesIntelligenceAgent(),
        OutreachAgent(),
        QualityAssuranceAgent(),
        ReportAgent(),
    ]


class RunStore(Protocol):
    """Persistence port for run state (database implementation in ``cip.db.store``)."""

    async def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None: ...
    async def set_agent_status(self, run_id: str, agent: str, status: AgentStatus,
                               result: AgentResult | None = None, error: str | None = None,
                               attempts: int | None = None) -> None: ...
    async def save_evidence(self, run_id: str, evidence: list[Evidence]) -> None: ...
    async def request_approval(self, run_id: str, agent: str, request: ApprovalRequest) -> None: ...


class Orchestrator:
    def __init__(self, store: RunStore, agents: list[Agent] | None = None, retry_delay: float = 2.0) -> None:
        self.store = store
        self.retry_delay = retry_delay
        self.agents = {a.name: a for a in (agents or default_agents())}
        self._validate_graph()

    def _validate_graph(self) -> None:
        for a in self.agents.values():
            for dep in (*a.requires, *a.after):
                if dep not in self.agents:
                    raise ValueError(f"Agent {a.name} depends on unknown agent {dep}")
        # cycle check via DFS
        state: dict[str, int] = {}

        def visit(n: str) -> None:
            if state.get(n) == 1:
                raise ValueError(f"Dependency cycle at {n}")
            if state.get(n) == 2:
                return
            state[n] = 1
            a = self.agents[n]
            for d in (*a.requires, *a.after):
                visit(d)
            state[n] = 2

        for n in self.agents:
            visit(n)

    async def run(self, ctx: RunContext, statuses: dict[str, AgentStatus]) -> str:
        """Advance the run as far as possible. Returns the resulting run status.

        ``statuses`` holds the persisted status of each agent (missing = pending);
        ``ctx.outputs`` must already contain results of completed agents.
        """
        if not isinstance(ctx.search, MeteredSearch):
            ctx.search = MeteredSearch(ctx.search)
        token = usage.activate(ctx.usage)
        try:
            return await self._run(ctx, statuses)
        finally:
            usage.deactivate(token)

    async def _run(self, ctx: RunContext, statuses: dict[str, AgentStatus]) -> str:
        for name in self.agents:
            statuses.setdefault(name, AgentStatus.PENDING)
            if statuses[name] in (AgentStatus.RUNNING, AgentStatus.AWAITING_APPROVAL):
                statuses[name] = AgentStatus.PENDING  # resumed after a crash or an approval
        if control := await self._control(ctx.run_id):
            return await self._stop(ctx, statuses, control)
        await self.store.set_run_status(ctx.run_id, "running")

        while True:
            # Cancel / pause requests are honoured between waves: running agents finish their step first.
            if control := await self._control(ctx.run_id):
                return await self._stop(ctx, statuses, control)
            ready: list[Agent] = []
            blocked_on_approval = False
            for name, agent in self.agents.items():
                if statuses[name] != AgentStatus.PENDING:
                    continue
                hard = [statuses[d] for d in agent.requires]
                soft = [statuses[d] for d in agent.after]
                if any(s in (AgentStatus.FAILED, AgentStatus.SKIPPED) for s in hard):
                    statuses[name] = AgentStatus.SKIPPED
                    await self.store.set_agent_status(ctx.run_id, name, AgentStatus.SKIPPED,
                                                      error="upstream dependency did not complete")
                    continue
                if not all(s in SATISFIED for s in hard) or not all(s in TERMINAL for s in soft):
                    if any(s == AgentStatus.AWAITING_APPROVAL for s in hard + soft):
                        blocked_on_approval = True
                    continue
                request = agent.approval_needed(ctx)
                if request:
                    statuses[name] = AgentStatus.AWAITING_APPROVAL
                    await self.store.set_agent_status(ctx.run_id, name, AgentStatus.AWAITING_APPROVAL)
                    await self.store.request_approval(ctx.run_id, name, request)
                    blocked_on_approval = True
                    continue
                ready.append(agent)

            if not ready:
                if any(s == AgentStatus.AWAITING_APPROVAL for s in statuses.values()) or blocked_on_approval:
                    await self.store.set_run_status(ctx.run_id, "awaiting_approval")
                    return "awaiting_approval"
                if any(s == AgentStatus.PENDING for s in statuses.values()):
                    # Remaining agents wait on something that can no longer happen.
                    for n, s in statuses.items():
                        if s == AgentStatus.PENDING:
                            statuses[n] = AgentStatus.SKIPPED
                            await self.store.set_agent_status(ctx.run_id, n, AgentStatus.SKIPPED,
                                                              error="unreachable")
                failed = [n for n, s in statuses.items() if s == AgentStatus.FAILED]
                status = "completed_with_errors" if failed else "completed"
                await self.store.set_run_status(ctx.run_id, status,
                                                error=f"failed agents: {', '.join(failed)}" if failed else None)
                return status

            await asyncio.gather(*(self._execute(ctx, a, statuses) for a in ready))

    async def _control(self, run_id: str) -> str | None:
        check = getattr(self.store, "control", None)
        return await check(run_id) if check else None

    async def _stop(self, ctx: RunContext, statuses: dict[str, AgentStatus], control: str) -> str:
        if control == "cancelled":
            for n, s in statuses.items():
                if s in (AgentStatus.PENDING, AgentStatus.AWAITING_APPROVAL):
                    statuses[n] = AgentStatus.SKIPPED
                    await self.store.set_agent_status(ctx.run_id, n, AgentStatus.SKIPPED, error="run cancelled")
        return control  # the run keeps its cancelled / paused status

    async def _execute(self, ctx: RunContext, agent: Agent, statuses: dict[str, AgentStatus]) -> None:
        statuses[agent.name] = AgentStatus.RUNNING
        await self.store.set_agent_status(ctx.run_id, agent.name, AgentStatus.RUNNING)
        token = usage.set_agent(agent.name)
        try:
            await self._attempts(ctx, agent, statuses)
        finally:
            usage.reset_agent(token)

    async def _attempts(self, ctx: RunContext, agent: Agent, statuses: dict[str, AgentStatus]) -> None:
        last_error = ""
        for attempt in range(1, agent.max_attempts + 1):
            try:
                before = {e.id for e in ctx.ledger.all()}
                result = await agent.run(ctx)
                if not isinstance(result, AgentResult):  # contract enforcement
                    raise TypeError(f"{agent.name} returned {type(result).__name__}, expected AgentResult")
                status = result.status if result.status in TERMINAL else AgentStatus.COMPLETED
                result.status = status
                result.usage = ctx.usage.agent_usage(agent.name)
                # Normalize to plain JSON types so fresh and resumed runs see identical data; reviewers'
                # overrides of this run (core/review.py) are applied to every agent's output.
                dumped = result.model_dump(mode="json")
                result = AgentResult.model_validate(review.patch(agent.name, dumped, ctx.review) if ctx.review
                                                    else dumped)
                # Only evidence that exists in the ledger may be referenced.
                for f in result.findings:
                    f.evidence_ids = ctx.ledger.validate_refs(f.evidence_ids)
                # Evidence read from a cached page is dated when the page was fetched, never "now" (PRD 21).
                if getattr(ctx.fetcher, "cached_at", None):
                    for ev in ctx.ledger.all():
                        if ev.id in before:
                            continue
                        when = ctx.fetcher.cache_date(ev.source_url, agent.name)
                        collected = ev.collected_at if ev.collected_at.tzinfo else \
                            ev.collected_at.replace(tzinfo=timezone.utc)
                        if when is not None and when < collected:
                            ev.collected_at = when
                ctx.outputs[agent.name] = result
                await self.store.save_evidence(ctx.run_id, ctx.ledger.all())
                await self.store.set_agent_status(ctx.run_id, agent.name, status, result=result, attempts=attempt)
                statuses[agent.name] = status
                return
            except AwaitingApproval as pending:
                statuses[agent.name] = AgentStatus.AWAITING_APPROVAL
                await self.store.set_agent_status(ctx.run_id, agent.name, AgentStatus.AWAITING_APPROVAL,
                                                  attempts=attempt)
                await self.store.request_approval(ctx.run_id, agent.name, pending.request)
                return
            except Exception as exc:  # noqa: BLE001 - agent failures must not crash the run
                last_error = f"{type(exc).__name__}: {exc}"
                log.exception("Agent %s failed (attempt %s/%s)", agent.name, attempt, agent.max_attempts)
                if attempt < agent.max_attempts:
                    await asyncio.sleep(min(self.retry_delay * 2 ** (attempt - 1), 30))
        statuses[agent.name] = AgentStatus.FAILED
        await self.store.set_agent_status(ctx.run_id, agent.name, AgentStatus.FAILED, error=last_error,
                                          attempts=agent.max_attempts)
