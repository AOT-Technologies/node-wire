from __future__ import annotations

import math
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from time import monotonic


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


@dataclass
class _Bucket:
    timestamps: deque[float] = field(default_factory=deque)
    last_seen: float = 0.0


class InMemoryRateLimiter:
    """
    Sliding-window in-memory limiter.

    This is intentionally simple for single-process REST deployments.
    Keys are bounded via LRU eviction and idle TTL to prevent unbounded memory growth.
    """

    def __init__(
        self,
        *,
        max_requests: int,
        window_seconds: int,
        max_tracked_keys: int = 10_000,
        key_ttl_seconds: int = 3600,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be > 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if max_tracked_keys <= 0:
            raise ValueError("max_tracked_keys must be > 0")
        if key_ttl_seconds <= 0:
            raise ValueError("key_ttl_seconds must be > 0")
        self._max_requests = max_requests
        self._window_seconds = float(window_seconds)
        self._max_tracked_keys = max_tracked_keys
        self._key_ttl_seconds = float(key_ttl_seconds)
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def tracked_key_count(self) -> int:
        with self._lock:
            return len(self._buckets)

    def _prune_window(self, bucket: _Bucket, cutoff: float) -> None:
        while bucket.timestamps and bucket.timestamps[0] <= cutoff:
            bucket.timestamps.popleft()

    def _evict_idle_keys(self, now: float) -> None:
        idle_cutoff = now - self._key_ttl_seconds
        stale_keys = [
            key for key, bucket in self._buckets.items() if bucket.last_seen <= idle_cutoff
        ]
        for key in stale_keys:
            del self._buckets[key]

    def _evict_lru_keys(self) -> None:
        while len(self._buckets) > self._max_tracked_keys:
            self._buckets.popitem(last=False)

    def consume(self, key: str) -> RateLimitResult:
        now = monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            self._evict_idle_keys(now)

            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(last_seen=now)
                self._buckets[key] = bucket
            else:
                bucket.last_seen = now
                self._buckets.move_to_end(key)

            self._prune_window(bucket, cutoff)

            if len(bucket.timestamps) >= self._max_requests:
                retry_after = max(
                    1,
                    int(math.ceil((bucket.timestamps[0] + self._window_seconds) - now)),
                )
                return RateLimitResult(allowed=False, retry_after_seconds=retry_after)

            bucket.timestamps.append(now)
            self._evict_lru_keys()
            return RateLimitResult(allowed=True)
