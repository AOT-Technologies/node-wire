#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Rate limiting shared by every binding (REST, MCP, gRPC).

Two independent layers, both process-local and in-memory:

1. **Global token bucket** (``global_rate_limiter`` below) — always active
   unless ``NW_RATE_LIMIT_DISABLED=true``. A single bucket shared by every
   caller/tenant; its purpose is coarse DoS protection for the process as a
   whole, not fairness between callers.
   Configuration via environment variables:
     - NW_RATE_LIMIT_BURST: maximum number of tokens (default: 50)
     - NW_RATE_LIMIT_REFILL_RATE: tokens added per second (default: 10.0)

2. **Per-identity sliding-window limiter** (:class:`InMemoryRateLimiter`,
   accessed via :func:`get_per_identity_rate_limiter`) — opt-in, **off by
   default**. Unlike the global bucket, this one is keyed per caller (however
   a transport chooses to identify its callers), so one noisy/malicious
   identity can't exhaust capacity for every other caller sharing the global
   bucket (see M-2, 2026-09-01 security review). Originally REST-only; moved
   here so MCP and gRPC can opt into the same mechanism.
   Configuration via environment variables:
     - NW_RATE_LIMIT_PER_IDENTITY_ENABLED: opt in (default: false)
     - NW_RATE_LIMIT_PER_IDENTITY_MAX_REQUESTS: requests per window (default: 120)
     - NW_RATE_LIMIT_PER_IDENTITY_WINDOW_SECONDS: window size (default: 60)
     - NW_RATE_LIMIT_PER_IDENTITY_MAX_TRACKED_KEYS: LRU cap (default: 10000)
     - NW_RATE_LIMIT_PER_IDENTITY_KEY_TTL_SECONDS: idle eviction (default: 3600)
   The REST-only ``NW_REST_RATE_LIMIT_*`` variables are still honored as a
   deprecated alias for backward compatibility (see
   :func:`per_identity_rate_limit_enabled` / :func:`per_identity_rate_limit_config`);
   the canonical ``NW_RATE_LIMIT_PER_IDENTITY_*`` name wins if both are set.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from functools import lru_cache
from time import monotonic

from opentelemetry import metrics

logger = logging.getLogger("runtime.rate_limit")

_meter = metrics.get_meter("runtime")
_rate_limit_rejections = _meter.create_counter(
    "connector.rate_limit_rejections",
    unit="1",
    description="Requests rejected by the global token-bucket rate limiter",
)


class RateLimitExceeded(Exception):
    """Raised when the rate limit has been exceeded."""

    pass


class TokenBucket:
    def __init__(self, capacity: float, refill_rate: float) -> None:
        """
        :param capacity: Maximum number of tokens the bucket can hold.
        :param refill_rate: Number of tokens added to the bucket per second.
        """
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self.tokens = self.capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: int = 1) -> None:
        """
        Attempt to acquire `amount` tokens from the bucket.
        :raises RateLimitExceeded: if there are not enough tokens available.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill

            # Refill the bucket based on elapsed time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            if self.tokens >= amount:
                self.tokens -= amount
            else:
                _rate_limit_rejections.add(1)
                raise RateLimitExceeded("Global rate limit exceeded. Please try again later.")


# Global default instance configured via environment variables
burst = float(os.environ.get("NW_RATE_LIMIT_BURST", "50"))
rate = float(os.environ.get("NW_RATE_LIMIT_REFILL_RATE", "10.0"))

# Check if rate limiting is disabled for tests
if os.environ.get("NW_RATE_LIMIT_DISABLED", "false").lower() in ("0", "false", "no"):
    global_rate_limiter = TokenBucket(capacity=burst, refill_rate=rate)
else:
    global_rate_limiter = TokenBucket(capacity=float("inf"), refill_rate=float("inf"))


# --------------------------------------------------------------------------- #
# Per-identity sliding-window limiter (moved from bindings/rest_api/rate_limit.py
# so REST, MCP, and gRPC can all opt into it — see module docstring / M-2).
# --------------------------------------------------------------------------- #


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

    Intentionally simple for single-process deployments. Keys are bounded via
    LRU eviction and idle TTL to prevent unbounded memory growth.
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


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@lru_cache(maxsize=None)
def _warn_legacy_rest_rate_limit_flag_once() -> None:
    """Warn once per process; ``lru_cache`` suppresses repeats."""
    logger.warning(
        "NW_REST_RATE_LIMIT_ENABLED is deprecated; set "
        "NW_RATE_LIMIT_PER_IDENTITY_ENABLED=true instead (now applies "
        "to MCP and gRPC too, not just REST)."
    )


def per_identity_rate_limit_enabled() -> bool:
    """Whether the shared per-identity limiter is active.

    Off by default. ``NW_RATE_LIMIT_PER_IDENTITY_ENABLED`` is the canonical,
    transport-agnostic flag; the REST-only ``NW_REST_RATE_LIMIT_ENABLED`` is
    honored as a deprecated alias for backward compatibility (logged once)
    when the canonical flag is unset.
    """
    canonical = os.environ.get("NW_RATE_LIMIT_PER_IDENTITY_ENABLED")
    if canonical is not None:
        return _truthy(canonical)

    legacy = os.environ.get("NW_REST_RATE_LIMIT_ENABLED")
    if legacy is not None and _truthy(legacy):
        _warn_legacy_rest_rate_limit_flag_once()
        return True
    return False


def per_identity_rate_limit_config() -> tuple[int, int, int, int]:
    """(max_requests, window_seconds, max_tracked_keys, key_ttl_seconds).

    Reads the canonical ``NW_RATE_LIMIT_PER_IDENTITY_*`` variables, falling
    back to the legacy REST-only ``NW_REST_RATE_LIMIT_*`` names for anything
    not explicitly set under the canonical name.
    """

    def pick(canonical_name: str, legacy_name: str, default: int) -> int:
        if os.environ.get(canonical_name, "").strip():
            return _env_int(canonical_name, default)
        return _env_int(legacy_name, default)

    return (
        pick("NW_RATE_LIMIT_PER_IDENTITY_MAX_REQUESTS", "NW_REST_RATE_LIMIT_MAX_REQUESTS", 120),
        pick("NW_RATE_LIMIT_PER_IDENTITY_WINDOW_SECONDS", "NW_REST_RATE_LIMIT_WINDOW_SECONDS", 60),
        pick(
            "NW_RATE_LIMIT_PER_IDENTITY_MAX_TRACKED_KEYS",
            "NW_REST_RATE_LIMIT_MAX_TRACKED_KEYS",
            10_000,
        ),
        pick(
            "NW_RATE_LIMIT_PER_IDENTITY_KEY_TTL_SECONDS",
            "NW_REST_RATE_LIMIT_KEY_TTL_SECONDS",
            3600,
        ),
    )


_per_identity_limiter: InMemoryRateLimiter | None = None
# Mutable container (not a rebindable global) so the cache-invalidation check
# below is a dict-key read/write rather than a `global` reassignment whose
# value CodeQL's intra-procedural analysis can't see consumed on the next call.
_per_identity_limiter_state: dict[str, tuple[int, int, int, int] | None] = {"cfg": None}
_per_identity_limiter_lock = threading.Lock()


def get_per_identity_rate_limiter() -> InMemoryRateLimiter:
    """Process-wide per-identity limiter instance, shared by every transport.

    Re-created (state reset) whenever the resolved config changes — matches
    the REST binding's previous per-process caching behavior, now centralized
    so REST/MCP/gRPC share one limiter (and one set of tracked keys/memory
    bound) instead of each transport keeping its own.
    """
    global _per_identity_limiter
    cfg = per_identity_rate_limit_config()
    with _per_identity_limiter_lock:
        if _per_identity_limiter is None or _per_identity_limiter_state["cfg"] != cfg:
            max_requests, window_seconds, max_tracked_keys, key_ttl_seconds = cfg
            _per_identity_limiter = InMemoryRateLimiter(
                max_requests=max_requests,
                window_seconds=window_seconds,
                max_tracked_keys=max_tracked_keys,
                key_ttl_seconds=key_ttl_seconds,
            )
            _per_identity_limiter_state["cfg"] = cfg
        return _per_identity_limiter


def fingerprint_rate_limit_credential(token: str, *, salt: bytes) -> str:
    """Derive a non-reversible rate-limit key from a verified credential.

    Uses PBKDF2 with a caller-supplied per-process secret salt so a leaked key
    (logs, metrics) cannot be brute-forced offline back to the credential.
    Shared helper so every transport that keys its per-identity bucket off a
    raw bearer token/API key (rather than an already-non-secret principal;
    see :func:`identity_rate_limit_key`) uses the same hardening.
    """
    digest = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, 600_000).hex()[:16]
    return f"token:{digest}"


def identity_rate_limit_key(principal: str | None, *, fallback: str) -> str:
    """Derive a per-identity rate-limit key from an already-authenticated principal.

    ``principal`` (e.g. a JWT ``sub`` claim or API-key identifier) is not
    secret material — it identifies a caller but isn't a credential an
    attacker could replay — so a fast SHA-256 is sufficient here, unlike
    :func:`fingerprint_rate_limit_credential` which hashes the raw credential
    itself. Falls back to ``fallback`` (e.g. a client address) for
    unauthenticated callers/transports with no principal available.
    """
    if principal and principal != "unknown":
        digest = hashlib.sha256(principal.encode("utf-8")).hexdigest()[:16]
        return f"principal:{digest}"
    return f"addr:{fallback}"
