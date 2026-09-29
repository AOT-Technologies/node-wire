# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Bind a stacklok plan to a node-wire connector: endpoint → action, parameter → input field.

Runs after stacklok's own ``build_server_plan``; everything it adds lives in the plan's optional
node-wire fields, so stacklok's renderers keep working on the same plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from mcp_builder.generate.plan import ParamPlan, ServerPlan, ToolPlan
from nw_stacklok.connector import ActionField, ConnectorInfo, read_connector
from nw_stacklok.scope import NodeWirePlan, NodeWireRuntime

BODY_PREFIX = "body."


class NodeWireResolveError(ValueError):
    """The scope names endpoints or parameters the node-wire connector cannot serve."""

    def __init__(self, problems: List[str]) -> None:
        self.problems = problems
        super().__init__(
            "Scope does not match the node-wire connector:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )


def attach(plan: ServerPlan, runtime: NodeWireRuntime, node_wire_root: Path) -> ServerPlan:
    """Return ``plan`` with every tool bound to a connector action and field."""
    info = read_connector(node_wire_root, runtime.connector_id)
    problems: List[str] = []
    tools = [_bind_tool(tool, info, problems) for tool in plan.tools]
    if problems:
        raise NodeWireResolveError(problems)
    return plan.model_copy(
        update={
            "tools": tools,
            "node_wire": NodeWirePlan(
                connector_id=runtime.connector_id, node_wire_root=str(node_wire_root)
            ),
        }
    )


def _bind_tool(tool: ToolPlan, info: ConnectorInfo, problems: List[str]) -> ToolPlan:
    key = (tool.http_method.upper(), tool.path)
    action = info.endpoints.get(key)
    if action is None:
        reason = info.skipped.get(key)
        problems.append(
            f"{tool.tool_name}: {key[0]} {key[1]} "
            + (
                f"was skipped by nw-connector-builder ({reason})"
                if reason is not None
                else f"is not an operation of connector {info.connector_id!r}"
            )
        )
        return tool
    fields = info.fields.get(action, [])
    by_location: Dict[tuple[str, str], ActionField] = {(f.location, f.wire_name): f for f in fields}
    whole_body = next((f for f in fields if f.location == "body"), None)

    bound: Dict[str, List[ParamPlan]] = {}
    covered: set[str] = set()
    for group_name, params in (
        ("path_params", tool.path_params),
        ("query_params", tool.query_params),
        ("body_fields", tool.body_fields),
    ):
        out: List[ParamPlan] = []
        for param in params:
            target = _field_for(param, by_location, whole_body)
            if target is None:
                problems.append(
                    f"{tool.tool_name}: parameter {param.original_name!r} ({param.location}) "
                    f"is not an input of action {action!r}"
                )
                out.append(param)
                continue
            covered.add(target.split(".", 1)[0])
            out.append(param.model_copy(update={"nw_field": target}))
        bound[group_name] = out

    for f in fields:
        if f.required and f.field_name not in covered and f.location != "body":
            problems.append(
                f"{tool.tool_name}: action {action!r} requires {f.wire_name!r} ({f.location}), "
                "which the scope does not list"
            )
    if whole_body is not None and whole_body.required and "body" not in covered:
        problems.append(f"{tool.tool_name}: action {action!r} requires a request body")
    return tool.model_copy(update={**bound, "nw_action": action})


def _field_for(
    param: ParamPlan,
    by_location: Dict[tuple[str, str], ActionField],
    whole_body: ActionField | None,
) -> str | None:
    location = str(param.location)
    if location == "body":
        flat = by_location.get(("body_property", param.original_name))
        if flat is not None:
            return flat.field_name
        if whole_body is not None:
            return f"{BODY_PREFIX}{param.original_name}"
        return None
    target = by_location.get((location, param.original_name))
    return target.field_name if target is not None else None
