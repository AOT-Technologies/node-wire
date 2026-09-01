#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for gRPC server servicer and auth interceptor."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest

from bindings.grpc_server import connector_pb2
from bindings.grpc_server.auth import (
    GrpcAuthInterceptor,
    _extract_token,
    _truthy,
)
from bindings.grpc_server.server import ConnectorServiceServicer
from node_wire_runtime import ConnectorResponse, ErrorCategory
from node_wire_runtime.rate_limit import RateLimitExceeded


# ---------------------------------------------------------------------------
# _truthy helper
# ---------------------------------------------------------------------------


def test_truthy_none_is_false() -> None:
    assert _truthy(None) is False


def test_truthy_true_string() -> None:
    for val in ("true", "TRUE", "1", "yes", "on", "  True  "):
        assert _truthy(val) is True, val


def test_truthy_false_string() -> None:
    for val in ("false", "0", "no", "off", ""):
        assert _truthy(val) is False, val


# ---------------------------------------------------------------------------
# _extract_token helper
# ---------------------------------------------------------------------------


def test_extract_token_from_authorization_bearer() -> None:
    meta = (("authorization", "Bearer mytoken123"),)
    assert _extract_token(meta) == "mytoken123"


def test_extract_token_from_authorization_raw() -> None:
    meta = (("authorization", "rawtoken"),)
    assert _extract_token(meta) == "rawtoken"


def test_extract_token_from_x_api_key() -> None:
    meta = (("x-api-key", "  apikey  "),)
    assert _extract_token(meta) == "apikey"


def test_extract_token_returns_none_when_absent() -> None:
    assert _extract_token(()) is None
    assert _extract_token((("content-type", "application/json"),)) is None


# ---------------------------------------------------------------------------
# GrpcAuthInterceptor.intercept_service
# ---------------------------------------------------------------------------


def _make_call_details(metadata: tuple = ()) -> grpc.HandlerCallDetails:
    details = MagicMock(spec=grpc.HandlerCallDetails)
    details.invocation_metadata = metadata
    return details


def test_interceptor_auth_disabled_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_GRPC_AUTH_DISABLED", "true")
    sentinel = object()
    cont = MagicMock(return_value=sentinel)
    result = GrpcAuthInterceptor().intercept_service(cont, _make_call_details())
    assert result is sentinel
    cont.assert_called_once()


def test_interceptor_returns_abort_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_GRPC_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("NW_GRPC_API_KEY", raising=False)
    monkeypatch.delenv("NW_GRPC_JWT_SECRET", raising=False)

    cont = MagicMock()
    handler = GrpcAuthInterceptor().intercept_service(cont, _make_call_details())
    # The returned handler should abort when called
    context = MagicMock(spec=grpc.ServicerContext)
    handler.unary_unary(None, context)
    context.abort.assert_called_once()
    code, msg = context.abort.call_args[0]
    assert code == grpc.StatusCode.UNAVAILABLE
    assert "NW_GRPC_API_KEY" in msg


def test_interceptor_returns_unauthenticated_when_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_GRPC_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_GRPC_API_KEY", "secret")

    cont = MagicMock()
    handler = GrpcAuthInterceptor().intercept_service(cont, _make_call_details(metadata=()))
    context = MagicMock(spec=grpc.ServicerContext)
    handler.unary_unary(None, context)
    context.abort.assert_called_once()
    code, _ = context.abort.call_args[0]
    assert code == grpc.StatusCode.UNAUTHENTICATED


def test_interceptor_returns_unauthenticated_for_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_GRPC_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_GRPC_API_KEY", "correct-key")
    monkeypatch.delenv("NW_GRPC_JWT_SECRET", raising=False)

    cont = MagicMock()
    meta = (("x-api-key", "wrong-key"),)
    handler = GrpcAuthInterceptor().intercept_service(cont, _make_call_details(metadata=meta))
    context = MagicMock(spec=grpc.ServicerContext)
    handler.unary_unary(None, context)
    context.abort.assert_called_once()
    code, _ = context.abort.call_args[0]
    assert code == grpc.StatusCode.UNAUTHENTICATED


def test_interceptor_wraps_handler_on_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_GRPC_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_GRPC_API_KEY", "valid-key")
    monkeypatch.delenv("NW_GRPC_JWT_SECRET", raising=False)

    inner_handler = grpc.unary_unary_rpc_method_handler(lambda req, ctx: "ok")
    cont = MagicMock(return_value=inner_handler)
    meta = (("x-api-key", "valid-key"),)
    result = GrpcAuthInterceptor().intercept_service(cont, _make_call_details(metadata=meta))
    assert result is not None
    assert result.unary_unary is not None
    assert result.unary_unary(None, None) == "ok"


def test_interceptor_returns_none_when_continuation_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NW_GRPC_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_GRPC_API_KEY", "valid-key")
    monkeypatch.delenv("NW_GRPC_JWT_SECRET", raising=False)

    cont = MagicMock(return_value=None)
    meta = (("x-api-key", "valid-key"),)
    result = GrpcAuthInterceptor().intercept_service(cont, _make_call_details(metadata=meta))
    assert result is None


def test_interceptor_jwt_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.jwt_test_helpers import mint_test_jwt

    monkeypatch.delenv("NW_GRPC_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("NW_GRPC_API_KEY", raising=False)
    monkeypatch.setenv("NW_GRPC_JWT_SECRET", "test-grpc-secret")

    token = mint_test_jwt({"sub": "svc"}, "test-grpc-secret")
    inner_handler = grpc.unary_unary_rpc_method_handler(lambda req, ctx: "ok")
    cont = MagicMock(return_value=inner_handler)
    meta = (("authorization", f"Bearer {token}"),)
    result = GrpcAuthInterceptor().intercept_service(cont, _make_call_details(metadata=meta))
    assert result is not None
    assert result.unary_unary(None, None) == "ok"


# ---------------------------------------------------------------------------
# ConnectorServiceServicer._invoke_async
# ---------------------------------------------------------------------------


@pytest.fixture()
def servicer() -> ConnectorServiceServicer:
    return ConnectorServiceServicer()


async def test_invoke_rate_limit_exceeded(servicer: ConnectorServiceServicer) -> None:
    async def _raise(*_a: Any, **_kw: Any) -> None:
        raise RateLimitExceeded("too fast")

    with patch("bindings.grpc_server.server.global_rate_limiter") as mock_rl:
        mock_rl.acquire = _raise
        req = connector_pb2.InvokeRequest(connector_id="any", action="act")
        resp = await servicer._invoke_async(req)

    assert resp.success is False
    assert resp.error_code == "RATE_LIMIT_EXCEEDED"
    assert resp.error_category == ErrorCategory.RETRYABLE.value


async def test_invoke_unknown_connector_returns_not_available(
    servicer: ConnectorServiceServicer,
) -> None:
    with patch.object(servicer._factory, "is_exposed", return_value=False):
        req = connector_pb2.InvokeRequest(connector_id="no_such", action="act")
        resp = await servicer._invoke_async(req)

    assert resp.success is False
    assert resp.error_code == "CONNECTOR_NOT_AVAILABLE"
    assert resp.error_category == ErrorCategory.BUSINESS.value
    assert "no_such" in resp.message


async def test_invoke_invalid_json_payload(servicer: ConnectorServiceServicer) -> None:
    fake_connector = MagicMock()
    with (
        patch.object(servicer._factory, "is_exposed", return_value=True),
        patch.object(servicer._factory, "get", new=AsyncMock(return_value=fake_connector)),
    ):
        req = connector_pb2.InvokeRequest(
            connector_id="x", action="y", payload_json="not-valid-json{"
        )
        resp = await servicer._invoke_async(req)

    assert resp.success is False
    assert resp.error_code == "INVALID_JSON"
    assert resp.error_category == ErrorCategory.BUSINESS.value


async def test_invoke_success_path(servicer: ConnectorServiceServicer) -> None:
    fake_connector = MagicMock()
    fake_connector.run = AsyncMock(
        return_value=ConnectorResponse(
            success=True,
            data={"result": "hello"},
            trace_id="trace-001",
        )
    )
    with (
        patch.object(servicer._factory, "is_exposed", return_value=True),
        patch.object(servicer._factory, "get", new=AsyncMock(return_value=fake_connector)),
    ):
        req = connector_pb2.InvokeRequest(
            connector_id="x", action="greet", payload_json='{"field": "val"}'
        )
        resp = await servicer._invoke_async(req)

    assert resp.success is True
    assert resp.trace_id == "trace-001"
    assert "result" in resp.data_json


async def test_invoke_action_injected_into_payload(servicer: ConnectorServiceServicer) -> None:
    captured_payload: list[Any] = []

    async def mock_run(payload: Any, **_: Any) -> ConnectorResponse:
        captured_payload.append(payload)
        return ConnectorResponse(success=True, trace_id="t1")

    fake_connector = MagicMock()
    fake_connector.run = mock_run
    with (
        patch.object(servicer._factory, "is_exposed", return_value=True),
        patch.object(servicer._factory, "get", new=AsyncMock(return_value=fake_connector)),
    ):
        req = connector_pb2.InvokeRequest(
            connector_id="x",
            action="do_thing",
            payload_json='{"some": "data"}',
        )
        await servicer._invoke_async(req)

    assert captured_payload[0]["action"] == "do_thing"


async def test_invoke_conflicting_action_rejected(servicer: ConnectorServiceServicer) -> None:
    fake_connector = MagicMock()
    fake_connector.run = AsyncMock()
    with (
        patch.object(servicer._factory, "is_exposed", return_value=True),
        patch.object(servicer._factory, "get", new=AsyncMock(return_value=fake_connector)),
    ):
        req = connector_pb2.InvokeRequest(
            connector_id="x",
            action="do_thing",
            payload_json='{"action": "other_action", "some": "data"}',
        )
        resp = await servicer._invoke_async(req)

    assert resp.success is False
    assert resp.error_code == "INVALID_PAYLOAD"
    assert resp.error_category == ErrorCategory.BUSINESS.value
    fake_connector.run.assert_not_called()


async def test_invoke_identity_propagated(servicer: ConnectorServiceServicer) -> None:
    from node_wire_runtime.caller_identity import build_caller_identity
    from node_wire_runtime.config_store import DEFAULT_TENANT

    identity = build_caller_identity({"sub": "grpc-svc"}, auth_type="grpc_api_key")
    captured: list[Any] = []

    async def mock_run(payload: Any, **kwargs: Any) -> ConnectorResponse:
        captured.append(kwargs)
        return ConnectorResponse(success=True, trace_id="t2")

    fake_connector = MagicMock()
    fake_connector.run = mock_run
    with (
        patch.object(servicer._factory, "is_exposed", return_value=True),
        patch.object(servicer._factory, "get", new=AsyncMock(return_value=fake_connector)),
        patch("bindings.grpc_server.server.get_grpc_caller_identity", return_value=identity),
    ):
        req = connector_pb2.InvokeRequest(connector_id="x", action="act", payload_json="{}")
        await servicer._invoke_async(req)

    assert captured[0]["principal"] == identity.principal
    # Multitenancy off in tests: resolve_tenant_id always returns DEFAULT_TENANT.
    assert captured[0]["tenant_id"] == DEFAULT_TENANT


async def test_invoke_no_identity_passes_none(servicer: ConnectorServiceServicer) -> None:
    from node_wire_runtime.config_store import DEFAULT_TENANT

    captured: list[Any] = []

    async def mock_run(payload: Any, **kwargs: Any) -> ConnectorResponse:
        captured.append(kwargs)
        return ConnectorResponse(success=True, trace_id="t3")

    fake_connector = MagicMock()
    fake_connector.run = mock_run
    with (
        patch.object(servicer._factory, "is_exposed", return_value=True),
        patch.object(servicer._factory, "get", new=AsyncMock(return_value=fake_connector)),
        patch("bindings.grpc_server.server.get_grpc_caller_identity", return_value=None),
    ):
        req = connector_pb2.InvokeRequest(connector_id="x", action="act", payload_json="{}")
        await servicer._invoke_async(req)

    assert captured[0]["principal"] is None
    assert captured[0]["tenant_id"] == DEFAULT_TENANT


async def test_invoke_empty_payload_json(servicer: ConnectorServiceServicer) -> None:
    fake_connector = MagicMock()
    fake_connector.run = AsyncMock(return_value=ConnectorResponse(success=True, trace_id="t4"))
    with (
        patch.object(servicer._factory, "is_exposed", return_value=True),
        patch.object(servicer._factory, "get", new=AsyncMock(return_value=fake_connector)),
    ):
        req = connector_pb2.InvokeRequest(connector_id="x", action="act")
        resp = await servicer._invoke_async(req)
    assert resp.success is True


async def test_invoke_error_response_maps_error_category(
    servicer: ConnectorServiceServicer,
) -> None:
    fake_connector = MagicMock()
    fake_connector.run = AsyncMock(
        return_value=ConnectorResponse(
            success=False,
            error_code="UPSTREAM_TIMEOUT",
            error_category=ErrorCategory.RETRYABLE,
            message="upstream timed out",
            trace_id="t5",
        )
    )
    with (
        patch.object(servicer._factory, "is_exposed", return_value=True),
        patch.object(servicer._factory, "get", new=AsyncMock(return_value=fake_connector)),
    ):
        req = connector_pb2.InvokeRequest(connector_id="x", action="act", payload_json="{}")
        resp = await servicer._invoke_async(req)

    assert resp.success is False
    assert resp.error_code == "UPSTREAM_TIMEOUT"
    assert resp.error_category == ErrorCategory.RETRYABLE.value


async def test_invoke_missing_tenant_when_multitenancy_enabled(
    servicer: ConnectorServiceServicer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    with patch("bindings.grpc_server.server.get_grpc_caller_identity", return_value=None):
        req = connector_pb2.InvokeRequest(connector_id="x", action="act")
        resp = await servicer._invoke_async(req)

    assert resp.success is False
    assert resp.error_code == "MISSING_TENANT"
    assert resp.error_category == ErrorCategory.AUTH.value


async def test_invoke_config_not_found(servicer: ConnectorServiceServicer) -> None:
    from node_wire_runtime.config_store import ConfigNotFoundError

    with (
        patch.object(servicer._factory, "is_exposed", return_value=True),
        patch.object(
            servicer._factory,
            "get",
            new=AsyncMock(side_effect=ConfigNotFoundError("no config")),
        ),
    ):
        req = connector_pb2.InvokeRequest(connector_id="x", action="act", payload_json="{}")
        resp = await servicer._invoke_async(req)

    assert resp.success is False
    assert resp.error_code == "CONFIG_NOT_FOUND"
    assert resp.error_category == ErrorCategory.AUTH.value


def test_invoke_passes_metadata_headers(servicer: ConnectorServiceServicer) -> None:
    context = MagicMock()
    context.invocation_metadata.return_value = (("x-tenant-id", "acme"),)
    req = connector_pb2.InvokeRequest(connector_id="x", action="act")

    with patch("bindings.grpc_server.server._async_runner") as runner:
        runner.run.return_value = connector_pb2.InvokeResponse(success=True, trace_id="t")
        resp = servicer.Invoke(req, context)

    assert resp.success is True
    call_args = runner.run.call_args[0][0]
    # The coroutine was created with metadata; force close to avoid warnings.
    call_args.close()
