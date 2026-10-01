"""Distributed run coordination (Redis lock + re-run flag) and Redis-backed rate limiting.

Uses a real redis-server when available (CI provides one); skipped otherwise.
"""

import asyncio
import os
import shutil
import socket
import subprocess
import time

import pytest

from cip.services.runlock import is_locked, lock_key, run_with_lock


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def redis_url():
    url = os.environ.get("CIP_TEST_REDIS_URL")
    if url:
        yield url
        return
    if not shutil.which("redis-server"):
        pytest.skip("redis-server not available")
    port = _free_port()
    proc = subprocess.Popen(["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"redis://127.0.0.1:{port}/0"
    proc.terminate()
    proc.wait()


@pytest.fixture
async def redis(redis_url):
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


async def test_only_one_executor_and_mid_pass_requests_rerun(redis):
    active = 0
    max_active = 0
    calls = 0

    async def execute(run_id):
        nonlocal active, max_active, calls
        calls += 1
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.2)
        active -= 1
        return "completed"

    first = asyncio.create_task(run_with_lock(redis, "run_a", execute, lock_ttl=5))
    await asyncio.sleep(0.05)
    assert await is_locked(redis, "run_a")
    # Two more start requests arrive while the first pass is executing (e.g. approvals granted).
    second = await run_with_lock(redis, "run_a", execute, lock_ttl=5)
    third = await run_with_lock(redis, "run_a", execute, lock_ttl=5)
    assert second == third == "busy"
    assert await first == "completed"
    assert max_active == 1          # never two executors at once
    assert calls == 2               # the queued requests collapse into exactly one re-run
    assert not await is_locked(redis, "run_a")


async def test_lock_is_renewed_during_long_runs_and_released_on_error(redis):
    async def slow(run_id):
        await asyncio.sleep(1.5)  # longer than the lock TTL
        return "completed"

    task = asyncio.create_task(run_with_lock(redis, "run_b", slow, lock_ttl=1, renew_every=0.3))
    await asyncio.sleep(1.2)
    assert await is_locked(redis, "run_b")  # kept alive past its 1s TTL
    assert await task == "completed"

    async def boom(run_id):
        raise RuntimeError("worker bug")

    with pytest.raises(RuntimeError):
        await run_with_lock(redis, "run_c", boom, lock_ttl=5)
    assert not await is_locked(redis, "run_c")


async def test_crashed_holder_lock_expires(redis):
    # Simulate a worker that died holding the lock: the key simply expires.
    await redis.set(lock_key("run_d"), "dead-worker", px=300)
    calls = []

    async def execute(run_id):
        calls.append(run_id)
        return "completed"

    assert await run_with_lock(redis, "run_d", execute, lock_ttl=5) == "busy"
    await asyncio.sleep(0.4)
    assert await run_with_lock(redis, "run_d", execute, lock_ttl=5) == "completed"
    assert calls == ["run_d"]


async def test_redis_rate_limiter_is_shared_across_processes(redis_url, monkeypatch):
    from cip.config import get_settings
    from cip.services.ratelimit import RateLimiter

    monkeypatch.setenv("CIP_REDIS_URL", redis_url)
    get_settings.cache_clear()
    try:
        api_process_1, api_process_2 = RateLimiter(), RateLimiter()  # separate in-memory state
        results = [await (api_process_1 if i % 2 else api_process_2).hit("auth:1.2.3.4", 5, 60) for i in range(7)]
        assert results == [True] * 5 + [False] * 2
        assert await api_process_1.hit("auth:5.6.7.8", 5, 60)  # other IPs unaffected
        await api_process_1.aclose()
        await api_process_2.aclose()
    finally:
        get_settings.cache_clear()


async def test_celery_mode_dispatches_to_workers(monkeypatch):
    from cip.config import get_settings
    from cip.services.runner import AnalysisRunner

    monkeypatch.setenv("CIP_RUN_EXECUTOR", "celery")
    monkeypatch.setenv("CIP_REDIS_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    try:
        from cip.workers import celery_app

        sent = []
        monkeypatch.setattr(celery_app.execute_run, "delay", lambda run_id: sent.append(run_id))
        r = AnalysisRunner()
        assert r.start("run_x") and r.start("run_x")
        assert sent == ["run_x", "run_x"] and not r.is_running("run_x")  # nothing executes in-process
    finally:
        get_settings.cache_clear()


async def test_api_starts_even_if_redis_is_down(monkeypatch, tmp_path):
    from cip.api.main import app, lifespan
    from cip.config import get_settings
    from cip.db import session as db

    monkeypatch.setenv("CIP_RUN_EXECUTOR", "celery")
    monkeypatch.setenv("CIP_REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listening
    monkeypatch.setenv("CIP_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
    get_settings.cache_clear()
    from cip.db.models import AnalysisRun, Client, Organization, Project

    db.configure()
    await db.create_all()
    async with db.sessionmaker()() as s:  # an interrupted run forces the start-up resume to query Redis
        org = Organization(name="o")
        s.add(org)
        await s.flush()
        cl = Client(org_id=org.id, key="c", name="c")
        s.add(cl)
        await s.flush()
        pr = Project(org_id=org.id, client_id=cl.id, name="p", record={})
        s.add(pr)
        await s.flush()
        s.add(AnalysisRun(org_id=org.id, project_id=pr.id, status="running"))
        await s.commit()
    await db.dispose()
    try:
        async with lifespan(app):
            pass  # reached: start-up survived the Redis outage
    finally:
        await db.dispose()
        get_settings.cache_clear()
