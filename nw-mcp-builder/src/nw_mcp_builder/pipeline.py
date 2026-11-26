# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Connector-mode orchestration (no OpenAPI path)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from nw_mcp_builder.generate.connector_project import write_connector_project
from nw_mcp_builder.output_dir import atomic_output_dir
from nw_mcp_builder.schema.models import load_scope

logger = logging.getLogger(__name__)


def run_connector_pipeline(
    scope_yaml: Path,
    node_wire_root: Path,
    output_dir: Path,
    *,
    wheels: tuple[Path, Path, Path],
    tool_mode: str = "list",
    replace: bool = False,
    finish: Callable[[Path], None] | None = None,
) -> Path:
    """Generate ``<output_dir>/<server.name>-mcp/`` for a node-wire connector scope.

    The project directory is named from the loaded scope's ``server.name``. It is written into a
    staging folder and moved into place only once complete (``finish`` runs last, on the staged
    copy); an existing project is replaced only with ``replace``, and only after that.
    """
    logger.info("Loading connector scope from %s", scope_yaml)
    scope = load_scope(scope_yaml)
    if scope.runtime is None or scope.runtime.type != "node_wire":
        raise ValueError(
            f"Scope '{scope_yaml}' is not connector mode "
            "(set runtime.type: node_wire and runtime.connector_id)."
        )

    final = output_dir / f"{scope.server.name}-mcp"
    logger.info(
        "Scaffolding connector host for '%s' (connector_id=%s)",
        scope.server.name,
        scope.runtime.connector_id,
    )
    with atomic_output_dir(final, replace=replace) as staging:
        staged = write_connector_project(
            scope, node_wire_root, staging / final.name, wheels=wheels, tool_mode=tool_mode
        )
        if finish is not None:
            finish(staged)
    logger.info("Generated connector project at %s", final)
    return final
