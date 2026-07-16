"""Connector-mode orchestration (no OpenAPI path)."""

from __future__ import annotations

import logging
from pathlib import Path

from nw_mcp_builder.generate.connector_project import write_connector_project
from nw_mcp_builder.schema.models import load_scope

logger = logging.getLogger(__name__)


def run_connector_pipeline(
    scope_yaml: Path,
    node_wire_root: Path,
    output_dir: Path,
) -> Path:
    """Generate a thin host project for a node-wire connector."""
    logger.info("Loading connector scope from %s", scope_yaml)
    scope = load_scope(scope_yaml)
    if scope.runtime is None or scope.runtime.type != "node_wire":
        raise ValueError(
            f"Scope '{scope_yaml}' is not connector mode "
            "(set runtime.type: node_wire and runtime.connector_id)."
        )

    logger.info(
        "Scaffolding connector host for '%s' (connector_id=%s)",
        scope.server.name,
        scope.runtime.connector_id,
    )
    project_dir = write_connector_project(scope, node_wire_root, output_dir)
    logger.info("Generated connector project at %s", project_dir)
    return project_dir
