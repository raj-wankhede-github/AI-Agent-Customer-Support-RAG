"""Sliding-window rate limiting.

The in-memory backend is correct for a single process. With several API replicas each
replica enforces its own window; swap in a shared backend (e.g. Redis) implementing
`RateLimiterBackend` when running more than one replica behind a load balancer.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol

_WINDOWS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True)
class RateLimit:
    count: int
    window_seconds: int

    @classmethod
    def parse(cls, spec: str) -> RateLimit:
        count, _, unit = spec.strip().partition("/")
        if not count.isdigit() or unit not in _WINDOWS:
            raise ValueError(f"Invalid rate limit {spec!r}; expected e.g. '30/minute'")
        return cls(int(count), _WINDOWS[unit])


class RateLimiterBackend(Protocol):
    async def hit(self, key: str, limit: RateLimit) -> int | None:
        """Record a hit. Returns seconds to wait when the limit is exceeded, else None."""


class InMemoryRateLimiter:
    def __init__(self, max_keys: int = 50_000) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._max_keys = max_keys

    async def hit(self, key: str, limit: RateLimit) -> int | None:
        now = time.monotonic()
        async with self._lock:
            window = self._hits.setdefault(key, deque())
            while window and window[0] <= now - limit.window_seconds:
                window.popleft()
            if len(window) >= limit.count:
                return max(1, math.ceil(window[0] + limit.window_seconds - now))
            window.append(now)
            if len(self._hits) > self._max_keys:
                self._evict(now)
            return None

    def _evict(self, now: float) -> None:
        stale = [k for k, w in self._hits.items() if not w or w[-1] < now - 3600]
        for key in stale:
            del self._hits[key]
