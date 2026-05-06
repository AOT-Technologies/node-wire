from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.policies.mcp_scope_policy import ScopePolicyHook


class _Input(BaseModel):
    action: Literal["read_patient"] = "read_patient"
    resource_id: str


class _Output(BaseModel):
    ok: bool


class _PolicyTestConnector(BaseConnector):
    connector_id = "policy_test_fhir_epic"
    output_model = _Output

    @nw_action("read_patient")
    async def read_patient(self, params: _Input, *, trace_id: str) -> _Output:
        return _Output(ok=True)


def _connector_with_scope_map() -> _PolicyTestConnector:
    return _PolicyTestConnector(
        policy_hook=ScopePolicyHook({"policy_test_fhir_epic.read_patient": "mcp:fhir.read_patient"})
    )


def test_scope_policy_bypasses_when_identity_missing_like_grpc() -> None:
    connector = _connector_with_scope_map()
    response = asyncio.run(
        connector.run({"action": "read_patient", "resource_id": "x"})
    )

    assert response.success is True
    assert response.error_code is None


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

