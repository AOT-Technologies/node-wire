# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Naming helpers for MCP server / project directories."""

from __future__ import annotations

from pathlib import Path


def server_name(connector_id: str) -> str:
    """Map connector id to MCP server name (``google_drive`` → ``google-drive-nw``)."""
    return connector_id.replace("_", "-") + "-nw"


def mcp_project_dir(node_wire_root: Path, connector_id: str) -> Path:
    """Path to generated MCP host: ``nw-mcp-builder/out/<server>-mcp/``."""
    return node_wire_root / "nw-mcp-builder" / "out" / f"{server_name(connector_id)}-mcp"


def docker_image_tag(connector_id: str, tag: str = "latest") -> str:
    """Docker image name: ``<server>-mcp:<tag>``."""
    return f"{server_name(connector_id)}-mcp:{tag}"
