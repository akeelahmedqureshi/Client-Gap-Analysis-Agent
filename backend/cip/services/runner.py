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
from cip.db import session as db
from cip.db.models import (
    AgentExecution,
    AnalysisRun,
    Approval,
    EvidenceRecord,
    Project,
    Report,
    ReviewOverride,
)
from cip.services.knowledge import load_for_run
from cip.services.tokens import make_token_resolver

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

    async def control(self, run_id: str) -> str | None:
        """'cancelled' or 'paused' when a person asked the run to stop (see api/routes/runs.py)."""
        async with db.sessionmaker()() as s:
            status = (await s.execute(select(AnalysisRun.status).where(AnalysisRun.id == run_id))).scalar_one_or_none()
        return status if status in ("cancelled", "paused") else None

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


async def build_context(run: AnalysisRun, project: Project) -> tuple[RunContext, dict[str, AgentStatus], set[str]]:
    async with db.sessionmaker()() as s:
        execs = (await s.execute(select(AgentExecution).where(AgentExecution.run_id == run.id))).scalars().all()
        ev_rows = (await s.execute(select(EvidenceRecord).where(EvidenceRecord.run_id == run.id))).scalars().all()
        approvals = (await s.execute(select(Approval).where(Approval.run_id == run.id))).scalars().all()
        knowledge = await load_for_run(s, run.org_id)
        review = [{"kind": o.kind, "label": o.target_label, "target_id": o.target_id, "field": o.field,
                   "value": o.value, "note": o.note, "by": o.created_by_email} for o in (await s.execute(select(ReviewOverride).where(
                       ReviewOverride.run_id == run.id, ReviewOverride.status == "applied"))).scalars()]
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
        token_resolver=make_token_resolver(run.org_id), knowledge=knowledge, review=review,
    )
    # Usage of stages completed in an earlier pass counts toward this run's budgets.
    ctx.usage.preload({name: res.usage for name, res in outputs.items() if res.usage})
    return ctx, statuses, rejected


class AnalysisRunner:
    """Dispatches analysis runs.

    ``inline`` (default): runs execute as asyncio tasks inside this API process.
    ``celery``: runs are queued to Celery workers (see cip/workers/celery_app.py) and coordinated
    with a Redis lock, so any number of API processes and workers can be used.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._rerun: set[str] = set()
        self._slots: asyncio.Semaphore | None = None
        # Optional hook to customise the run context (e.g. inject connectors in tests).
        self.context_hook = None

    def cancel(self, run_id: str) -> None:
        """Stop an in-process run immediately (Celery runs stop at their next wave of agents)."""
        self._rerun.discard(run_id)
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()

    def is_running(self, run_id: str) -> bool:
        t = self._tasks.get(run_id)
        return bool(t and not t.done())

    @property
    def mode(self) -> str:
        return get_settings().run_executor

    def _redis(self):
        import redis.asyncio as aioredis

        return aioredis.from_url(get_settings().redis_url)

    async def is_active(self, run_id: str) -> bool:
        """Is the run executing anywhere (this process, or any worker in celery mode)?"""
        if self.mode != "celery":
            return self.is_running(run_id)
        from cip.services.runlock import is_locked

        client = self._redis()
        try:
            return await is_locked(client, run_id)
        finally:
            await client.aclose()

    def start(self, run_id: str) -> bool:
        """Start (or resume) a run. If it is already executing, re-run it once the current pass ends
        so approvals granted mid-pass are picked up."""
        if self.mode == "celery":
            from cip.workers.celery_app import execute_run

            execute_run.delay(run_id)  # the worker's run lock de-duplicates and handles re-runs
            return True
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
            # With Celery, runs still executing on a live worker hold their lock: leave those alone.
            runs = [r for r in runs if not await self.is_active(r.id)]
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
        # Inline mode: at most CIP_MAX_CONCURRENT_RUNS analyses at once; the rest wait as "queued".
        if self.mode != "celery":
            if self._slots is None:
                self._slots = asyncio.Semaphore(max(1, get_settings().max_concurrent_runs))
            async with self._slots:
                return await self._execute(run_id)
        return await self._execute(run_id)

    async def _execute(self, run_id: str) -> str:
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
            status = await Orchestrator(store).run(ctx, statuses)
        except Exception as exc:  # noqa: BLE001
            log.exception("Run %s crashed", run_id)
            await DbRunStore(run.org_id).set_run_status(run_id, "failed", error=str(exc))
            status = "failed"
        await self._after_pass(run_id, status)
        return status

    async def _after_pass(self, run_id: str, status: str) -> None:
        """Change detection, monitoring alerts and notifications; never allowed to fail the run itself."""
        from cip.services.monitoring import on_run_finished

        from cip.services.notifications import notify_run

        try:
            await on_run_finished(run_id, status)
        except Exception:  # noqa: BLE001
            log.exception("Post-run processing failed for %s", run_id)
        try:
            await notify_run(run_id, status)
        except Exception:  # noqa: BLE001
            log.exception("Run notifications failed for %s", run_id)


runner = AnalysisRunner()
