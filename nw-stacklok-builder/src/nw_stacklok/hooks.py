# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""The node-wire hooks ``mcp_builder.pipeline.run_pipeline`` calls in node-wire mode."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from mcp_builder.generate.plan import ServerPlan
from mcp_builder.schema.models import MCPScope
from nw_stacklok import manifests as _manifests
from nw_stacklok import project as _project
from nw_stacklok import render as _render
from nw_stacklok import resolve as _resolve


@dataclass(frozen=True)
class NodeWireOptions:
    """How to build a node-wire-mode server (passed to ``run_pipeline(node_wire=...)``)."""

    node_wire_root: Path
    # Copy cp313 musllinux wheels into the project and pin them in [tool.uv.sources].
    wheels: bool = True
    # Run `uv lock` in the generated project so the image builds with `uv sync --frozen`.
    lock: bool = False


def attach(plan: ServerPlan, scope: MCPScope, options: NodeWireOptions | None) -> ServerPlan:
    if scope.runtime is None:
        if options is not None:
            raise ValueError("node-wire options given, but the scope has no `runtime:` block")
        return plan
    if options is None:
        raise ValueError(
            "The scope's `runtime: node_wire` block needs a node-wire checkout; "
            "generate it with `nw gen-stacklok`."
        )
    return _resolve.attach(plan, scope.runtime, options.node_wire_root.resolve())


def client_module(plan: ServerPlan) -> str:
    return _render.client_module(plan)


def manifests(plan: ServerPlan) -> Dict[str, str]:
    return _manifests.render(plan)


def finish(project_dir: Path, plan: ServerPlan, options: NodeWireOptions) -> None:
    _project.finish_project(project_dir, plan, wheels=options.wheels, lock=options.lock)
