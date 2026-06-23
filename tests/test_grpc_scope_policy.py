from __future__ import annotations

import asyncio
from typing import Literal

import grpc
import pytest
from pydantic import BaseModel

from bindings.grpc_server.auth import (
    _wrap_unary_handler,
    get_grpc_caller_identity,
    verify_grpc_token_and_identity,
)
from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.caller_identity import build_caller_identity
from node_wire_runtime.policies.mcp_scope_policy import (
    DEFAULT_SCOPE_MODE_DENY,
    ScopePolicyHook,
    load_scope_policy_default_from_env,
)


class _Input(BaseModel):
    action: Literal["read_patient"] = "read_patient"
    resource_id: str


class _Output(BaseModel):
    ok: bool


class _GrpcPolicyConnector(BaseConnector):
    connector_id = "grpc_policy_test"
    output_model = _Output

    @nw_action("read_patient")
    async def read_patient(self, params: _Input, *, trace_id: str) -> _Output:
        return _Output(ok=True)


def test_load_scope_policy_default_is_deny_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_SCOPE_POLICY_DEFAULT", raising=False)
    assert load_scope_policy_default_from_env() == DEFAULT_SCOPE_MODE_DENY


def test_verify_grpc_token_and_identity_api_key_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_GRPC_API_KEY", "secret")
    monkeypatch.setenv("NW_GRPC_API_KEY_SCOPES", '["mcp:grpc_policy_test.read_patient"]')

    ok, identity = verify_grpc_token_and_identity("secret", api_key="secret", jwt_secret=None)
    assert ok is True
    assert identity is not None
    assert identity.auth_type == "grpc_api_key"
    assert "mcp:grpc_policy_test.read_patient" in identity.scopes


def test_grpc_identity_context_var_is_set_for_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = build_caller_identity(
        {"sub": "grpc-user", "scopes": ["mcp:test"]},
        auth_type="grpc_api_key",
    )
    captured: list[object] = []

    def handler(request: object, context: grpc.ServicerContext) -> str:
        captured.append(get_grpc_caller_identity())
        return "ok"

    wrapped = _wrap_unary_handler(grpc.unary_unary_rpc_method_handler(handler), identity)
    assert wrapped.unary_unary is not None
    assert wrapped.unary_unary(None, None) == "ok"
    assert captured[0] is identity
    assert get_grpc_caller_identity() is None


def test_grpc_scope_policy_denies_without_identity() -> None:
    hook = ScopePolicyHook({}, default_mode=DEFAULT_SCOPE_MODE_DENY)
    connector = _GrpcPolicyConnector(policy_hook=hook)
    response = asyncio.run(connector.run({"action": "read_patient", "resource_id": "x"}))

    assert response.success is False
    assert response.error_code == "POLICY_DENIED"


def test_grpc_scope_policy_allows_with_matching_api_key_scope() -> None:
    hook = ScopePolicyHook({}, default_mode=DEFAULT_SCOPE_MODE_DENY)
    connector = _GrpcPolicyConnector(policy_hook=hook)
    identity = build_caller_identity(
        {"sub": "api-key-user", "scopes": ["mcp:grpc_policy_test.read_patient"]},
        auth_type="grpc_api_key",
    )
    response = asyncio.run(
        connector.run(
            {"action": "read_patient", "resource_id": "x"},
            principal=identity.principal,
            tenant_id=identity.tenant_id,
            scopes=identity.scopes,
        )
    )
    assert response.success is True
