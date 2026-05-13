#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Shared ingress helpers for connector bindings (MCP, REST, gRPC).

Tool/route action is authoritative for MCP and REST; normalizers map LLM aliases
to canonical Pydantic fields before validation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from node_wire_runtime import BaseConnector

logger = logging.getLogger("node_wire_runtime.ingress")


def normalize_mcp_tool_arguments(
    connector: BaseConnector, action: str, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Apply action-registered argument normalizers (see SdkActionMeta.mcp_normalize).

    Used for MCP, REST, and gRPC so the same alias mapping applies across bindings.
    Mutates a copy of ``arguments`` and returns it.
    """
    args = dict(arguments)
    if not isinstance(connector, BaseConnector):
        return args
    meta = type(connector).sdk_action_metas().get(action)
    if meta is not None and meta.mcp_normalize is not None:
        meta.mcp_normalize(args)
    return args


def enforce_authoritative_action(payload: Dict[str, Any], authoritative_action: str) -> None:
    """
    Ensure the payload does not contradict the invoked tool or REST route.

    After :func:`normalize_mcp_tool_arguments`, the payload may contain a temporary
    ``action`` alias (e.g. ``upload``) that normalizers rewrite to the canonical
    action; those must match ``authoritative_action`` before the final assignment.

    :raises ValueError: if ``action`` is present and differs from ``authoritative_action``.
    """
    if "action" not in payload:
        return
    raw = payload.get("action")
    if raw is None:
        return
    if isinstance(raw, str) and not raw.strip():
        return
    current = str(raw).strip() if isinstance(raw, str) else str(raw)
    if current != authoritative_action:
        raise ValueError(
            f"Payload 'action' {raw!r} does not match the invoked route or tool "
            f"action {authoritative_action!r}."
        )
