"""Celery worker for analysis runs (CIP_RUN_EXECUTOR=celery).

Start a worker with:

    celery -A cip.workers.celery_app worker --loglevel=info --concurrency=2

Each task executes one run (resuming from its persisted state) under a
distributed lock, so duplicate deliveries are harmless. ``acks_late`` +
``reject_on_worker_lost`` make Redis redeliver a task whose worker died; the
orchestrator then resumes from the database without repeating completed agents.
"""

from __future__ import annotations

import asyncio
import logging

from celery import Celery
from celery.signals import worker_ready

from cip.config import get_settings

log = logging.getLogger(__name__)
_settings = get_settings()

celery_app = Celery("cip", broker=_settings.redis_url or "redis://localhost:6379/0")
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": _settings.celery_visibility_timeout_seconds},
)


async def _execute(run_id: str) -> str:
    import redis.asyncio as aioredis

    from cip.db import session as db
    from cip.services.runlock import run_with_lock
    from cip.services.runner import runner

    s = get_settings()
    db.configure()  # fresh engine bound to this task's event loop
    client = aioredis.from_url(s.redis_url)
    try:
        return await run_with_lock(client, run_id, runner.execute, lock_ttl=s.run_lock_ttl_seconds)
    finally:
        await client.aclose()
        await db.dispose()


@celery_app.task(name="cip.execute_run", acks_late=True, reject_on_worker_lost=True)
def execute_run(run_id: str) -> str:
    status = asyncio.run(_execute(run_id))
    log.info("Run %s finished pass with status %s", run_id, status)
    return status


async def _resume_interrupted() -> list[str]:
    from cip.db import session as db
    from cip.services.runner import runner

    db.configure()
    try:
        return await runner.resume_interrupted()  # skips runs whose lock is held by a live worker
    finally:
        await db.dispose()


@worker_ready.connect
def resume_on_worker_start(**_kwargs) -> None:
    """A worker coming up re-queues runs left 'running' by a worker that died (its lock has expired)."""
    try:
        ids = asyncio.run(_resume_interrupted())
        if ids:
            log.info("Re-queued interrupted runs: %s", ids)
    except Exception:  # noqa: BLE001 - never block worker start-up
        log.exception("Could not resume interrupted runs")
