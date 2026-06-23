from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.policies.mcp_scope_policy import (
    DEFAULT_SCOPE_MODE_DENY,
    ScopePolicyHook,
)


class _Input(BaseModel):
    action: Literal["read_patient"] = "read_patient"
    resource_id: str


class _Output(BaseModel):
    ok: bool


class _PolicyTestConnector(BaseConnector):
    # Do not shadow production ``fhir_epic`` in the global registry.
    connector_id = "policy_transport_test"
    output_model = _Output

    @nw_action("read_patient")
    async def read_patient(self, params: _Input, *, trace_id: str) -> _Output:
        return _Output(ok=True)


def _connector_with_scope_map() -> _PolicyTestConnector:
    return _PolicyTestConnector(
        policy_hook=ScopePolicyHook({"policy_transport_test.read_patient": "mcp:fhir.read_patient"})
    )


def test_scope_policy_denies_when_identity_missing() -> None:
    connector = _connector_with_scope_map()
    response = asyncio.run(connector.run({"action": "read_patient", "resource_id": "x"}))

    assert response.success is False
    assert response.error_code == "POLICY_DENIED"
    assert response.message == "Missing required scope: mcp:fhir.read_patient"


def test_scope_policy_denies_when_identity_present_without_required_scope() -> None:
    connector = _connector_with_scope_map()
    response = asyncio.run(
        connector.run(
            {"action": "read_patient", "resource_id": "x"},
            principal="alice",
            tenant_id="tenant-1",
            scopes=("mcp:other.scope",),
        )
    )

    assert response.success is False
    assert response.error_code == "POLICY_DENIED"
    assert response.message == "Missing required scope: mcp:fhir.read_patient"


def test_scope_policy_default_deny_uses_conventional_scope() -> None:
    hook = ScopePolicyHook({}, default_mode=DEFAULT_SCOPE_MODE_DENY)
    connector = _PolicyTestConnector(policy_hook=hook)
    response = asyncio.run(
        connector.run(
            {"action": "read_patient", "resource_id": "x"},
            principal="alice",
            tenant_id="tenant-1",
            scopes=("mcp:policy_transport_test.read_patient",),
        )
    )
    assert response.success is True


def test_scope_policy_default_deny_without_fallback_scope() -> None:
    hook = ScopePolicyHook({}, default_mode=DEFAULT_SCOPE_MODE_DENY)
    connector = _PolicyTestConnector(policy_hook=hook)
    response = asyncio.run(
        connector.run(
            {"action": "read_patient", "resource_id": "x"},
            principal="alice",
            tenant_id="tenant-1",
            scopes=("mcp:wrong",),
        )
    )
    assert response.success is False
    assert "Missing required scope" in (response.message or "")
