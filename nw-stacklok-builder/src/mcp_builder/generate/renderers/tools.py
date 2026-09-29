"""Render tool methods for the generated MCP server.

Pipeline stage: rendering (ServerPlan -> source code string).

Generates a Tools class with one async method per tool. Each method
has flattened parameters (not Pydantic models) so FastMCP exposes a
clean per-parameter input schema to LLMs.

Template: renderers/templates/tools.py.jinja2
"""

from __future__ import annotations

from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape

from mcp_builder.generate.plan import ParamPlan, ServerPlan, ToolPlan
from mcp_builder.generate.renderers.escape import escape_python_string

# node-wire: tool bodies that run a connector action (nw-stacklok-builder/UPSTREAM.md).
from nw_stacklok.render import arguments_expr

logger = structlog.get_logger()

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def render_tools_module(plan: ServerPlan) -> str:
    """Generate the tools.py module with a Tools class for the output project.

    Pipeline stage: rendering (plan -> source code).

    Each tool in plan.tools becomes an async method on the Tools class.
    Method args are flattened from path/query params and body fields so
    FastMCP can introspect them for the tool's input schema.

    Args:
        plan: The server plan containing tool definitions.

    Returns:
        Python source code string for the tools module.
    """
    logger.info("rendering tools module", tool_count=len(plan.tools))
    env = Environment(
        # Escape only HTML/XML templates; these render Python source, which
        # HTML-escaping would corrupt.
        autoescape=select_autoescape(),
        loader=FileSystemLoader(_TEMPLATES_DIR),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["py_string"] = escape_python_string
    template = env.get_template("tools.py.jinja2")

    tools_context = [_build_tool_context(t) for t in plan.tools]
    any_binary = any(t["response_kind"] == "binary" for t in tools_context)

    result = template.render(
        module_name=plan.module_name,
        server_name=plan.server_name,
        tools=tools_context,
        any_binary=any_binary,
        any_node_wire=any(t["nw_action"] for t in tools_context),
    )
    logger.debug("tools module rendered", chars=len(result))
    return result


def _build_tool_context(tool: ToolPlan) -> dict[str, object]:
    """Pre-compute all template values for one tool method.

    Separates params into required/optional for signature ordering,
    and builds Python expression strings for the path, query dict,
    and body dict.
    """
    all_params = tool.path_params + tool.query_params + tool.body_fields
    required = [p for p in all_params if p.required]
    optional = [p for p in all_params if not p.required]
    ordered = required + optional

    logger.debug(
        "building tool context",
        tool_name=tool.tool_name,
        http_method=tool.http_method,
        path=tool.path,
        required_params=len(required),
        optional_params=len(optional),
    )

    signature = _build_signature(ordered)
    path_expr = _build_path_expr(tool.path, tool.path_params)
    params_expr = _build_dict_expr(tool.query_params)
    body_expr = _build_dict_expr(tool.body_fields)

    return {
        "method_name": tool.tool_name,
        "signature": signature,
        "description": tool.description,
        "http_method": tool.http_method,
        "path_expr": path_expr,
        "params_expr": params_expr,
        "body_expr": body_expr,
        "hints": tool.hints,
        "response_kind": tool.response_kind,
        "nw_action": tool.nw_action,
        "nw_arguments_expr": arguments_expr(ordered) if tool.nw_action else "",
    }


def _build_signature(params: list[ParamPlan]) -> str:
    """Build the method signature fragment after ``self``.

    Each parameter uses ``Annotated[type, Field(description=...)]`` so
    FastMCP can expose per-parameter descriptions in the tool's input
    schema. Required params appear first, optional params get
    ``| None = None``.

    Example:
        >>> _build_signature([required_param, optional_param])
        ', item_id: Annotated[str, Field(description="...")], color: Annotated[str | None, Field(description="...")] = None'
    """
    parts: list[str] = []
    for p in params:
        desc = escape_python_string(p.description) if p.description else ""
        if p.required:
            parts.append(
                f', {p.py_name}: Annotated[{p.py_type}, Field(description="{desc}")]'
            )
        else:
            parts.append(
                f', {p.py_name}: Annotated[{p.py_type} | None, Field(description="{desc}")] = None'
            )
    return "".join(parts)


def _build_path_expr(path: str, path_params: list[ParamPlan]) -> str:
    """Build the path argument as a Python expression string.

    Converts OpenAPI path templates (``/items/{itemId}``) to Python
    f-strings (``f"/items/{item_id}"``). Returns a plain string literal
    when there are no path parameters. Escapes literal portions of the
    path (quotes, backslashes) in both branches.
    """
    if not path_params:
        return f'"{escape_python_string(path)}"'
    # Escape literal portions first, then substitute param placeholders.
    # The placeholders use {originalName} which won't be affected by
    # escape_python_string (it only escapes \, ", and \n).
    result = escape_python_string(path)
    for p in path_params:
        result = result.replace(f"{{{p.original_name}}}", f"{{{p.py_name}}}")
    return f'f"{result}"'


def _build_dict_expr(params: list[ParamPlan]) -> str:
    """Build a dict literal mapping original API names to Python variable names.

    Returns empty string when there are no params (the template uses this
    to conditionally omit the argument).

    Example:
        >>> _build_dict_expr([param_with_name_page_size])
        '{"page-size": page_size}'
    """
    if not params:
        return ""
    pairs = [f'"{escape_python_string(p.original_name)}": {p.py_name}' for p in params]
    return "{" + ", ".join(pairs) + "}"
