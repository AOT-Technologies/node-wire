#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Shared Binding invoke path for REST, MCP, and gRPC.

Resolves exposure, factory instance, ingress normalization, and connector.run
in one place. Bindings remain thin adapters for transport-specific Tenant
resolution, rate limits, and response encoding.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from node_wire_runtime import ConnectorResponse
from node_wire_runtime.config_store import ConfigNotFoundError
from node_wire_runtime.ingress import enforce_authoritative_action, normalize_mcp_tool_arguments


class ConnectorNotExposed(Exception):
    """Connector is not exposed on the requested protocol."""

    def __init__(self, connector_id: str, protocol: str) -> None:
        self.connector_id = connector_id
        self.protocol = protocol
        super().__init__(f"Connector {connector_id!r} is not exposed via {protocol!r}")


async def invoke(
    factory: Any,
    *,
    connector_id: str,
    action: str,
    payload: Dict[str, Any],
    protocol: str,
    tenant_id: str,
    config_name: Optional[str] = None,
    principal: Optional[str] = None,
    scopes: Optional[Tuple[str, ...]] = None,
) -> ConnectorResponse:
    """Run one connector action through the shared Binding invoke seam.

    Callers must resolve Tenant and named config before invoking; this module
    does not read headers or MCP session overlay state.
    """
    if not factory.is_exposed(connector_id, protocol):
        raise ConnectorNotExposed(connector_id, protocol)

    connector = await factory.get(
        connector_id,
        tenant_id=tenant_id,
        config_name=config_name,
    )

    run_payload = normalize_mcp_tool_arguments(connector, action, dict(payload))
    enforce_authoritative_action(run_payload, action)
    run_payload["action"] = action

    return await connector.run(
        run_payload,
        principal=principal,
        tenant_id=tenant_id,
        scopes=scopes,
    )
