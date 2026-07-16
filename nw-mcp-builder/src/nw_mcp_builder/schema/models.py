# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for connector-mode mcp-scope.yaml validation.

ponytail: kept the same shape as mcp-builder so hand-written fixtures
(salesforce_nw / fhir_epic_nw style) still validate; OpenAPI codegen
fields are metadata-only here.
"""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class ParamLocation(StrEnum):
    PATH = "path"
    QUERY = "query"
    BODY = "body"


class Parameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    required: bool
    location: ParamLocation


class SpecConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    format: Literal["openapi3", "openapi3.1"]
    base_url: str
    total_endpoints: int | None = None
    scoped_endpoints: int | None = None


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str

    @field_validator("name")
    @classmethod
    def validate_dns_label(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", v) or len(v) > 63:
            raise ValueError(
                f"Server name '{v}' is not a valid DNS label. "
                "Must contain only lowercase letters, digits, and hyphens, "
                "must start and end with a letter or digit, and be at most 63 characters."
            )
        return v


class Tool(BaseModel):
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
        if not re.fullmatch(r"(GET|POST|PUT|PATCH|DELETE) /\S+", v):
            raise ValueError(
                f"Endpoint '{v}' must match 'METHOD /path' where METHOD is "
                "one of GET, POST, PUT, PATCH, DELETE."
            )
        return v

    @model_validator(mode="after")
    def validate_path_params_declared(self) -> Self:
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
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    tools: list[Tool] = Field(min_length=1)


class OAuth2Auth(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    type: Literal["oidc"]
    issuer: str
    scopes_available: dict[str, str] = Field(default_factory=dict)
    scopes_required: list[str] = Field(default_factory=list)
    notes: str | None = None


class APIKeyAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["api_key"]
    notes: str | None = None


class NoAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["none"]
    notes: str | None = None


AuthConfig = Annotated[
    OAuth2Auth | OIDCAuth | APIKeyAuth | NoAuth,
    Field(discriminator="type"),
]


class RuntimeConfig(BaseModel):
    """When set, generate emits a thin node-wire McpServer host."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["node_wire"]
    connector_id: str


class MCPScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1"]
    server: ServerConfig
    spec: SpecConfig
    workflows: list[str] | None = None
    groups: list[Group] = Field(min_length=1)
    auth: AuthConfig
    runtime: RuntimeConfig | None = None

    @model_validator(mode="after")
    def validate_unique_tool_names(self) -> Self:
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
    """Load and validate a connector-mode scope YAML."""
    path = Path(path)
    logger.info("loading scope path=%s", path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ValueError(f"File '{path}' is empty or contains only comments")
    scope = MCPScope.model_validate(raw)
    tool_count = sum(len(g.tools) for g in scope.groups)
    logger.info(
        "scope loaded server_name=%s group_count=%s tool_count=%s auth_type=%s",
        scope.server.name,
        len(scope.groups),
        tool_count,
        scope.auth.type,
    )
    return scope
