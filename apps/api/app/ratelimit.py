"""A tiny in-memory fixed-window rate limiter.

Good enough for a single-process free-tier deployment (one Render instance). It is per process,
so with more instances the effective limit multiplies; a shared store (Redis) would replace this
at scale — see docs/adr and the scale-out design.
"""

import threading
import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[float, int]] = {}  # key -> (window_start, count)

    def allow(self, key: str) -> bool:
        """Count one attempt for `key`; False once it has used up its window."""
        now = self._clock()
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= self._window:
                start, count = now, 0
            if count >= self._limit:
                self._hits[key] = (start, count)
                return False
            self._hits[key] = (start, count + 1)
            if len(self._hits) > self._max_keys:  # bound memory under a key-flooding attack
                self._evict(now)
            return True

    def retry_after(self, key: str) -> int:
        now = self._clock()
        with self._lock:
            start, _ = self._hits.get(key, (now, 0))
            return max(1, int(self._window - (now - start)) + 1)

    def _evict(self, now: float) -> None:
        live = {k: v for k, v in self._hits.items() if now - v[0] < self._window}
        if len(live) > self._max_keys:
            # A flood of distinct keys inside one window: expiry frees nothing, so keep only the
            # newest half rather than let memory grow without bound.
            newest = sorted(live.items(), key=lambda kv: kv[1][0], reverse=True)
            live = dict(newest[: self._max_keys // 2])
        self._hits = live
