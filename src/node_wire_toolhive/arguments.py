# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Tool calls FastMCP rejects itself, reported in node-wire's error taxonomy.

FastMCP checks a call's tool name and arguments before the tool runs, and reports a failure in
its own words (``Unknown tool: x``, pydantic's validation text): no error code, no trace id.
Every other failure of a stacklok-built server reads ``CODE [CATEGORY]: message (trace_id=...)``,
so a client (or an operator reading the logs) would handle these differently for no reason.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from node_wire_runtime.errors import ErrorCode, NodeWireError, reject, validation_error
from node_wire_toolhive.client import NodeWireClient, NodeWireToolError

logger = logging.getLogger("node_wire_toolhive")


def report_call_errors(mcp: FastMCP, client: NodeWireClient) -> None:
    """Make unknown tools and invalid arguments fail like every other node-wire tool call.

    Such a call then fails with ``UNKNOWN_TOOL [BUSINESS]`` or ``VALIDATION_ERROR [BUSINESS]:
    Input validation failed; channel: Field required (trace_id=...)``, logged by the runtime
    under that trace id. It covers every tool, whenever it is added; the listing is unchanged.
    """
    # FastMCP has no public hook here. Its ToolManager is a plain class, and FastMCP looks up
    # manager.call_tool on every call, so wrapping that one method is enough. (Not a Tool
    # subclass: Tool is a pydantic model, which rejects Cython-compiled methods.)
    manager = getattr(mcp, "_tool_manager", None)
    call_tool = getattr(manager, "call_tool", None)
    if manager is None or call_tool is None or not hasattr(manager, "get_tool"):
        logger.warning("FastMCP's tool manager moved; call errors keep the SDK's wording")
        return

    def rejected(exc: NodeWireError, tool: str) -> NodeWireToolError:
        return NodeWireToolError.from_response(
            reject(exc, connector_id=client.connector_id, action=tool)
        )

    async def checked_call_tool(
        name: str, arguments: dict[str, Any], *args: Any, **kwargs: Any
    ) -> Any:
        tool = manager.get_tool(name)
        if tool is None:
            raise rejected(NodeWireError(ErrorCode.UNKNOWN_TOOL, f"Unknown tool: {name}"), name)
        metadata = tool.fn_metadata
        try:
            # The check FastMCP makes next (FuncMetadata.call_fn_with_arg_validation).
            metadata.arg_model.model_validate(metadata.pre_parse_json(arguments))
        except ValidationError as exc:
            raise rejected(validation_error(exc), name) from exc
        return await call_tool(name, arguments, *args, **kwargs)

    manager.call_tool = checked_call_tool
