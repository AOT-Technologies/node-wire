#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for OpenTelemetry metric recording in BaseConnector.run()."""

from __future__ import annotations

from typing import Literal
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from node_wire_runtime import BaseConnector, ErrorCategory, ErrorMapper, nw_action
import node_wire_runtime.base_connector as bc_module
import node_wire_runtime.rate_limit as rl_module
import node_wire_runtime.resilience as res_module
from pybreaker import CircuitBreaker


class _MIn(BaseModel):
    action: Literal["run"] = "run"


class _MOut(BaseModel):
    done: bool


class _MetricConnector(BaseConnector):
    connector_id = "metric_test"
    output_model = _MOut

    @nw_action("run")
    async def run_action(self, params: _MIn, *, trace_id: str) -> _MOut:
        return _MOut(done=True)


class _MetricFailConnector(BaseConnector):
    connector_id = "metric_fail"
    output_model = _MOut

    @nw_action("run")
    async def run_action(self, params: _MIn, *, trace_id: str) -> _MOut:
        raise ValueError("intentional failure")


@pytest.mark.asyncio
async def test_invocation_counter_incremented_on_success() -> None:
    connector = _MetricConnector()
    mock_counter = MagicMock()
    mock_histogram = MagicMock()
    with (
        patch.object(bc_module, "_invocation_counter", mock_counter),
        patch.object(bc_module, "_invocation_duration", mock_histogram),
    ):
        resp = await connector.run({"action": "run"})
    assert resp.success is True
    mock_counter.add.assert_called_once()
    call_attrs = mock_counter.add.call_args[1]["attributes"]
    assert call_attrs["connector.id"] == "metric_test"
    assert call_attrs["success"] is True
    assert call_attrs["error_category"] == "none"


@pytest.mark.asyncio
async def test_invocation_histogram_recorded_on_success() -> None:
    connector = _MetricConnector()
    mock_counter = MagicMock()
    mock_histogram = MagicMock()
    with (
        patch.object(bc_module, "_invocation_counter", mock_counter),
        patch.object(bc_module, "_invocation_duration", mock_histogram),
    ):
        await connector.run({"action": "run"})
    mock_histogram.record.assert_called_once()
    duration_val = mock_histogram.record.call_args[0][0]
    assert duration_val >= 0


@pytest.mark.asyncio
async def test_invocation_counter_incremented_on_failure() -> None:
    connector = _MetricFailConnector()
    mock_counter = MagicMock()
    mock_histogram = MagicMock()
    with (
        patch.object(bc_module, "_invocation_counter", mock_counter),
        patch.object(bc_module, "_invocation_duration", mock_histogram),
    ):
        resp = await connector.run({"action": "run"})
    assert resp.success is False
    mock_counter.add.assert_called_once()
    call_attrs = mock_counter.add.call_args[1]["attributes"]
    assert call_attrs["success"] is False
    assert call_attrs["error_category"] != "none"


@pytest.mark.asyncio
async def test_invocation_counter_incremented_on_validation_failure() -> None:
    connector = _MetricConnector()
    mock_counter = MagicMock()
    mock_histogram = MagicMock()
    with (
        patch.object(bc_module, "_invocation_counter", mock_counter),
        patch.object(bc_module, "_invocation_duration", mock_histogram),
    ):
        resp = await connector.run({"action": "nonexistent"})
    assert resp.success is False
    mock_counter.add.assert_called_once()
    call_attrs = mock_counter.add.call_args[1]["attributes"]
    assert call_attrs["success"] is False


@pytest.mark.asyncio
async def test_metric_attributes_include_connector_action() -> None:
    connector = _MetricConnector()
    mock_counter = MagicMock()
    with patch.object(bc_module, "_invocation_counter", mock_counter):
        with patch.object(bc_module, "_invocation_duration", MagicMock()):
            await connector.run({"action": "run"})
    attrs = mock_counter.add.call_args[1]["attributes"]
    assert attrs["connector.action"] == "execute"


class _RetryableMetricError(Exception):
    pass


@pytest.fixture
def _register_retryable() -> None:
    ErrorMapper.register(
        "retry_cx", _RetryableMetricError, ErrorCategory.RETRYABLE, code="RETRYABLE_METRIC"
    )
    try:
        yield
    finally:
        ErrorMapper._connector_registries.get("retry_cx", {}).pop(_RetryableMetricError, None)


@pytest.mark.asyncio
async def test_retry_counter_incremented_on_retryable_error(_register_retryable: None) -> None:
    attempts = {"n": 0}

    @res_module.with_resilience(CircuitBreaker(), connector_id="retry_cx", action="do")
    async def flaky(*, trace_id: str = "t") -> str:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise _RetryableMetricError("boom")
        return "ok"

    mock_counter = MagicMock()
    with patch.object(res_module, "_retry_counter", mock_counter):
        result = await flaky(trace_id="t1")

    assert result == "ok"
    # One retryable failure occurred before the successful attempt.
    mock_counter.add.assert_called_once()
    attrs = mock_counter.add.call_args[1]["attributes"]
    assert attrs["connector.id"] == "retry_cx"
    assert attrs["connector.action"] == "do"
    assert attrs["error_code"] == "RETRYABLE_METRIC"


@pytest.mark.asyncio
async def test_circuit_breaker_rejection_counter_incremented() -> None:
    breaker = CircuitBreaker()
    breaker.open()

    @res_module.with_resilience(breaker, connector_id="cb_cx", action="do")
    async def never_runs(*, trace_id: str = "t") -> str:
        return "unreachable"

    mock_counter = MagicMock()
    with patch.object(res_module, "_circuit_breaker_rejections", mock_counter):
        with pytest.raises(Exception):
            await never_runs(trace_id="t1")

    mock_counter.add.assert_called_once()
    attrs = mock_counter.add.call_args[1]["attributes"]
    assert attrs["connector.id"] == "cb_cx"
    assert attrs["connector.action"] == "do"


@pytest.mark.asyncio
async def test_rate_limit_rejection_counter_incremented() -> None:
    bucket = rl_module.TokenBucket(capacity=1, refill_rate=0)
    await bucket.acquire()  # consumes the only token

    mock_counter = MagicMock()
    with patch.object(rl_module, "_rate_limit_rejections", mock_counter):
        with pytest.raises(rl_module.RateLimitExceeded):
            await bucket.acquire()

    mock_counter.add.assert_called_once()
