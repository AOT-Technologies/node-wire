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
    """Path to generated MCP host: ``nw-mcp-builder/out/<server>-mcp/``.

    ``<server>`` is the ``server.name`` of the connector's scope fixture, which is what the
    generator names the folder after; :func:`server_name` only until a fixture exists.
    """
    name = _fixture_server_name(node_wire_root, connector_id) or server_name(connector_id)
    return node_wire_root / "nw-mcp-builder" / "out" / f"{name}-mcp"


def _fixture_server_name(node_wire_root: Path, connector_id: str) -> str | None:
    import yaml

    fixture = node_wire_root / "nw-mcp-builder" / "fixtures" / f"{connector_id}_nw.yaml"
    try:
        doc = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    server = doc.get("server") if isinstance(doc, dict) else None
    name = server.get("name") if isinstance(server, dict) else None
    return str(name) if name else None


def docker_image_tag(connector_id: str, tag: str = "latest") -> str:
    """Docker image name: ``<server>-mcp:<tag>``."""
    return f"{server_name(connector_id)}-mcp:{tag}"
