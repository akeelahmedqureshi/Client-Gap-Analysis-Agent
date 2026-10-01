"""Run execution: DB-backed RunStore, context rebuilding, background task registry."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from cip.agents.base import ApprovalRequest, RunContext
from cip.agents.orchestrator import Orchestrator
from cip.config import get_settings
from cip.connectors.research.search import get_search_provider
from cip.connectors.research.web import WebFetcher
from cip.core.evidence import EvidenceLedger
from cip.core.llm import get_llm
from cip.core.schemas import AgentResult, AgentStatus, Evidence, NormalizedRecord
from cip.core.scoring import ScoringConfig
from cip.core.security.crypto import TokenCipher
from cip.db import session as db
from cip.db.models import (
    AgentExecution,
    AnalysisRun,
    Approval,
    EvidenceRecord,
    Project,
    Report,
    SourceConnection,
)

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DbRunStore:
    def __init__(self, org_id: str) -> None:
        self.org_id = org_id
        self._saved: set[str] = set()
        self._lock = asyncio.Lock()  # serialize writes (SQLite-friendly)

    async def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        async with self._lock, db.sessionmaker()() as s:
            run = await s.get(AnalysisRun, run_id)
            run.status = status
            run.error = error
            await s.commit()

    async def set_agent_status(self, run_id: str, agent: str, status: AgentStatus,
                               result: AgentResult | None = None, error: str | None = None,
                               attempts: int | None = None) -> None:
        async with self._lock, db.sessionmaker()() as s:
            row = (await s.execute(select(AgentExecution).where(AgentExecution.run_id == run_id,
                                                                AgentExecution.agent == agent))).scalar_one_or_none()
            if row is None:
                row = AgentExecution(run_id=run_id, agent=agent)
                s.add(row)
            row.status = status.value
            if status == AgentStatus.RUNNING:
                row.started_at = _now()
                row.error = None
            if status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.SKIPPED):
                row.finished_at = _now()
            if result is not None:
                row.result = result.model_dump(mode="json")
                if result.errors:
                    row.error = "; ".join(result.errors)[:4000]
            if error is not None:
                row.error = error
            if attempts is not None:
                row.attempts = attempts
            if agent == "report" and status == AgentStatus.COMPLETED and result is not None:
                existing = (await s.execute(select(Report).where(Report.run_id == run_id))).scalar_one_or_none()
                report = existing or Report(run_id=run_id, org_id=self.org_id)
                report.title = result.data["report"]["title"]
                report.content = result.data["report"]
                report.markdown = result.data["markdown"]
                if not existing:
                    s.add(report)
            await s.commit()

    async def save_evidence(self, run_id: str, evidence: list[Evidence]) -> None:
        # Parallel agents share one ledger: decide what is new only while holding the lock.
        async with self._lock:
            new = [e for e in evidence if e.id not in self._saved]
            if not new:
                return
            await self._insert_evidence(run_id, new)
            self._saved.update(e.id for e in new)

    async def _insert_evidence(self, run_id: str, new: list[Evidence]) -> None:
        async with db.sessionmaker()() as s:
            for e in new:
                s.add(EvidenceRecord(id=e.id, run_id=run_id, org_id=self.org_id, claim=e.claim,
                                     source_url=e.source_url, source_type=e.source_type,
                                     extracted_text=e.extracted_text, repository_path=e.repository_path,
                                     line_range=e.line_range, confidence=e.confidence, collected_at=e.collected_at))
            await s.commit()

    async def request_approval(self, run_id: str, agent: str, request: ApprovalRequest) -> None:
        async with self._lock, db.sessionmaker()() as s:
            row = (await s.execute(select(Approval).where(Approval.run_id == run_id,
                                                          Approval.gate == request.gate))).scalar_one_or_none()
            if row is None:
                s.add(Approval(run_id=run_id, agent=agent, gate=request.gate, title=request.title, what=request.what,
                               why=request.why, target=request.target, data_analyzed=request.data_analyzed))
            elif row.status == "rejected":
                pass  # stays rejected; the agent will be skipped by the runner
            await s.commit()


def make_token_resolver(org_id: str):
    async def resolve(provider: str, host: str) -> str | None:
        async with db.sessionmaker()() as s:
            con = (await s.execute(select(SourceConnection).where(
                SourceConnection.org_id == org_id, SourceConnection.provider == provider,
                SourceConnection.host == host))).scalar_one_or_none()
            if not con:
                return None
            if con.expires_at and con.expires_at.replace(tzinfo=con.expires_at.tzinfo or timezone.utc) < _now():
                log.warning("Stored %s token for %s has expired", provider, host)
                return None
            return TokenCipher().decrypt(con.encrypted_token)
    return resolve


async def build_context(run: AnalysisRun, project: Project) -> tuple[RunContext, dict[str, AgentStatus], set[str]]:
    async with db.sessionmaker()() as s:
        execs = (await s.execute(select(AgentExecution).where(AgentExecution.run_id == run.id))).scalars().all()
        ev_rows = (await s.execute(select(EvidenceRecord).where(EvidenceRecord.run_id == run.id))).scalars().all()
        approvals = (await s.execute(select(Approval).where(Approval.run_id == run.id))).scalars().all()
    ledger = EvidenceLedger(Evidence(id=e.id, claim=e.claim, source_url=e.source_url, source_type=e.source_type,
                                     extracted_text=e.extracted_text, repository_path=e.repository_path,
                                     line_range=e.line_range, confidence=e.confidence, collected_at=e.collected_at)
                            for e in ev_rows)
    statuses: dict[str, AgentStatus] = {}
    outputs: dict[str, AgentResult] = {}
    for ex in execs:
        statuses[ex.agent] = AgentStatus(ex.status)
        if ex.status == AgentStatus.COMPLETED.value and ex.result:
            outputs[ex.agent] = AgentResult.model_validate(ex.result)
    granted = set(run.approved_gates or []) | {a.gate for a in approvals if a.status == "approved"}
    # A rejected gate skips its agent (and hard dependents) instead of blocking forever.
    rejected: set[str] = set()
    for a in approvals:
        if a.status == "rejected" and statuses.get(a.agent) in (None, AgentStatus.AWAITING_APPROVAL,
                                                                AgentStatus.PENDING):
            statuses[a.agent] = AgentStatus.SKIPPED
            rejected.add(a.agent)
    settings = get_settings()
    scoring = ScoringConfig()
    if run.scoring_weights:
        scoring.weights.update({k: float(v) for k, v in run.scoring_weights.items()})
    ctx = RunContext(
        run_id=run.id, project_id=project.id, record=NormalizedRecord.model_validate(project.record),
        ledger=ledger, outputs=outputs, approvals=granted, llm=get_llm(settings), settings=settings,
        scoring=scoring, fetcher=WebFetcher(), search=get_search_provider(settings),
        token_resolver=make_token_resolver(run.org_id),
    )
    return ctx, statuses, rejected


class AnalysisRunner:
    """Runs analyses in-process as asyncio tasks (swap for Celery/a workflow engine at scale)."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._rerun: set[str] = set()
        # Optional hook to customise the run context (e.g. inject connectors in tests).
        self.context_hook = None

    def is_running(self, run_id: str) -> bool:
        t = self._tasks.get(run_id)
        return bool(t and not t.done())

    def start(self, run_id: str) -> bool:
        """Start (or resume) a run. If it is already executing, re-run it once the current pass ends
        so approvals granted mid-pass are picked up."""
        if self.is_running(run_id):
            self._rerun.add(run_id)
            return False
        task = asyncio.create_task(self.execute(run_id), name=f"run:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._on_done(run_id))
        return True

    def _on_done(self, run_id: str) -> None:
        self._tasks.pop(run_id, None)
        if run_id in self._rerun:
            self._rerun.discard(run_id)
            self.start(run_id)

    async def wait(self, run_id: str) -> None:
        """Wait until the run (including any queued re-run) is idle. Used by tests and the CLI."""
        while self.is_running(run_id) or run_id in self._rerun:
            task = self._tasks.get(run_id)
            if task:
                await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

    async def resume_interrupted(self) -> list[str]:
        """Restart runs left 'queued'/'running' by a previous process (crash, deploy, restart).

        Completed agents are kept; agents caught mid-execution are re-run from scratch. Runs waiting
        for approval are left alone — they resume when a decision is made.
        """
        async with db.sessionmaker()() as s:
            runs = (await s.execute(select(AnalysisRun).where(
                AnalysisRun.status.in_(["queued", "running"])))).scalars().all()
            ids = [r.id for r in runs]
            if ids:
                for ex in (await s.execute(select(AgentExecution).where(
                        AgentExecution.run_id.in_(ids), AgentExecution.status == "running"))).scalars():
                    ex.status = "pending"
                    ex.error = "interrupted by restart; re-running"
                for r in runs:
                    r.status = "queued"
                await s.commit()
        for run_id in ids:
            log.info("Resuming interrupted run %s", run_id)
            self.start(run_id)
        return ids

    async def execute(self, run_id: str) -> str:
        async with db.sessionmaker()() as s:
            run = await s.get(AnalysisRun, run_id)
            project = await s.get(Project, run.project_id)
        try:
            ctx, statuses, rejected = await build_context(run, project)
            if self.context_hook:
                self.context_hook(ctx)
            store = DbRunStore(run.org_id)
            for name in rejected:
                await store.set_agent_status(run_id, name, AgentStatus.SKIPPED, error="approval rejected")
            store._saved.update(e.id for e in ctx.ledger.all())
            return await Orchestrator(store).run(ctx, statuses)
        except Exception as exc:  # noqa: BLE001
            log.exception("Run %s crashed", run_id)
            await DbRunStore(run.org_id).set_run_status(run_id, "failed", error=str(exc))
            return "failed"


runner = AnalysisRunner()
