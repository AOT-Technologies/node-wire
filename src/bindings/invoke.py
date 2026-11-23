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

import logging
from typing import Any, Dict, Iterable, Optional, Tuple

from node_wire_runtime import ConnectorResponse
from node_wire_runtime.errors import CATALOGUE, ErrorCode, ErrorMapper
from node_wire_runtime.exposure import ActionExposure
from node_wire_runtime.ingress import enforce_authoritative_action, normalize_mcp_tool_arguments

logger = logging.getLogger("bindings.invoke")


class ConnectorNotExposed(Exception):
    """Connector is not exposed on the requested protocol."""

    def __init__(self, connector_id: str, protocol: str) -> None:
        self.connector_id = connector_id
        self.protocol = protocol
        super().__init__(f"Connector {connector_id!r} is not exposed via {protocol!r}")


ErrorMapper.register_global(
    ConnectorNotExposed,
    CATALOGUE[ErrorCode.CONNECTOR_NOT_AVAILABLE],
    code=ErrorCode.CONNECTOR_NOT_AVAILABLE,
)


class ActionNotExposed(Exception):
    """The connector is served, but this deployment's exposure profile hides the action."""

    def __init__(self, connector_id: str, action: str, protocol: str) -> None:
        self.connector_id = connector_id
        self.action = action
        self.protocol = protocol
        super().__init__(
            f"Action {action!r} of connector {connector_id!r} is not exposed via {protocol!r}"
        )


ErrorMapper.register_global(
    ActionNotExposed,
    CATALOGUE[ErrorCode.ACTION_NOT_EXPOSED],
    code=ErrorCode.ACTION_NOT_EXPOSED,
)


def build_exposure(
    factory: Any, protocol: str, *, connector_ids: Optional[Iterable[str]] = None
) -> ActionExposure:
    """The process's ``ActionExposure`` for ``protocol`` (see ``NW_EXPOSE_PROFILE``), logged once.

    Raises ``ExposureConfigError`` so a bad profile stops the server instead of exposing everything.
    """
    wanted = None if connector_ids is None else set(connector_ids)
    connectors = [
        c for c in factory.list_for_protocol(protocol) if wanted is None or c.connector_id in wanted
    ]
    exposure = ActionExposure.from_env(connectors, config_path=factory.config_path)
    if exposure.profile:
        logger.info("exposure: profile %s", exposure.profile)
        for cid, count in exposure.summary().items():
            methods = ", ".join(f"{m} {n}" for m, n in count.by_method.items())
            logger.info(
                "exposure: %s: %d of %d actions (%s)", cid, count.exposed, count.total, methods
            )
    return exposure


async def invoke(
    factory: Any,
    *,
    connector_id: str,
    action: str,
    payload: Dict[str, Any],
    protocol: str,
    tenant_id: str,
    exposure: ActionExposure,
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
    if not exposure.allows(connector_id, action):
        raise ActionNotExposed(connector_id, action, protocol)

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
