# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""node-wire-toolhive: run node-wire connectors inside stacklok-built MCP servers behind ToolHive.

ToolHive owns auth: it authenticates the MCP client, then forwards the upstream credential as
``Authorization: Bearer`` and (per tenant proxy) an ``X-Tenant-ID`` header. This layer relays that
credential into the connector's own auth placement, resolves the tenant from the header, and runs
actions through the node-wire runtime (validation, policy hook, retries, errors, telemetry).
"""

from node_wire_toolhive.arguments import report_call_errors
from node_wire_toolhive.client import NodeWireClient, NodeWireToolError
from node_wire_toolhive.config_tools import register_config_tools
from node_wire_toolhive.relay import RelayAuthProvider, relay_auth_provider_hook
from node_wire_toolhive.telemetry import init_telemetry

__all__ = [
    "NodeWireClient",
    "NodeWireToolError",
    "RelayAuthProvider",
    "init_telemetry",
    "register_config_tools",
    "relay_auth_provider_hook",
    "report_call_errors",
]
