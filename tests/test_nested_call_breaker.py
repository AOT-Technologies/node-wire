#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Circuit breaker accounting for nested ``call_action`` on the same connector instance.

The outer call owns the breaker: the nested call neither asks it nor reports to it, and the
outer call classifies a nested failure by the nested call's own category.
"""

from __future__ import annotations

import asyncio
from typing import Literal
from functools import partial

import httpx
import pytest
from pydantic import BaseModel

from node_wire_runtime import BaseConnector, ErrorCategory, nw_action
from node_wire_runtime.resilience import with_resilience


class _Outer(BaseModel):
    action: Literal["outer"] = "outer"


class _Inner(BaseModel):
    action: Literal["inner"] = "inner"
    mode: str = "ok"


class _Out(BaseModel):
    ok: bool = True


class NestedProbe(BaseConnector):
    connector_id = "test_nested_breaker_probe"
    output_model = _Out
    breaker_fail_max = 2
    breaker_reset_timeout = 0.05

    def __init__(self) -> None:
        super().__init__()
        self.inner_mode = "connect"
        self.inner_calls = 0

    @nw_action("outer")
    async def outer(self, params: _Outer, *, trace_id: str) -> _Out:
        return await self.call_action("inner", {"mode": self.inner_mode})

    @nw_action("inner")
    async def inner(self, params: _Inner, *, trace_id: str) -> _Out:
        self.inner_calls += 1
        if params.mode == "connect":
            raise httpx.ConnectError("refused")
        return _Out()


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "node_wire_runtime.base_connector.with_resilience",
        partial(with_resilience, base_wait=0.001, max_wait=0.002),
    )


async def _outer(connector: NestedProbe) -> object:
    return await connector.run({"action": "outer"}, tenant_id="t1")


async def test_repeated_nested_transport_failures_trip_the_breaker() -> None:
    connector = NestedProbe()
    breaker = connector._breaker_for_tenant("t1")

    first = await _outer(connector)
    assert first.success is False
    assert first.error_code == "ConnectError"  # the nested call's own code
    assert breaker.fail_counter == 1

    await _outer(connector)
    assert breaker.state.name == "open"

    calls = connector.inner_calls
    rejected = await _outer(connector)
    assert rejected.success is False
    assert connector.inner_calls == calls  # rejected before running


async def test_nested_retryable_failure_is_reported_with_its_own_category() -> None:
    connector = NestedProbe()
    from node_wire_runtime.errors import ErrorMapper

    ErrorMapper.register(
        "test_nested_breaker_probe", httpx.ConnectError, ErrorCategory.RETRYABLE, code="DOWN"
    )
    try:
        resp = await _outer(connector)
    finally:
        ErrorMapper._connector_registries["test_nested_breaker_probe"].pop(httpx.ConnectError)
    assert resp.error_code == "DOWN"
    assert resp.error_category is ErrorCategory.RETRYABLE
    # The nested call retried (3 attempts); the outer call does not retry it again.
    assert connector.inner_calls == 3
    assert connector._breaker_for_tenant("t1").fail_counter == 1


async def test_half_open_trial_whose_nested_call_fails_does_not_close_the_breaker() -> None:
    connector = NestedProbe()
    breaker = connector._breaker_for_tenant("t1")
    await _outer(connector)
    await _outer(connector)
    assert breaker.state.name == "open"

    await asyncio.sleep(0.08)
    # The trial: the nested call reaches the upstream (it is not rejected by the half-open
    # breaker its own outer call holds) and fails, so the breaker opens again.
    calls = connector.inner_calls
    trial = await _outer(connector)
    assert trial.success is False
    assert connector.inner_calls == calls + 1
    assert breaker.state.name == "open"


async def test_half_open_trial_with_a_healthy_nested_call_closes_the_breaker() -> None:
    connector = NestedProbe()
    breaker = connector._breaker_for_tenant("t1")
    await _outer(connector)
    await _outer(connector)
    assert breaker.state.name == "open"

    await asyncio.sleep(0.08)
    connector.inner_mode = "ok"
    trial = await _outer(connector)
    assert trial.success is True
    assert breaker.state.name == "closed"


async def test_a_trial_that_fails_before_reaching_the_upstream_does_not_close_it() -> None:
    """An unmapped bug (FATAL, not transport) says nothing about the upstream."""
    connector = NestedProbe()
    breaker = connector._breaker_for_tenant("t1")
    await _outer(connector)
    await _outer(connector)
    assert breaker.state.name == "open"

    await asyncio.sleep(0.08)

    async def _boom(self: NestedProbe, params: _Outer, *, trace_id: str) -> _Out:
        raise RuntimeError("bug before any upstream call")

    connector.outer = _boom.__get__(connector)  # type: ignore[method-assign]
    resp = await _outer(connector)
    assert resp.success is False
    assert breaker.state.name != "closed"
