"""In-memory sliding-window rate limiter.

Sufficient for the single-process deployment the platform currently requires
(see docs/deployment.md); swap for Redis when running multiple API processes.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window_seconds: float) -> bool:
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


limiter = RateLimiter()
