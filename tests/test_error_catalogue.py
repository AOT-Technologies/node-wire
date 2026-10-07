# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""The runtime's error catalogue: one code, one category, one format on every surface."""

from __future__ import annotations

import logging
import re

import pytest
from pydantic import BaseModel, ValidationError

from node_wire_runtime import BaseConnector, ErrorCategory, ErrorMapper, nw_action
from node_wire_runtime.config_store import ConfigNotFoundError
from node_wire_runtime.errors import (
    CATALOGUE,
    ErrorCode,
    NodeWireError,
    error_text,
    http_status,
    reject,
    validation_error,
)
from node_wire_runtime.identity import MissingTenantError

_UUID = r"[0-9a-f-]{36}"


def test_every_code_has_one_category() -> None:
    codes = {v for k, v in vars(ErrorCode).items() if not k.startswith("_")}
    assert codes == set(CATALOGUE)
    assert all(isinstance(category, ErrorCategory) for category in CATALOGUE.values())


def test_a_node_wire_error_takes_its_category_from_the_catalogue() -> None:
    exc = NodeWireError(ErrorCode.MISSING_TENANT, "X-Tenant-ID is required")
    assert (exc.code, exc.category, str(exc)) == (
        "MISSING_TENANT",
        ErrorCategory.AUTH,
        "X-Tenant-ID is required",
    )
    assert isinstance(exc, ValueError)  # callers that catch ValueError keep working
    with pytest.raises(KeyError):
        NodeWireError("NOT_A_CODE", "x")


@pytest.mark.parametrize(
    ("exc", "code", "category"),
    [
        (
            NodeWireError(ErrorCode.UNKNOWN_TOOL, "no such tool"),
            "UNKNOWN_TOOL",
            ErrorCategory.BUSINESS,
        ),
        (MissingTenantError("no tenant"), "MISSING_TENANT", ErrorCategory.AUTH),
        (ConfigNotFoundError("acme", "slack", "staging"), "CONFIG_NOT_FOUND", ErrorCategory.AUTH),
    ],
)
def test_pre_run_failures_resolve_to_catalogue_codes(
    exc: Exception, code: str, category: ErrorCategory
) -> None:
    import node_wire_runtime.base_connector  # noqa: F401  (registers the runtime-wide mappings)

    mapped = ErrorMapper.resolve(exc, connector_id="any")
    assert (mapped.code, mapped.category) == (code, category)


def test_reject_builds_a_traced_envelope_and_logs_it_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        response = reject(
            NodeWireError(ErrorCode.CONFIG_NOT_FOUND, "Unknown config 'staging'"),
            connector_id="slack_web",
            action="nw_select_config",
            tenant_id="acme",
        )

    assert response.success is False
    assert (response.error_code, response.error_category) == (
        "CONFIG_NOT_FOUND",
        ErrorCategory.AUTH,
    )
    assert response.message == "Unknown config 'staging'"
    assert re.fullmatch(_UUID, response.trace_id)
    (record,) = [r for r in caplog.records if getattr(r, "trace_id", None) == response.trace_id]
    assert record.audit_event == "invocation_rejected"
    assert (record.connector_id, record.action, record.tenant_id) == (
        "slack_web",
        "nw_select_config",
        "acme",
    )


def test_reject_scrubs_credentials_from_the_message(caplog: pytest.LogCaptureFixture) -> None:
    exc = RuntimeError("GET https://api.x.com/v1?api_key=sk-live-2 failed: Bearer abc123")
    with caplog.at_level(logging.WARNING):
        response = reject(exc, connector_id="http_generic")

    assert response.message == "GET https://api.x.com/v1 failed: Bearer ***REDACTED***"
    (record,) = [r for r in caplog.records if getattr(r, "trace_id", None) == response.trace_id]
    assert record.error_message == response.message


class _LeakIn(BaseModel):
    action: str = "run"


class _LeakOut(BaseModel):
    done: bool


class _LeakyConnector(BaseConnector):
    connector_id = "leak_test"
    output_model = _LeakOut

    @nw_action("run")
    async def run_action(self, params: _LeakIn, *, trace_id: str) -> _LeakOut:
        raise RuntimeError("GET https://api.x.com/v1?api_key=sk-live-2 failed")


@pytest.mark.asyncio
async def test_a_failed_run_scrubs_credentials_from_the_message() -> None:
    response = await _LeakyConnector().run({"action": "run"})
    assert response.success is False
    assert response.message == "GET https://api.x.com/v1 failed"


def test_invalid_arguments_are_one_validation_error() -> None:
    class Post(BaseModel):
        channel: str
        text: str

    with pytest.raises(ValidationError) as caught:
        Post.model_validate({})
    exc = validation_error(caught.value)

    assert exc.code == ErrorCode.VALIDATION_ERROR and exc.category is ErrorCategory.BUSINESS
    assert str(exc) == "Input validation failed; channel: Field required; text: Field required"
    assert exc.details == [
        {"loc": ["channel"], "msg": "Field required"},
        {"loc": ["text"], "msg": "Field required"},
    ]
    response = reject(exc, connector_id="slack_web", action="post_message")
    assert response.details == exc.details
    assert response.trace_id and response.error_code == "VALIDATION_ERROR"


def test_validation_error_from_plain_problems() -> None:
    exc = validation_error([(("body", "channel"), "'channel' is a required property")])
    assert str(exc) == "Input validation failed; body.channel: 'channel' is a required property"


def test_error_text_is_the_one_line_every_mcp_surface_shows() -> None:
    response = reject(NodeWireError(ErrorCode.MISSING_TENANT, "X-Tenant-ID is required"))
    assert error_text(response) == (
        f"MISSING_TENANT [AUTH]: X-Tenant-ID is required (trace_id={response.trace_id})"
    )


@pytest.mark.parametrize(
    ("category", "status"),
    [
        (None, 200),
        (ErrorCategory.BUSINESS, 400),
        (ErrorCategory.AUTH, 401),
        (ErrorCategory.RETRYABLE, 503),
        (ErrorCategory.FATAL, 500),
    ],
)
def test_http_status_follows_the_category(category: ErrorCategory | None, status: int) -> None:
    assert http_status(category) == status
