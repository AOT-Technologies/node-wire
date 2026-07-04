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

from node_wire_runtime import BaseConnector, nw_action
import node_wire_runtime.base_connector as bc_module


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
