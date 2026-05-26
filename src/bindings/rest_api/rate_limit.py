from __future__ import annotations

import math
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


class InMemoryRateLimiter:
    """
    Sliding-window in-memory limiter.

    This is intentionally simple for single-process REST deployments.
    """

    def __init__(self, *, max_requests: int, window_seconds: int) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._max_requests = max_requests
        self._window_seconds = float(window_seconds)
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def consume(self, key: str) -> RateLimitResult:
        now = monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_requests:
                retry_after = max(
                    1,
                    int(math.ceil((bucket[0] + self._window_seconds) - now)),
                )
                return RateLimitResult(allowed=False, retry_after_seconds=retry_after)
            bucket.append(now)
            return RateLimitResult(allowed=True)
