#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for per-invocation audit log events emitted by BaseConnector.run()."""

from __future__ import annotations

import logging
from typing import Literal

import pytest
from pydantic import BaseModel

from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.policy import PolicyContext, PolicyDenied, PolicyHook


class _In(BaseModel):
    action: Literal["go"] = "go"


class _Out(BaseModel):
    ok: bool


class _AuditConnector(BaseConnector):
    connector_id = "audit_test"
    output_model = _Out

    @nw_action("go")
    async def go(self, params: _In, *, trace_id: str) -> _Out:
        return _Out(ok=True)


class _BoomConnector(BaseConnector):
    connector_id = "audit_boom"
    output_model = _Out

    @nw_action("go")
    async def go(self, params: _In, *, trace_id: str) -> _Out:
        raise RuntimeError("something broke")


class _DenyAll(PolicyHook):
    def check(self, ctx: PolicyContext) -> None:
        raise PolicyDenied("not allowed")


@pytest.mark.asyncio
async def test_audit_invocation_start_emitted(caplog: pytest.LogCaptureFixture) -> None:
    connector = _AuditConnector()
    with caplog.at_level(logging.INFO, logger="runtime.base_connector"):
        await connector.run(
            {"action": "go"},
            principal="alice",
            tenant_id="t1",
            scopes=("scope:read",),
        )
    start_records = [
        r for r in caplog.records if r.__dict__.get("audit_event") == "invocation_start"
    ]
    assert len(start_records) == 1
    rec = start_records[0]
    assert rec.__dict__.get("audit") is True
    assert rec.__dict__.get("connector_id") == "audit_test"
    assert rec.__dict__.get("principal") == "alice"
    assert rec.__dict__.get("tenant_id") == "t1"
    assert "scope:read" in rec.__dict__.get("scopes", [])


@pytest.mark.asyncio
async def test_audit_invocation_success_emitted(caplog: pytest.LogCaptureFixture) -> None:
    connector = _AuditConnector()
    with caplog.at_level(logging.INFO, logger="runtime.base_connector"):
        resp = await connector.run({"action": "go"})
    assert resp.success is True
    success_records = [
        r for r in caplog.records if r.__dict__.get("audit_event") == "invocation_success"
    ]
    assert len(success_records) == 1
    rec = success_records[0]
    assert rec.__dict__.get("audit") is True
    assert rec.__dict__.get("connector_id") == "audit_test"
    assert "duration_ms" in rec.__dict__


@pytest.mark.asyncio
async def test_audit_invocation_failure_emitted(caplog: pytest.LogCaptureFixture) -> None:
    connector = _BoomConnector()
    with caplog.at_level(logging.ERROR, logger="runtime.base_connector"):
        resp = await connector.run({"action": "go"})
    assert resp.success is False
    failure_records = [
        r for r in caplog.records if r.__dict__.get("audit_event") == "invocation_failure"
    ]
    assert len(failure_records) == 1
    rec = failure_records[0]
    assert rec.__dict__.get("audit") is True
    assert rec.__dict__.get("connector_id") == "audit_boom"
    assert "duration_ms" in rec.__dict__


@pytest.mark.asyncio
async def test_audit_invocation_validation_failure_emitted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connector = _AuditConnector()
    with caplog.at_level(logging.ERROR, logger="runtime.base_connector"):
        await connector.run({"action": "go", "unexpected_extra_field_that_breaks": True})
    # Validation may succeed (extra fields tolerated by default) or fail depending on model config.
    # Use a clearly wrong payload instead.
    with caplog.at_level(logging.ERROR, logger="runtime.base_connector"):
        resp = await connector.run({"action": "nonexistent_action"})
    assert resp.success is False
    validation_records = [
        r
        for r in caplog.records
        if r.__dict__.get("audit_event") == "invocation_validation_failure"
    ]
    assert len(validation_records) >= 1
    rec = validation_records[0]
    assert rec.__dict__.get("audit") is True


@pytest.mark.asyncio
async def test_audit_policy_denial_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    connector = _AuditConnector(policy_hook=_DenyAll())
    with caplog.at_level(logging.WARNING, logger="runtime.base_connector"):
        resp = await connector.run({"action": "go"})
    assert resp.success is False
    denial_records = [r for r in caplog.records if r.__dict__.get("audit_event") == "policy_denial"]
    assert len(denial_records) == 1
    assert denial_records[0].__dict__.get("audit") is True


@pytest.mark.asyncio
async def test_audit_start_includes_empty_scopes_when_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connector = _AuditConnector()
    with caplog.at_level(logging.INFO, logger="runtime.base_connector"):
        await connector.run({"action": "go"})
    start_records = [
        r for r in caplog.records if r.__dict__.get("audit_event") == "invocation_start"
    ]
    assert len(start_records) == 1
    assert start_records[0].__dict__.get("scopes") == []
