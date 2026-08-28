# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Hand-off to nw-mcp-builder after a clean promote."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run_mcp_handoff(
    connector_id: str,
    *,
    node_wire_root: Path,
    force_output: bool,
) -> dict[str, Any]:
    try:
        from nw_mcp_builder.from_connector import run_from_connector
    except ImportError as exc:
        return {"ok": False, "error": f"nw-mcp-builder not importable: {exc}"}

    package_root = node_wire_root / "nw-mcp-builder"
    try:
        project = run_from_connector(
            connector_id,
            node_wire_root=node_wire_root,
            package_root=package_root,
            force_fixture=True,  # always — fixture must track newly promoted connector
            force_output=force_output,
        )
        return {"ok": True, "project": str(project)}
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        logger.exception("MCP hand-off failed")
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("MCP hand-off failed")
        return {"ok": False, "error": str(exc)}
