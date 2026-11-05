#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""The outbound SSRF check reuses a passed DNS check for NW_REST_DNS_CACHE_SECONDS.

Only passes are cached; blocked or unresolvable hosts are resolved on every call,
and the hostname blocklist and egress allowlist are checked before the cache.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from node_wire_runtime import http_safety as hs
from node_wire_runtime.http_safety import SsrfBlockedError, assert_safe_destination


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fake resolver: set ``state["ip"]``; ``state["calls"]`` counts lookups."""
    state: dict[str, Any] = {"ip": "93.184.216.34", "calls": 0}

    async def fake_getaddrinfo(host: str, port: int, **kwargs: Any) -> list:
        state["calls"] += 1
        return [(None, None, None, None, (state["ip"], port))]

    monkeypatch.setattr(
        "node_wire_runtime.http_safety.asyncio.get_event_loop",
        lambda: MagicMock(getaddrinfo=AsyncMock(side_effect=fake_getaddrinfo)),
    )
    return state


async def test_passed_check_is_reused_within_ttl(resolver: dict[str, Any]) -> None:
    for _ in range(3):
        await assert_safe_destination("https://api.example.com/a")
    assert resolver["calls"] == 1
    await assert_safe_destination("https://api.example.com:8443/a")  # other port: own entry
    assert resolver["calls"] == 2


async def test_failed_check_is_not_cached(resolver: dict[str, Any]) -> None:
    resolver["ip"] = "10.0.0.5"
    for _ in range(2):
        with pytest.raises(SsrfBlockedError):
            await assert_safe_destination("https://api.example.com/a")
    assert resolver["calls"] == 2
    resolver["ip"] = "93.184.216.34"
    await assert_safe_destination("https://api.example.com/a")
    assert resolver["calls"] == 3


async def test_entry_expires_and_host_is_resolved_again(
    resolver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(hs.time, "monotonic", lambda: now[0])
    monkeypatch.setenv("NW_REST_DNS_CACHE_SECONDS", "30")
    await assert_safe_destination("https://api.example.com/a")
    now[0] += 29
    await assert_safe_destination("https://api.example.com/a")
    assert resolver["calls"] == 1
    now[0] += 2
    resolver["ip"] = "169.254.169.254"  # rebound to metadata after expiry: caught
    with pytest.raises(SsrfBlockedError):
        await assert_safe_destination("https://api.example.com/a")


async def test_zero_ttl_resolves_every_call(
    resolver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NW_REST_DNS_CACHE_SECONDS", "0")
    for _ in range(3):
        await assert_safe_destination("https://api.example.com/a")
    assert resolver["calls"] == 3


async def test_allowlist_is_enforced_before_the_cache(
    resolver: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await assert_safe_destination("https://api.example.com/a")
    monkeypatch.setenv("NW_REST_ALLOWED_HOSTS", "other.example.com")
    with pytest.raises(SsrfBlockedError, match="allowlist"):
        await assert_safe_destination("https://api.example.com/a")


async def test_cache_is_bounded(resolver: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hs, "_DNS_CACHE_MAX_ENTRIES", 3)
    for i in range(5):
        await assert_safe_destination(f"https://h{i}.example.com/a")
    assert list(hs._dns_checked_ok) == [
        ("h2.example.com", 443),
        ("h3.example.com", 443),
        ("h4.example.com", 443),
    ]
