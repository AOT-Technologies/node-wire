# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""The node-wire hooks ``mcp_builder.pipeline.run_pipeline`` calls in node-wire mode."""

from __future__ import annotations

from collections.abc import Mapping
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
    # Distribution name -> the py3-none-any wheel the build stage resolved for it (one per
    # ``nw_stacklok.project.distributions``). Copied into the project and pinned in
    # [tool.uv.sources]; required, because node-wire-toolhive and node-wire-bindings are not
    # published to PyPI, so a server without them cannot be installed.
    wheels: Mapping[str, Path]
    # Run `uv lock` in the generated project (last) so the image builds with `uv sync --frozen`.
    lock: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.wheels, Mapping):
            raise ValueError(
                "node-wire mode needs the node-wire wheels (wheels=<name -> wheel path>): "
                "node-wire-toolhive and node-wire-bindings are not published to PyPI"
            )


def attach(plan: ServerPlan, scope: MCPScope, options: NodeWireOptions | None) -> ServerPlan:
    """Bind the plan to the connector, after checking everything finishing will need.

    Runs before anything is scaffolded, so a missing wheel or config entry writes nothing.
    """
    if scope.runtime is None:
        if options is not None:
            raise ValueError("node-wire options given, but the scope has no `runtime:` block")
        return plan
    if options is None:
        raise ValueError(
            "The scope's `runtime: node_wire` block needs a node-wire checkout; "
            "generate it with `nw gen-stacklok`."
        )
    root = options.node_wire_root.resolve()
    connector_id = scope.runtime.connector_id
    _project.check_wheels(root, connector_id, options.wheels)
    _project.connector_entry(root, connector_id)
    bound = _resolve.attach(plan, scope.runtime, root)
    check_node_wire_plan(bound)
    return bound


def check_node_wire_plan(plan: ServerPlan) -> None:
    """The invariant behind the plan's optional node-wire fields.

    ``plan.node_wire`` set means every tool has ``nw_action`` and every parameter ``nw_field``;
    unset means none do. The renderers branch on these fields one by one, so a half-bound plan
    would render a server whose tools mix node-wire calls and direct HTTP calls.
    """
    bound = plan.node_wire is not None
    for tool in plan.tools:
        params = [*tool.path_params, *tool.query_params, *tool.body_fields]
        unbound = tool.nw_action is None or any(p.nw_field is None for p in params)
        any_bound = tool.nw_action is not None or any(p.nw_field is not None for p in params)
        assert not (bound and unbound) and (bound or not any_bound), (
            f"tool {tool.tool_name!r} is not consistently bound to the node-wire connector"
        )


def client_module(plan: ServerPlan) -> str:
    return _render.client_module(plan)


def manifests(plan: ServerPlan) -> Dict[str, str]:
    return _manifests.render(plan)


def finish(project_dir: Path, plan: ServerPlan, options: NodeWireOptions) -> None:
    _project.finish_project(project_dir, plan, wheels=options.wheels, lock=options.lock)
