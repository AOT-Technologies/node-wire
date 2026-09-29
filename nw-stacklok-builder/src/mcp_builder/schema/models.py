"""Pydantic v2 models for mcp-scope.yaml validation."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# node-wire: optional runtime block (nw-stacklok-builder/UPSTREAM.md).
from nw_stacklok.scope import NodeWireRuntime

logger = structlog.get_logger()


class ParamLocation(StrEnum):
    """Where a parameter belongs in the HTTP request."""

    PATH = "path"
    QUERY = "query"
    BODY = "body"


class Parameter(BaseModel):
    """A parameter entry for a tool in mcp-scope.yaml.

    When a tool defines a ``parameters`` list, those entries act as an
    **allowlist**: only the listed parameters are included in the generated
    MCP tool. The ``description`` and ``required`` fields override the
    corresponding values from the OpenAPI spec.

    The ``location`` field tells codegen where this parameter belongs:
    - ``path``: URL template slot (e.g., ``/items/{itemId}``)
    - ``query``: URL query parameter (e.g., ``?fields=name``)
    - ``body``: JSON request body property (e.g., ``{"color": "red"}``)
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    required: bool
    location: ParamLocation


class SpecConfig(BaseModel):
    """OpenAPI spec metadata."""

    model_config = ConfigDict(extra="forbid")

    source: str
    format: Literal["openapi3", "openapi3.1"]
    base_url: str
    total_endpoints: int | None = None
    scoped_endpoints: int | None = None


class ServerConfig(BaseModel):
    """Server identity metadata."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str

    @field_validator("name")
    @classmethod
    def validate_dns_label(cls, v: str) -> str:
        """Enforce RFC 1123 DNS-label rules (K8s name, image tag, URL segment all need this).

        ``google-drive`` ok; ``Google_Drive`` and ``-drive`` fail.
        """
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", v) or len(v) > 63:
            raise ValueError(
                f"Server name '{v}' is not a valid DNS label. "
                "Must contain only lowercase letters, digits, and hyphens, "
                "must start and end with a letter or digit, and be at most 63 characters."
            )
        return v


class Tool(BaseModel):
    """A single tool = one MCP tool = one HTTP call into the upstream API.

    ``endpoint`` (``"METHOD /path"``) is what codegen resolves against the
    OpenAPI spec. ``hints`` are free-form notes from scoping that Phase 4
    uses to suggest polish (pagination, response shaping, quirks).

    ``response_kind`` declares how the generated tool decodes the response
    body: ``"json"`` returns a ``dict``, ``"text"`` returns decoded text
    as a ``str``, ``"binary"`` returns a base64-encoded ``str``, and
    ``"auto"`` decides at runtime from the response's ``Content-Type``
    header (returns ``dict | str``). Use ``auto`` only when the operation's
    response shape varies based on request inputs — Google Drive
    ``files.export`` is the canonical example: a ``mimeType`` query
    parameter chooses between text and binary outputs. Prefer a fixed kind
    whenever the spec commits to one, since ``auto`` widens the tool's
    return type and weakens the LLM caller's input schema.

    It is required at scoping time so the decision is explicit in the
    scope YAML and cannot be silently inferred from an ambiguous spec. See
    ``skills/ai-scoping/assets/generator-contract.md`` for the full
    contract each kind commits to.

    Example:

        - tool_name: list_drive_files
          endpoint: GET /files
          description: List files visible to the authenticated user.
          response_kind: json
          parameters:
            - name: q
              description: Drive query string.
              required: false
              location: query
          hints:
            - "Paginated via nextPageToken; 100 items default."
    """

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    endpoint: str
    description: str
    response_kind: Literal["json", "text", "binary", "auto"]
    parameters: list[Parameter] = Field(default_factory=list)
    hints: list[str] | None = None

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, v: str) -> str:
        """snake_case, max 40 chars.

        The cap is practical: longer names crowd the LLM's tool-selection
        context and usually mean the name wasn't curated.
        """
        if len(v) > 40:
            raise ValueError(f"Tool name '{v}' exceeds 40 characters ({len(v)} chars)")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", v):
            raise ValueError(
                f"Tool name '{v}' must be snake_case: lowercase letters, digits, "
                "and underscores only, starting with a letter."
            )
        return v

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        """Enforce ``"METHOD /path"`` shape.

        Codegen splits on the first space to look the operation up in the
        spec; anything that doesn't parse breaks that lookup. Method
        allowlist is what the generator can emit HTTP calls for.
        """
        if not re.fullmatch(r"(GET|POST|PUT|PATCH|DELETE) /\S+", v):
            raise ValueError(
                f"Endpoint '{v}' must match 'METHOD /path' where METHOD is "
                "one of GET, POST, PUT, PATCH, DELETE."
            )
        return v

    @model_validator(mode="after")
    def validate_path_params_declared(self) -> Self:
        """Every ``{placeholder}`` in the endpoint path must appear in ``parameters`` with ``location=path``.

        Otherwise the generated client would have no way to fill the URL
        template — catch it at load time, not request time.
        """
        _, path = self.endpoint.split(" ", 1)
        placeholders = set(re.findall(r"\{(\w+)\}", path))
        if not placeholders:
            return self
        declared = {p.name for p in self.parameters if p.location == ParamLocation.PATH}
        missing = placeholders - declared
        if missing:
            raise ValueError(
                f"Endpoint '{self.endpoint}' has path parameters {missing} "
                f"that are not declared in parameters with location='path'."
            )
        return self


class Group(BaseModel):
    """A semantic grouping of related tools."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    tools: list[Tool] = Field(min_length=1)


class OAuth2Auth(BaseModel):
    """OAuth2 auth variant — matches OpenAPI's oauth2 security scheme shape.

    Fields mirror what OpenAPI's ``flows.<flow>`` block provides, plus an
    optional ``userinfo_url`` (vendor-specific; not standardized in OpenAPI).
    Endpoint URLs are absolute: relative paths from the spec are resolved
    against ``spec.base_url`` during scoping.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["oauth2"]
    flow: Literal["authorizationCode"]
    authorization_url: str
    token_url: str
    userinfo_url: str | None = None
    scopes_available: dict[str, str] = Field(default_factory=dict)
    scopes_required: list[str] = Field(default_factory=list)
    notes: str | None = None


class OIDCAuth(BaseModel):
    """OIDC auth variant — carries the issuer; endpoints come from discovery.

    For OIDC, the upstream publishes a ``/.well-known/openid-configuration``
    that lists ``authorization_endpoint``, ``token_endpoint``, and
    ``userinfo_endpoint``. We don't duplicate those here — ``deploy-assist``
    reads them at deploy time.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["oidc"]
    issuer: str
    scopes_available: dict[str, str] = Field(default_factory=dict)
    scopes_required: list[str] = Field(default_factory=list)
    notes: str | None = None


class APIKeyAuth(BaseModel):
    """Static bearer-token auth — API key in ``Authorization`` header."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["api_key"]
    notes: str | None = None


class NoAuth(BaseModel):
    """No auth — the upstream API is public or auth is out of scope."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["none"]
    notes: str | None = None


AuthConfig = Annotated[
    OAuth2Auth | OIDCAuth | APIKeyAuth | NoAuth,
    Field(discriminator="type"),
]


class MCPScope(BaseModel):
    """Root model for mcp-scope.yaml."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["1"]
    server: ServerConfig
    spec: SpecConfig
    workflows: list[str] | None = None
    groups: list[Group] = Field(min_length=1)
    auth: AuthConfig
    # node-wire: run the tools on a node-wire connector instead of an HTTP client.
    runtime: NodeWireRuntime | None = None

    @model_validator(mode="after")
    def validate_unique_tool_names(self) -> Self:
        """``tool_name`` must be unique across all groups.

        Groups are a scoping aid, not a namespace — the server exposes
        one flat tool list. The error names both offending groups.
        """
        seen: dict[str, str] = {}
        for group in self.groups:
            for tool in group.tools:
                if tool.tool_name in seen:
                    raise ValueError(
                        f"Duplicate tool_name '{tool.tool_name}' found in groups "
                        f"'{seen[tool.tool_name]}' and '{group.name}'"
                    )
                seen[tool.tool_name] = group.name
        return self


def load_scope(path: str | Path) -> MCPScope:
    """Load and validate an mcp-scope.yaml file.

    Args:
        path: Path to the YAML file.

    Returns:
        Validated MCPScope instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
        pydantic.ValidationError: If the YAML content fails schema validation.
    """
    path = Path(path)
    logger.info("loading scope", path=str(path))
    with path.open() as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ValueError(f"File '{path}' is empty or contains only comments")
    scope = MCPScope.model_validate(raw)
    tool_count = sum(len(g.tools) for g in scope.groups)
    logger.info(
        "scope loaded",
        server_name=scope.server.name,
        group_count=len(scope.groups),
        tool_count=tool_count,
        auth_type=scope.auth.type,
    )
    return scope
