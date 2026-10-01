"""Distributed "one executor per run" coordination over Redis.

Guarantees, across any number of API processes and Celery workers:

* at most one process executes a given run at a time (Redis lock, renewed
  while executing, expiring if the holder dies);
* a start request that arrives while the run is executing (e.g. an approval
  granted mid-pass) is never lost: it leaves a re-run flag which the holder
  honours before releasing — and the requester re-tries the lock after
  setting the flag, which closes the race with a holder that is just
  releasing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


def lock_key(run_id: str) -> str:
    return f"cip:run-lock:{run_id}"


def rerun_key(run_id: str) -> str:
    return f"cip:run-rerun:{run_id}"


async def run_with_lock(redis, run_id: str, execute: Callable[[str], Awaitable[str]],
                        lock_ttl: int = 300, renew_every: float | None = None) -> str:
    """Execute ``run_id`` if no one else is; otherwise ask the current holder to re-run it."""
    lock = redis.lock(lock_key(run_id), timeout=lock_ttl, blocking=False)
    if not await lock.acquire():
        await redis.set(rerun_key(run_id), "1", ex=lock_ttl * 4)
        # The holder may have released between our attempt and setting the flag: try once more.
        if not await lock.acquire():
            return "busy"
    # The execution we are about to start satisfies any earlier pending request
    # (e.g. one left behind while a now-dead worker held the lock).
    await redis.delete(rerun_key(run_id))

    renew_every = renew_every or max(1.0, lock_ttl / 3)

    async def keep_alive() -> None:
        while True:
            await asyncio.sleep(renew_every)
            try:
                await lock.extend(lock_ttl, replace_ttl=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not renew lock for run %s: %s", run_id, exc)

    renewer = asyncio.create_task(keep_alive())
    status = "failed"
    try:
        while True:
            status = await execute(run_id)
            if not await redis.getdel(rerun_key(run_id)):
                break
            log.info("Run %s: re-running for a request that arrived mid-pass", run_id)
    finally:
        renewer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewer
        with contextlib.suppress(Exception):
            await lock.release()
    return status


async def is_locked(redis, run_id: str) -> bool:
    return bool(await redis.exists(lock_key(run_id)))
