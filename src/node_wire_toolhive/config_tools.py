# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Named-config tools for stacklok-built MCP servers.

The tenant is fixed by the ToolHive proxy (``X-Tenant-ID``), so there is no tenant-selection
tool; these only choose among the tenant's named configs for the current MCP session.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from node_wire_runtime.identity import is_multitenancy_enabled
from node_wire_toolhive.client import NodeWireClient

LIST_CONFIGS_TOOL = "nw_list_configs"
SELECT_CONFIG_TOOL = "nw_select_config"


def register_config_tools(mcp: FastMCP, client: NodeWireClient) -> None:
    """Add ``nw_list_configs`` / ``nw_select_config`` when multitenancy is enabled.

    Their failures (no tenant, unknown config) are logged and reported with a ``trace_id``, like
    a connector tool's.
    """
    if not is_multitenancy_enabled():
        return

    async def nw_list_configs() -> Dict[str, Any]:
        """List the named configs available to your tenant; the default is used unless you
        select another with nw_select_config."""
        try:
            tenant_id = client.tenant_id()
            return {
                "tenant_id": tenant_id,
                "selected": client.selected_config(),
                "configs": client.config_names(tenant_id),
            }
        except Exception as exc:  # noqa: BLE001 — surface as a traced tool error
            raise client._rejected(exc, action=LIST_CONFIGS_TOOL, tenant_id=None) from exc

    async def nw_select_config(
        config_name: Annotated[str, Field(description="Config name from nw_list_configs.")],
    ) -> Dict[str, Any]:
        """Use a named config for the rest of this session's tool calls."""
        try:
            tenant_id = client.select_config(config_name)
        except Exception as exc:  # noqa: BLE001 — surface as a traced tool error
            raise client._rejected(exc, action=SELECT_CONFIG_TOOL, tenant_id=None) from exc
        return {"tenant_id": tenant_id, "selected": config_name}

    mcp.add_tool(nw_list_configs, name=LIST_CONFIGS_TOOL)
    mcp.add_tool(nw_select_config, name=SELECT_CONFIG_TOOL)
