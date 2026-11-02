#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""The MCP binding still works on a published node-wire-runtime without newer helpers.

Generated host images install node-wire-runtime from PyPI, which can be older than the
bindings in this repo. ``ConnectorConfigStore.has_tenant`` and
``BaseConnector.sdk_action_meta`` are newer than the published runtime.
"""

from __future__ import annotations

from typing import Any

import pytest

from bindings.mcp_server.server import McpServer
from node_wire_runtime.base_connector import BaseConnector
from node_wire_runtime.config_store import ConnectorConfigStore
from node_wire_runtime.models import ConnectorResponse


@pytest.fixture
def old_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(ConnectorConfigStore, "has_tenant")
    monkeypatch.delattr(BaseConnector, "sdk_action_meta")


def test_store_has_tenant_without_runtime_helper(old_runtime: None) -> None:
    server = McpServer(connector_ids=["smtp"])
    assert server._store_has_tenant("no-such-tenant") is False


async def test_tool_call_without_runtime_helper(
    old_runtime: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_invoke(factory: Any, **kwargs: Any) -> ConnectorResponse:
        calls.append(kwargs)
        return ConnectorResponse(success=True, data={"sent": True}, trace_id="t")

    monkeypatch.setattr("bindings.mcp_server.server.invoke", fake_invoke)
    server = McpServer(connector_ids=["smtp"])
    args = {"to": ["a@example.com"], "subject": "Hi", "body": "Hello"}
    await server.invoke_tool("smtp_send_email", args)
    assert calls[0]["action"] == "send_email"
