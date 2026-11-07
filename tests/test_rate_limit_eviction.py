#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Idle-key eviction stops at the first live bucket; it must evict exactly what
the previous full scan evicted."""

from __future__ import annotations

import random

from node_wire_runtime import rate_limit as rl


def _reference_evict(limiter: rl.InMemoryRateLimiter, now: float) -> None:
    idle_cutoff = now - limiter._key_ttl_seconds
    stale = [k for k, b in limiter._buckets.items() if b.last_seen <= idle_cutoff]
    for k in stale:
        del limiter._buckets[k]


def test_eviction_matches_full_scan(monkeypatch) -> None:
    rng = random.Random(1234)
    clock = [0.0]
    monkeypatch.setattr(rl, "monotonic", lambda: clock[0])
    fast = rl.InMemoryRateLimiter(
        max_requests=3, window_seconds=5, max_tracked_keys=50, key_ttl_seconds=10
    )
    ref = rl.InMemoryRateLimiter(
        max_requests=3, window_seconds=5, max_tracked_keys=50, key_ttl_seconds=10
    )
    monkeypatch.setattr(ref, "_evict_idle_keys", lambda now: _reference_evict(ref, now))

    for _ in range(5000):
        clock[0] += rng.choice([0.0, 0.1, 0.5, 2.0, 15.0])
        key = f"k{rng.randrange(80)}"
        a = fast.consume(key)
        b = ref.consume(key)
        assert (a.allowed, a.retry_after_seconds) == (b.allowed, b.retry_after_seconds)
        assert list(fast._buckets) == list(ref._buckets)


def test_idle_keys_evicted_and_live_keys_kept(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(rl, "monotonic", lambda: clock[0])
    limiter = rl.InMemoryRateLimiter(max_requests=5, window_seconds=5, key_ttl_seconds=10)
    limiter.consume("old")
    clock[0] = 8.0
    limiter.consume("recent")
    clock[0] = 12.0
    limiter.consume("new")
    assert list(limiter._buckets) == ["recent", "new"]
