"""ServerPlan: the typed intermediate representation for code generation.

Pipeline stage: planning (MCPScope + OpenAPI → ServerPlan).
This module defines the data contract between spec analysis and rendering.
All raw OpenAPI access happens here in build_server_plan(). Downstream
renderers only receive the ServerPlan and never touch the spec.

Terminology (OpenAPI → plan mapping):
    - Parameters: path/query args from the URL (e.g., /items/{id}?fields=name).
      Defined in the operation's ``parameters`` array.
    - Body fields: properties of the JSON request body schema
      (e.g., {"color": "red"}). Defined under ``requestBody.content``.
    - Both are flattened into ParamPlan objects with a ``location`` tag
      ("path", "query", or "body") so renderers don't need to know the
      OpenAPI distinction.

Reading guide:
    - ServerPlan is the root — it contains everything needed to generate
      an entire MCP server project.
    - build_server_plan() is the only public function — it transforms
      scope + spec into a ServerPlan.
    - Renderers under mcp_builder.generate.renderers/ consume the plan
      (e.g., render_client_module(plan: ServerPlan) -> str).

Parameter semantics:
    Every tool's ``parameters`` list is an **allowlist**: only the listed
    parameters are included in the generated tool. Each parameter declares
    its ``location`` (path, query, or body). Parameters present in the
    OpenAPI spec but absent from the YAML are excluded. Path parameters
    are validated at load time — the YAML must declare every ``{placeholder}``
    in the endpoint path.
"""

from __future__ import annotations

import keyword
import re
from typing import Annotated, Literal

import structlog

from pydantic import BaseModel, Field

from mcp_builder.spec import (
    OPENAPI_TYPE_MAP,
    OpenAPISpec,
    PythonType,
    get_body_fields,
    get_parameters,
    parse_endpoint,
)
# node-wire: optional binding to a node-wire connector (nw-stacklok-builder/UPSTREAM.md).
from nw_stacklok.scope import NodeWirePlan

from mcp_builder.schema.models import (
    APIKeyAuth,
    MCPScope,
    NoAuth,
    OAuth2Auth,
    OIDCAuth,
    ParamLocation,
    Parameter,
    Tool,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Plan models — the data contract between analysis and rendering
# ---------------------------------------------------------------------------


class ParamPlan(BaseModel):
    """A single parameter in a generated tool method.

    Represents a flattened argument that will appear in the tool's function
    signature. Can originate from path params, query params, or body fields.

    Example:
        ParamPlan(
            name="itemId", py_name="item_id", py_type="str",
            description="The ID of the item.", required=True,
            location="path", original_name="itemId"
        )
    """

    name: str  # original OpenAPI name
    py_name: str  # sanitized Python identifier (e.g., hyphens → underscores)
    py_type: PythonType
    description: str
    required: bool
    location: ParamLocation
    original_name: str  # preserved for path template substitution
    nw_field: str | None = None  # node-wire: connector input field it binds to


class ToolPlan(BaseModel):
    """Everything needed to render one tool method and its parameter model.

    ``response_kind`` is copied verbatim from the scope YAML's
    ``Tool.response_kind``. ``"json"`` emits a tool whose return type is
    ``dict`` (the client parses the body as JSON); ``"text"`` emits a tool
    whose return type is ``str`` (the client returns the body decoded as
    text via the response's declared charset, falling back to UTF-8);
    ``"binary"`` emits a tool whose return type is ``str`` (the client
    returns the raw bytes base64-encoded for MCP transport); ``"auto"``
    emits a tool whose return type is ``dict | str`` (the client inspects
    the response's ``Content-Type`` header at request time and decodes
    accordingly: JSON-y types → dict, ``text/*`` → str, anything else →
    base64-encoded str).

    Example:
        ToolPlan(
            tool_name="get_item", class_name="GetItem",
            http_method="GET", path="/items/{itemId}",
            description="Get an item by ID.",
            path_params=[ParamPlan(name="itemId", ...)],
            query_params=[ParamPlan(name="fields", ...)],
            body_fields=[],
            hints=["response has 50+ fields"],
            group_name="item-operations",
            response_kind="json",
        )
    """

    tool_name: str  # snake_case, e.g. "get_item"
    class_name: str  # PascalCase, e.g. "GetItem"
    http_method: str  # e.g. "GET"
    path: str  # e.g. "/items/{itemId}"
    description: str
    path_params: list[ParamPlan]
    query_params: list[ParamPlan]
    body_fields: list[ParamPlan]
    hints: list[str]
    group_name: str
    response_kind: Literal["json", "text", "binary", "auto"]
    nw_action: str | None = None  # node-wire: connector action it runs


class GroupPlan(BaseModel):
    """A semantic group of tools, used for organizing tool registrations.

    Example:
        GroupPlan(name="file-operations", tool_names=["list_files", "get_file"])
    """

    name: str
    tool_names: list[str]


# Discriminated union of the four auth variants, keyed on the ``type``
# literal. Structurally identical to ``schema.models.AuthConfig`` — the
# plan layer carries the scope's auth instance through unchanged so that
# renderers can dispatch with ``isinstance(plan.auth, OAuth2Auth)`` etc.
# and get full type narrowing on variant-specific fields.
AuthPlan = Annotated[
    OAuth2Auth | OIDCAuth | APIKeyAuth | NoAuth,
    Field(discriminator="type"),
]


class ServerPlan(BaseModel):
    """The complete intermediate representation for generating one MCP server.

    Output of ``build_server_plan()``; consumed by every renderer under
    ``mcp_builder.generate.renderers`` and by ``scaffold`` + ``patches``.

    This is the single data contract between "understanding the spec" and
    "generating code." A reviewer can inspect this model to understand
    exactly what data is available to each renderer.

    Example:
        ServerPlan(
            module_name="google_drive_mcp",
            server_name="google-drive",
            description="Google Drive MCP server",
            base_url="https://www.googleapis.com/drive/v3",
            auth=OIDCAuth(type="oidc", issuer="https://accounts.google.com", ...),
            tools=[ToolPlan(tool_name="list_files", ...), ...],
            groups=[GroupPlan(name="file-operations", ...), ...],
        )
    """

    module_name: str  # Python module name, e.g. "google_drive_mcp"
    server_name: str  # DNS label, e.g. "google-drive"
    description: str
    base_url: str
    auth: AuthPlan
    tools: list[ToolPlan]
    groups: list[GroupPlan]
    node_wire: NodeWirePlan | None = None  # node-wire: set when scope.runtime is node_wire


# ---------------------------------------------------------------------------
# Plan builder — the only function that touches the raw OpenAPI spec
# ---------------------------------------------------------------------------


def build_server_plan(scope: MCPScope, spec: OpenAPISpec) -> ServerPlan:
    """Build a ServerPlan from a validated scope and typed OpenAPI spec.

    Pipeline stage: planning (scope + spec → ServerPlan).

    This is the only place in the codebase that reads from both the MCPScope
    and the OpenAPI spec. After this function returns, all downstream code
    works exclusively with the ServerPlan.

    Args:
        scope: Validated mcp-scope.yaml content.
        spec: Typed OpenAPI spec (3.0 or 3.1).

    Returns:
        A fully populated ServerPlan ready for rendering.
    """
    module_name = server_name_to_module(scope.server.name)
    logger.info(
        "building server plan",
        server_name=scope.server.name,
        module_name=module_name,
    )

    tools: list[ToolPlan] = []
    groups: list[GroupPlan] = []

    for group in scope.groups:
        logger.debug(
            "processing group", group_name=group.name, tool_count=len(group.tools)
        )
        group_tool_names: list[str] = []
        for tool in group.tools:
            tool_plan = _build_tool_plan(tool, spec, group.name)
            tools.append(tool_plan)
            group_tool_names.append(tool.tool_name)
            logger.debug(
                "built tool plan",
                tool_name=tool.tool_name,
                path_params=len(tool_plan.path_params),
                query_params=len(tool_plan.query_params),
                body_fields=len(tool_plan.body_fields),
            )
        groups.append(GroupPlan(name=group.name, tool_names=group_tool_names))

    auth = _build_auth_plan(scope)

    logger.info("plan complete", tool_count=len(tools), group_count=len(groups))

    return ServerPlan(
        module_name=module_name,
        server_name=scope.server.name,
        description=scope.server.description,
        base_url=scope.spec.base_url,
        auth=auth,
        tools=tools,
        groups=groups,
    )


def _build_tool_plan(tool: Tool, spec: OpenAPISpec, group_name: str) -> ToolPlan:
    """Build a ToolPlan for a single tool definition.

    YAML parameters are the source of truth: each declares its location
    (path, query, body) explicitly. The spec is only consulted for type
    enrichment — params not found in the spec default to ``str``.

    Path param completeness is enforced at YAML load time by
    ``Tool.validate_path_params_declared``, so no fallback logic is needed here.
    """
    tool_name = tool.tool_name
    method, path = parse_endpoint(tool.endpoint)
    logger.debug("building tool plan", tool_name=tool_name, endpoint=tool.endpoint)

    # Spec type lookup maps — YAML provides name/description/required/location,
    # spec provides Python types.  Params not in the spec default to str.
    spec_params = get_parameters(spec, method, path)
    spec_body = get_body_fields(spec, method, path)
    spec_param_types: dict[str, PythonType] = {
        p.name: OPENAPI_TYPE_MAP[p.schema_type] for p in spec_params
    }
    spec_body_types: dict[str, PythonType] = {
        f.name: OPENAPI_TYPE_MAP[f.schema_type] for f in spec_body
    }

    # Group YAML params by location and build plans.
    yaml_path = [p for p in tool.parameters if p.location == ParamLocation.PATH]
    yaml_query = [p for p in tool.parameters if p.location == ParamLocation.QUERY]
    yaml_body = [p for p in tool.parameters if p.location == ParamLocation.BODY]

    path_params = _build_param_plans(yaml_path, spec_param_types, ParamLocation.PATH)
    query_params = _build_param_plans(yaml_query, spec_param_types, ParamLocation.QUERY)
    body_fields = _build_param_plans(yaml_body, spec_body_types, ParamLocation.BODY)

    # Detect and resolve name collisions across all param locations
    all_params = path_params + query_params + body_fields
    _resolve_name_collisions(all_params)

    return ToolPlan(
        tool_name=tool_name,
        class_name=_tool_name_to_class(tool_name),
        http_method=method,
        path=path,
        description=tool.description,
        path_params=path_params,
        query_params=query_params,
        body_fields=body_fields,
        hints=tool.hints or [],
        group_name=group_name,
        response_kind=tool.response_kind,
    )


def _build_param_plans(
    params: list[Parameter],
    spec_types: dict[str, PythonType],
    location: ParamLocation,
) -> list[ParamPlan]:
    """Build ParamPlan objects from YAML parameters, enriched by spec types.

    YAML provides name, description, required, and location.  The spec
    provides the Python type (via ``spec_types`` lookup); params not found
    in the spec default to ``str``.

    This single function handles all three locations (path, query, body)
    and both spec-backed and spec-missing parameters.
    """
    plans = []
    for param in params:
        py_name = _sanitize_name(param.name)
        in_spec = param.name in spec_types
        if in_spec:
            py_type = spec_types[param.name]
        else:
            # The OpenAPI spec has no type info for this parameter.
            # This typically means the spec is incomplete (e.g., a POST
            # endpoint with no requestBody defined).  We default to str
            # because we have no better information.  The validate_scope()
            # check warns about this at validation time so the user can
            # confirm the spec is genuinely incomplete.
            py_type = "str"
            logger.warning(
                "parameter not found in spec — defaulting to str",
                param=param.name,
                location=location,
                tool_hint="run 'mcp-builder validate' to see all missing params",
            )
        logger.debug(
            "param plan",
            name=param.name,
            py_name=py_name,
            py_type=py_type,
            location=location,
            required=param.required,
            from_spec=in_spec,
        )
        plans.append(
            ParamPlan(
                name=param.name,
                py_name=py_name,
                py_type=py_type,
                description=param.description,
                required=param.required,
                location=location,
                original_name=param.name,
            )
        )
    return plans


def _build_auth_plan(scope: MCPScope) -> AuthPlan:
    """Return the scope's auth variant (the plan uses the same union type)."""
    logger.debug("building auth plan", auth_type=scope.auth.type)
    return scope.auth


# ---------------------------------------------------------------------------
# Utilities — naming, sanitization, collision resolution
# ---------------------------------------------------------------------------


def server_name_to_module(name: str) -> str:
    """Convert a DNS-label server name to a Python module name.

    The generated MCP server project uses this as its Python package name
    (e.g., the directory under src/).

    Example:
        >>> server_name_to_module("google-drive")
        "google_drive_mcp"
    """
    return name.replace("-", "_") + "_mcp"


def _tool_name_to_class(name: str) -> str:
    """Convert a snake_case tool name to PascalCase.

    Example:
        >>> _tool_name_to_class("get_item")
        "GetItem"
    """
    return "".join(word.capitalize() for word in name.split("_"))


def _sanitize_name(name: str) -> str:
    """Convert an OpenAPI parameter name to a valid Python identifier.

    Replaces any non-alphanumeric character (hyphens, dots, dollar signs,
    brackets, etc.) with underscores, collapses runs of underscores, strips
    leading underscores, and prepends ``param_`` if the result starts with
    a digit or is empty. Also appends ``_`` to Python keywords.

    Example:
        >>> _sanitize_name("$filter")
        "filter"
        >>> _sanitize_name("page-size")
        "page_size"
        >>> _sanitize_name("from")
        "from_"
        >>> _sanitize_name("page[size]")
        "page_size"
    """
    # Replace any non-alphanumeric, non-underscore character with underscore
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    # Collapse runs of underscores and strip leading/trailing
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    if not sanitized or sanitized[0].isdigit():
        sanitized = "param_" + sanitized
    # Avoid Python keywords and method-reserved names: from -> from_, self -> self_
    if keyword.iskeyword(sanitized) or sanitized in ("self", "cls"):
        sanitized += "_"
    if sanitized != name:
        logger.debug("sanitized name", original=name, sanitized=sanitized)
    return sanitized


def _resolve_name_collisions(params: list[ParamPlan]) -> None:
    """Detect and resolve py_name collisions by suffixing with location.

    Mutates the params in-place. If two params have the same py_name
    (e.g., "id" from both path and query), they get suffixed:
    "id_path" and "id_query".

    Runs a second pass to catch collisions introduced by the first rename
    (e.g., "foo-bar" and "foo.bar" both become "foo_bar", then both become
    "foo_bar_query" if they're in the same location). In that case, appends
    a numeric suffix.
    """
    logger.debug("checking for name collisions", param_count=len(params))

    # First pass: suffix with location
    seen: dict[str, list[ParamPlan]] = {}
    for p in params:
        seen.setdefault(p.py_name, []).append(p)

    for py_name, group in seen.items():
        if len(group) > 1:
            logger.warning(
                "name collision detected",
                py_name=py_name,
                params=[(p.name, p.location) for p in group],
            )
            for p in group:
                old_name = p.py_name
                p.py_name = f"{py_name}_{p.location}"
                logger.debug(
                    "collision rename (location suffix)",
                    original=old_name,
                    renamed=p.py_name,
                    param_name=p.name,
                    location=p.location,
                )

    # Second pass: if location-suffixed names still collide, add numeric suffix
    seen2: dict[str, list[ParamPlan]] = {}
    for p in params:
        seen2.setdefault(p.py_name, []).append(p)

    for py_name, group in seen2.items():
        if len(group) > 1:
            logger.warning(
                "post-suffix collision",
                py_name=py_name,
                params=[(p.name, p.location) for p in group],
            )
            for i, p in enumerate(group):
                old_name = p.py_name
                p.py_name = f"{py_name}_{i + 1}" if i > 0 else py_name
                if p.py_name != old_name:
                    logger.debug(
                        "collision rename (numeric suffix)",
                        original=old_name,
                        renamed=p.py_name,
                    )

    collisions_found = any(len(g) > 1 for g in seen.values())
    if not collisions_found:
        logger.debug("no name collisions detected")
