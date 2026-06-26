#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Regression: ``BaseConnector.call_action`` must honor scope policy via ``run``."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel

from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.errors import ErrorMapper
from node_wire_runtime.models import ErrorCategory
from node_wire_runtime.policies.mcp_scope_policy import (
    DEFAULT_SCOPE_MODE_ALLOW,
    ScopePolicyHook,
)
from node_wire_runtime.policy import PolicyDenied


class _NestedBizError(Exception):
    """Test-only mapped exception for nested action failure semantics."""


ErrorMapper.register(_NestedBizError, ErrorCategory.BUSINESS, code="NESTED_BIZ_TEST")


class _DelInput(BaseModel):
    action: Literal["delegate"] = "delegate"
    resource_id: str


class _ReadInput(BaseModel):
    action: Literal["read_patient"] = "read_patient"
    resource_id: str


class _Output(BaseModel):
    ok: bool


class _CompositeConnector(BaseConnector):
    # Must not reuse a production ``connector_id`` — ``__init_subclass__`` overwrites
    # :data:`_CONNECTOR_REGISTRY` and would break other tests.
    connector_id = "policy_test_composite"
    output_model = _Output

    @nw_action("delegate")
    async def delegate(self, params: _DelInput, *, trace_id: str) -> _Output:
        return await self.call_action(
            "read_patient",
            {"action": "read_patient", "resource_id": params.resource_id},
        )

    @nw_action("read_patient")
    async def read_patient(self, params: _ReadInput, *, trace_id: str) -> _Output:
        return _Output(ok=True)


class _FailNestedConnector(BaseConnector):
    connector_id = "policy_test_fail_nested"
    output_model = _Output

    @nw_action("delegate")
    async def delegate(self, params: _DelInput, *, trace_id: str) -> _Output:
        return await self.call_action(
            "read_patient",
            {"action": "read_patient", "resource_id": params.resource_id},
        )

    @nw_action("read_patient")
    async def read_patient(self, params: _ReadInput, *, trace_id: str) -> _Output:
        raise _NestedBizError("nested failure")


@pytest.mark.asyncio
async def test_call_action_inherits_identity_for_nested_policy() -> None:
    hook = ScopePolicyHook(
        {"policy_test_composite.read_patient": "mcp:fhir.read_patient"},
        default_mode=DEFAULT_SCOPE_MODE_ALLOW,
    )
    connector = _CompositeConnector(policy_hook=hook)

    resp = await connector.run(
        {"action": "delegate", "resource_id": "x"},
        principal="alice",
        tenant_id="t1",
        scopes=("mcp:fhir.read_patient",),
    )
    assert resp.success is True
    assert resp.data is not None
    assert resp.data["ok"] is True


@pytest.mark.asyncio
async def test_call_action_nested_policy_denied_raises() -> None:
    hook = ScopePolicyHook(
        {"policy_test_composite.read_patient": "mcp:fhir.read_patient"},
        default_mode=DEFAULT_SCOPE_MODE_ALLOW,
    )
    connector = _CompositeConnector(policy_hook=hook)

    resp = await connector.run(
        {"action": "delegate", "resource_id": "x"},
        principal="alice",
        tenant_id="t1",
        scopes=("mcp:other.scope",),
    )
    assert resp.success is False
    assert resp.error_code == "POLICY_DENIED"


def test_call_action_direct_raises_policy_denied_sync_wrap() -> None:
    """PolicyDenied from nested run surfaces through async delegate body."""
    hook = ScopePolicyHook(
        {"policy_test_composite.read_patient": "mcp:fhir.read_patient"},
        default_mode=DEFAULT_SCOPE_MODE_ALLOW,
    )
    connector = _CompositeConnector(policy_hook=hook)

    async def _run() -> None:
        await connector.call_action(
            "read_patient",
            {"action": "read_patient", "resource_id": "x"},
            principal="alice",
            scopes=("mcp:other.scope",),
        )

    import asyncio

    with pytest.raises(PolicyDenied):
        asyncio.run(_run())


@pytest.mark.asyncio
async def test_call_action_preserves_nested_error_category_and_code() -> None:
    connector = _FailNestedConnector(policy_hook=None)
    resp = await connector.run({"action": "delegate", "resource_id": "x"})
    assert resp.success is False
    assert resp.error_code == "NESTED_BIZ_TEST"
    assert resp.error_category == ErrorCategory.BUSINESS
    assert "nested failure" in (resp.message or "")
    assert resp.details is not None
    assert "nested_trace_id" in resp.details
