"""Rate limiting for sign-in/registration attempts.

In-memory sliding window by default (correct for a single API process). When
``CIP_REDIS_URL`` is set, a Redis fixed-window counter is used instead so the
limit holds across all API processes.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from cip.config import get_settings


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def hit(self, key: str, limit: int, window_seconds: float) -> bool:
        """Record an attempt; return False if ``key`` exceeded ``limit`` within the window."""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


class RedisRateLimiter:
    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url)

    async def hit(self, key: str, limit: int, window_seconds: float) -> bool:
        k = f"cip:rl:{key}"
        async with self._redis.pipeline(transaction=True) as pipe:
            count, _ = await pipe.incr(k).expire(k, int(window_seconds), nx=True).execute()
        return int(count) <= limit

    async def aclose(self) -> None:
        await self._redis.aclose()


class RateLimiter:
    """Picks the backend lazily from settings (so tests and the CLI can change them)."""

    def __init__(self) -> None:
        self._memory = MemoryRateLimiter()
        self._redis: RedisRateLimiter | None = None

    async def hit(self, key: str, limit: int, window_seconds: float) -> bool:
        url = get_settings().redis_url
        if url:
            if self._redis is None:
                self._redis = RedisRateLimiter(url)
            return await self._redis.hit(key, limit, window_seconds)
        return await self._memory.hit(key, limit, window_seconds)

    def reset(self) -> None:
        self._memory.reset()

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


limiter = RateLimiter()
