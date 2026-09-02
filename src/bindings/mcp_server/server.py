#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import copy
import contextvars
import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any, Dict, List, Mapping, Optional, Tuple

from bindings.factory import ConnectorFactory
from bindings.invoke import ConnectorNotExposed, invoke
from bindings.mcp_server.auth import (
    McpAuthError,
    authenticate_mcp_request,
    reset_upstream_passthrough_context,
    log_effective_mcp_auth_state,
)
from node_wire_runtime.caller_identity import CallerIdentity
from node_wire_runtime.config_store import DEFAULT_TENANT, ConfigNotFoundError
from node_wire_runtime.identity import (
    MissingTenantError,
    TenantIdentityMismatchError,
    is_multitenancy_enabled,
    resolve_tenant_id,
)
from node_wire_runtime.policies.mcp_scope_policy import (
    action_allowed_for_identity_scopes,
    load_scope_map_from_env,
    load_scope_policy_default_from_env,
    resolve_required_scope_for_action,
)
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime import ConnectorResponse, ErrorCategory, get_connector_registry
from node_wire_runtime.manifest import MCP_MANIFEST_CONTRACT_VERSION, build_manifest
from node_wire_runtime.ingress import normalize_mcp_tool_arguments  # re-export for tests
from node_wire_runtime.rate_limit import (
    RateLimitExceeded,
    get_per_identity_rate_limiter,
    global_rate_limiter,
    identity_rate_limit_key,
    per_identity_rate_limit_enabled,
)
from node_wire_runtime.streaming import stream_completion_log
from node_wire_runtime.tenant_session import TenantSessionOverlay

logger = logging.getLogger("bindings.mcp_server")

_DEFAULT_MCP_HOST = "127.0.0.1"
_PUBLIC_BIND_HOSTS = frozenset({"0.0.0.0", "::"})

# Read-only meta-tools (not connectors). Advertised names are OpenAI/NVIDIA-safe
# (no dots). Legacy dotted names still invoke.
LIST_CONFIGS_TOOL = "nw_list_configs"
LIST_CONFIGS_TOOL_ALIASES = frozenset({LIST_CONFIGS_TOOL, "nw.list_configs"})
LIST_TENANTS_TOOL = "nw_list_tenants"
LIST_TENANTS_TOOL_ALIASES = frozenset({LIST_TENANTS_TOOL, "nw.list_tenants"})
SELECT_TENANT_TOOL = "nw_select_tenant"
SELECT_TENANT_TOOL_ALIASES = frozenset({SELECT_TENANT_TOOL, "nw.select_tenant"})
SELECT_CONFIG_TOOL = "nw_select_config"
SELECT_CONFIG_TOOL_ALIASES = frozenset({SELECT_CONFIG_TOOL, "nw.select_config"})

# Pin-lock/allowlist env readers and the tenant/config selection state they
# guard now live in node_wire_runtime.tenant_session.TenantSessionOverlay.


def _optional_str(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def format_list_tenants_text(
    *,
    tenants: List[str],
    connector_id: Optional[str],
    pinned_tenant_id: Optional[str],
) -> str:
    """Markdown listing used as the ``summary`` field of ``nw_list_tenants``."""
    if connector_id:
        heading = f"Tenants with `{connector_id}` configs"
    else:
        heading = "Tenants with configs on this server"
    if pinned_tenant_id:
        heading += f" (current: `{pinned_tenant_id}`)"
    else:
        heading += " (no tenant selected)"

    if not tenants:
        body = "No tenants found."
    else:
        lines = []
        for tid in tenants:
            suffix = "  *(current)*" if pinned_tenant_id and tid == pinned_tenant_id else ""
            lines.append(f"- `{tid}`{suffix}")
        body = "\n".join(lines)

    return (
        f"{heading}\n\n{body}\n\n"
        "Call `nw_select_tenant` with a tenant_id from this list to switch "
        "every connector on this server. Then call `nw_select_config` with a "
        "config name that all connectors should use."
    )


def mcp_advertised_tool_name(connector_id: str, action: str) -> str:
    """MCP tools/list name: ``{connector_id}_{action}`` with ``.``/``-`` → ``_``.

    OpenAI-compatible function names allow only ``[a-zA-Z0-9_-]``. Internal
    dispatch still uses ``connector_id`` + ``action`` (e.g. ``files.list``).
    """
    action_part = str(action).replace(".", "_").replace("-", "_")
    return f"{connector_id}_{action_part}"


def _with_config_name_property(input_schema: Dict[str, Any]) -> Dict[str, Any]:
    """Advertise ``config_name`` as an accepted per-request argument on a
    connector-run tool's input schema (Ticket 3 of
    ``.scratch/mcp-tenant-config-per-request/``), mirroring REST's payload
    field / gRPC's request field — the channel Ticket 2 wired server-side.

    Returns a new schema; never mutates ``input_schema`` in place. Skips
    connectors whose own input model already defines a ``config_name``
    field for business-logic reasons, rather than silently shadowing it.
    """
    properties = input_schema.get("properties")
    if not isinstance(properties, dict) or "config_name" in properties:
        return input_schema
    new_properties = dict(properties)
    new_properties["config_name"] = {
        "type": ["string", "null"],
        "description": (
            "Optional config name for this call. Omit or null to use the "
            "selected or default config."
        ),
    }
    return {**input_schema, "properties": new_properties}


def mcp_llm_safe_input_schema(schema: Any) -> Any:
    """JSON Schema NVIDIA/OpenAI function calling will accept.

    Drops ``null`` unions (``type: [T, null]`` / ``anyOf`` + null). Invoke already
    treats JSON null as omitted, so advertised schemas can be plain types.
    """
    if not isinstance(schema, dict):
        return schema
    return _strip_json_schema_nulls(copy.deepcopy(schema))


def _strip_json_schema_nulls(node: Any) -> Any:
    if isinstance(node, list):
        return [_strip_json_schema_nulls(x) for x in node]
    if not isinstance(node, dict):
        return node

    t = node.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        if len(non_null) == 1:
            node["type"] = non_null[0]
        elif non_null:
            node["type"] = non_null
        else:
            node["type"] = "string"

    for union_key in ("anyOf", "oneOf"):
        variants = node.get(union_key)
        if not isinstance(variants, list):
            continue
        kept = [
            v
            for v in variants
            if not (isinstance(v, dict) and v.get("type") == "null" and len(v) == 1)
        ]
        if len(kept) == 1 and isinstance(kept[0], dict):
            merged = {k: v for k, v in node.items() if k != union_key}
            merged.update(kept[0])
            return _strip_json_schema_nulls(merged)
        node[union_key] = [_strip_json_schema_nulls(v) for v in kept]

    if isinstance(node.get("properties"), dict):
        node["properties"] = {
            k: _strip_json_schema_nulls(v) for k, v in node["properties"].items()
        }
    if "items" in node:
        node["items"] = _strip_json_schema_nulls(node["items"])
    if isinstance(node.get("additionalProperties"), dict):
        node["additionalProperties"] = _strip_json_schema_nulls(
            node["additionalProperties"]
        )
    if "default" in node and node["default"] is None:
        node.pop("default", None)
    return node


def _jsonrpc_method(item: Any) -> str | None:
    try:
        return str(item.message.root.method)
    except Exception:
        return None


def _server_discover_result(server_name: str) -> Dict[str, Any]:
    """Minimal success payload for ToolHive's non-spec ``server/discover`` probe."""
    return {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": server_name, "version": "node-wire"},
    }


def resolve_mcp_host(env_value: str | None = None) -> str:
    if env_value is not None:
        return env_value.strip()
    return os.getenv("NW_MCP_HOST", _DEFAULT_MCP_HOST).strip()


def is_public_bind_host(host: str) -> bool:
    return host in _PUBLIC_BIND_HOSTS


_streamable_http_identity_ctx: contextvars.ContextVar[CallerIdentity | None] = (
    contextvars.ContextVar(
        "nw_streamable_http_identity",
        default=None,
    )
)

_http_request_headers: ContextVar[Mapping[str, str] | None] = ContextVar(
    "mcp_http_request_headers",
    default=None,
)

# Pinned factory tenant for the current streamable-http request (§6.3).
# Set with env_pin=None so process NW_TENANT_ID cannot override X-Tenant-ID.
_session_tenant_ctx: ContextVar[str | None] = ContextVar(
    "mcp_session_tenant",
    default=None,
)


def _process_response_payload(data: Any, max_items: int) -> Tuple[Any, bool, int, Optional[str]]:
    """
    Recursively search for large lists and truncate them.
    Also tracks the maximum list size found and searches for pagination tokens.
    Returns: (processed_data, was_truncated, max_list_size, next_page_token)
    """
    next_page_token = None
    max_list_size = 0
    was_truncated = False

    if isinstance(data, list):
        current_len = len(data)
        max_list_size = max(max_list_size, current_len)

        working_list = data
        if current_len > max_items:
            working_list = data[:max_items]
            was_truncated = True

        out_list = []
        for item in working_list:
            new_item, t, mls, npt = _process_response_payload(item, max_items)
            out_list.append(new_item)
            was_truncated = was_truncated or t
            max_list_size = max(max_list_size, mls)
            if npt and not next_page_token:
                next_page_token = npt

        return out_list, was_truncated, max_list_size, next_page_token

    if isinstance(data, dict):
        out_dict = {}
        for k, v in data.items():
            if k in (
                "nextPageToken",
                "pageToken",
                "next_cursor",
                "cursor",
                "next_page_token",
            ) and isinstance(v, str):
                if not next_page_token:
                    next_page_token = v

            new_v, t, mls, npt = _process_response_payload(v, max_items)
            out_dict[k] = new_v
            was_truncated = was_truncated or t
            max_list_size = max(max_list_size, mls)
            if npt and not next_page_token:
                next_page_token = npt

        return out_dict, was_truncated, max_list_size, next_page_token

    return data, False, 0, next_page_token


def _resolve_upstream_passthrough(
    factory: ConnectorFactory,
    connector_ids: frozenset[str] | None,
) -> bool:
    """Enable when google_drive-only MCP server uses upstream_bearer auth."""
    if connector_ids != frozenset({"google_drive"}):
        return False
    cfg = factory._configs.get("google_drive")
    if cfg is None:
        return False
    auth = cfg.raw.get("auth") or {}
    return auth.get("provider") == "upstream_bearer"


def _upstream_passthrough_scopes(
    factory: ConnectorFactory,
    connector_ids: frozenset[str] | None,
) -> tuple[str, ...]:
    if connector_ids is None:
        return ()
    scope_map = load_scope_map_from_env()
    default_mode = load_scope_policy_default_from_env()
    manifest = build_manifest(factory.list_for_protocol("mcp"))
    scopes: set[str] = set()
    for entry in manifest:
        cid = entry["connector_id"]
        if cid not in connector_ids:
            continue
        required = resolve_required_scope_for_action(
            connector_id=cid,
            action=str(entry["action"]),
            action_scope_map=scope_map,
            default_mode=default_mode,
        )
        if required:
            scopes.add(required)
    return tuple(sorted(scopes))


class McpServer:
    """
    Manifest-driven MCP server: tools come from connector metadata; execution
    dispatches through ConnectorFactory and connector.run().

    Use list_tools() / invoke_tool() for programmatic access, or run_stdio()
    for a full MCP stdio transport.
    """

    def __init__(
        self,
        *,
        server_name: str = "node-wire",
        connector_ids: Optional[List[str]] = None,
        factory: ConnectorFactory | None = None,
    ) -> None:
        self._server_name = server_name
        self._connector_ids: Optional[frozenset[str]] = (
            None if connector_ids is None else frozenset(connector_ids)
        )
        auto_register()
        if factory is not None:
            self._factory = factory
        else:
            self._factory = ConnectorFactory()
            self._factory.load()
            # Same YAML hydrate as REST; skip when factory is injected (playground).
            from node_wire_runtime.tenant_persistence import load_tenants

            load_tenants(self._factory.store)
        self._upstream_passthrough = _resolve_upstream_passthrough(
            self._factory, self._connector_ids
        )
        self._upstream_passthrough_scopes = (
            _upstream_passthrough_scopes(self._factory, self._connector_ids)
            if self._upstream_passthrough
            else ()
        )
        # §6.4: which tenant/config this session has selected — one in-memory
        # overlay for this process (stdio and HTTP). Split ToolHive images do
        # not share it — run one MCP with all connectors.
        self._tenant_session = TenantSessionOverlay(
            store_has_tenant=self._store_has_tenant,
            config_coverage=self._config_coverage,
        )
        try:
            from importlib.metadata import version as pkg_version

            _pkg_ver = pkg_version("node-wire")
        except Exception:  # pragma: no cover
            _pkg_ver = "unknown"
        logger.info(
            "MCP server initialized | server_name=%s | manifest_contract=%s | package=%s",
            server_name,
            MCP_MANIFEST_CONTRACT_VERSION,
            _pkg_ver,
        )

    @property
    def tenant_session(self) -> TenantSessionOverlay:
        """The session's tenant/config selection overlay.

        Public so trusted in-process embedders (e.g.
        ``agents.toolhive.InProcessMcpClient``) can pin a session without
        reaching into ``McpServer``'s private state.
        """
        return self._tenant_session

    def list_tools(self, *, identity: CallerIdentity | None = None) -> List[Dict[str, Any]]:
        identity = self._ensure_identity(identity=identity)
        return self._list_tools_impl(identity=identity)

    def _list_tools_impl(self, *, identity: CallerIdentity | None = None) -> List[Dict[str, Any]]:
        scope_map = load_scope_map_from_env()
        default_mode = load_scope_policy_default_from_env()
        connectors = self._factory.list_for_protocol("mcp")
        manifest = build_manifest(connectors)
        tools: List[Dict[str, Any]] = []
        for entry in manifest:
            cid = entry["connector_id"]
            if self._connector_ids is not None and cid not in self._connector_ids:
                continue
            if not action_allowed_for_identity_scopes(
                connector_id=cid,
                action=str(entry["action"]),
                principal=identity.principal if identity else None,
                tenant_id=identity.tenant_id if identity else None,
                scopes=identity.scopes if identity else None,
                action_scope_map=scope_map,
                default_mode=default_mode,
            ):
                continue
            schema_desc = entry["input_schema"].get("description", "")

            security_lines = []
            if entry.get("requires_auth"):
                security_lines.append("- Requires Auth: Yes")
            scopes = entry.get("scopes")
            if scopes:
                security_lines.append(f"- Scopes: {', '.join(scopes)}")
            rate_limit = entry.get("rate_limit")
            if rate_limit:
                security_lines.append(f"- Rate Limit: {rate_limit}")
            if entry.get("deprecated"):
                security_lines.append("- DEPRECATED: True")

            sec_block = "\n".join(security_lines)
            if sec_block:
                sec_block = f"\n\nSecurity & Limits:\n{sec_block}\n\n"

            tool_desc = (
                (f"{schema_desc}\n" if schema_desc else "")
                + sec_block
                + (
                    f"Pass fields from inputSchema only; do not include an action field "
                    f"(it is injected from the tool name). "
                    f"Manifest contract v{MCP_MANIFEST_CONTRACT_VERSION}."
                )
            )
            input_schema = entry["input_schema"]
            if is_multitenancy_enabled():
                input_schema = _with_config_name_property(input_schema)

            tools.append(
                {
                    "name": mcp_advertised_tool_name(cid, str(entry["action"])),
                    "description": tool_desc,
                    "input_schema": input_schema,
                    "output_schema": entry["output_schema"],
                }
            )
        if is_multitenancy_enabled():
            tools.insert(
                0,
                {
                    "name": SELECT_CONFIG_TOOL,
                    "description": (
                        "Select one config name for every connector on this server. "
                        "Connectors that lack this name on the current tenant fail on "
                        "the next tool call. Does not create configs."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "config_name": {
                                "type": "string",
                                "description": "Config name from nw_list_configs / nw_select_tenant.",
                            },
                        },
                        "required": ["config_name"],
                    },
                    "output_schema": {"type": "object"},
                },
            )
            tools.insert(
                0,
                {
                    "name": LIST_CONFIGS_TOOL,
                    "description": (
                        "List named connector configs for a tenant from the config store. "
                        "Optional tenant_id; omit to use the selected or pinned tenant. "
                        "Read-only; does not create configs. Call nw_select_config "
                        "with a returned name to apply it to every connector."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "connector_id": {
                                "type": ["string", "null"],
                                "description": (
                                    "Optional connector id to filter (e.g. google_drive). "
                                    "Omit or null to list all connectors for this tenant."
                                ),
                            },
                            "tenant_id": {
                                "type": ["string", "null"],
                                "description": (
                                    "Optional tenant id. Omit or null for the selected or "
                                    "pinned tenant."
                                ),
                            },
                        },
                    },
                    "output_schema": {"type": "object"},
                },
            )
            tools.insert(
                0,
                {
                    "name": SELECT_TENANT_TOOL,
                    "description": (
                        "Switch the tenant for every connector on this MCP server. "
                        "Returns named configs for that tenant. Env/header pin is the "
                        "default until this is called. Does not create tenants."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "tenant_id": {
                                "type": "string",
                                "description": "Tenant id from nw_list_tenants.",
                            },
                            "connector_id": {
                                "type": ["string", "null"],
                                "description": (
                                    "Optional connector id to filter returned configs."
                                ),
                            },
                        },
                        "required": ["tenant_id"],
                    },
                    "output_schema": {"type": "object"},
                },
            )
            tools.insert(
                0,
                {
                    "name": LIST_TENANTS_TOOL,
                    "description": (
                        "List tenant ids from the config store that have at least one named "
                        "config. Optional connector_id filters to tenants that have that "
                        "connector. After listing, call nw_select_tenant to switch."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "connector_id": {
                                "type": ["string", "null"],
                                "description": (
                                    "Optional connector id (e.g. google_drive). Omit or null "
                                    "to list tenants that have any connector on this server."
                                ),
                            },
                        },
                    },
                    "output_schema": {"type": "object"},
                },
            )
        return tools

    def _resolve_tool_tenant_id(self, identity: CallerIdentity | None) -> str:
        session_tenant = _session_tenant_ctx.get()
        if session_tenant is not None:
            return session_tenant
        try:
            return resolve_tenant_id(
                headers=_http_request_headers.get(),
                jwt_identity=identity,
                env_pin=self._tenant_session.env_pin,
            )
        except (MissingTenantError, TenantIdentityMismatchError) as exc:
            raise ValueError(str(exc)) from exc

    def _store_has_tenant(self, tenant_id: str) -> bool:
        return tenant_id in self._factory.store.list_tenants()

    def _effective_tenant_id(
        self,
        identity: CallerIdentity | None,
        *,
        tenant_arg: Optional[str] = None,
    ) -> str:
        return self._tenant_session.effective_tenant_id(
            tenant_arg=tenant_arg,
            request_tenant_id=_session_tenant_ctx.get(),
            resolve_from_request=lambda: self._resolve_tool_tenant_id(identity),
        )

    def _server_connector_ids(self) -> List[str]:
        if self._connector_ids is not None:
            return sorted(self._connector_ids)
        return sorted(
            c.connector_id for c in self._factory.list_for_protocol("mcp")
        )

    def _config_coverage(
        self, tenant_id: str, config_name: str
    ) -> Tuple[List[str], List[str]]:
        have: List[str] = []
        missing: List[str] = []
        for cid in self._server_connector_ids():
            if self._factory.store.get(tenant_id, cid, config_name) is not None:
                have.append(cid)
            else:
                missing.append(cid)
        return have, missing

    def _list_configs_for_tenant(
        self, tenant_id: str, connector_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Slim redacted config rows for agents (name / default / connector_id)."""
        allow = self._connector_ids
        if connector_id is not None:
            if allow is not None and connector_id not in allow:
                raise ValueError(
                    f"Connector {connector_id!r} is not allowed on this MCP server."
                )
            docs = self._factory.store.list(tenant_id, connector_id)
        elif allow is not None:
            docs = []
            for cid in sorted(allow):
                docs.extend(self._factory.store.list(tenant_id, cid))
        else:
            docs = self._factory.store.list(tenant_id, None)

        out: List[Dict[str, Any]] = []
        for d in docs:
            name = d.get("name")
            cid = d.get("connector_id")
            if not isinstance(name, str) or not isinstance(cid, str):
                continue
            if allow is not None and cid not in allow:
                continue
            out.append(
                {
                    "connector_id": cid,
                    "name": name,
                    "default": bool(d.get("default")),
                }
            )
        return out

    async def _invoke_list_configs(
        self,
        arguments: Dict[str, Any],
        *,
        identity: CallerIdentity | None,
    ) -> Dict[str, Any]:
        if not is_multitenancy_enabled():
            raise ValueError(
                f"Tool {LIST_CONFIGS_TOOL!r} requires NW_MULTITENANCY_ENABLED=true"
            )
        tenant_arg = _optional_str(arguments.get("tenant_id"))
        tenant_id = self._effective_tenant_id(identity, tenant_arg=tenant_arg)
        raw_cid = arguments.get("connector_id")
        connector_id: Optional[str] = None
        if isinstance(raw_cid, str) and raw_cid.strip():
            connector_id = raw_cid.strip()
        # null / blank / non-string → all connectors

        logger.info(
            "MCP tool resolved | tool=%s | tenant_id=%s | connector_id=%s",
            LIST_CONFIGS_TOOL,
            tenant_id,
            connector_id or "(all)",
            extra={
                "tool_name": LIST_CONFIGS_TOOL,
                "tenant_id": tenant_id,
                "connector_id": connector_id or "",
            },
        )
        configs = self._list_configs_for_tenant(tenant_id, connector_id)
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "configs": configs,
        }

    def _list_tenant_ids(self, connector_id: Optional[str]) -> List[str]:
        """Tenant ids in the store that have at least one config (allowlist-aware)."""
        return self._tenant_session.filter_allowed_tenants(
            self._list_tenant_ids_unfiltered(connector_id)
        )

    def _list_tenant_ids_unfiltered(self, connector_id: Optional[str]) -> List[str]:
        allow = self._connector_ids
        if connector_id is not None:
            if allow is not None and connector_id not in allow:
                raise ValueError(
                    f"Connector {connector_id!r} is not allowed on this MCP server."
                )
            return [
                tid
                for tid in self._factory.store.list_tenants()
                if self._factory.store.has_config(tid, connector_id)
            ]
        if allow is not None:
            return [
                tid
                for tid in self._factory.store.list_tenants()
                if any(self._factory.store.has_config(tid, cid) for cid in allow)
            ]
        return self._factory.store.list_tenants()

    def _pinned_tenant_id_or_none(self, identity: CallerIdentity | None) -> Optional[str]:
        return self._tenant_session.pinned_tenant_id_or_none(
            request_tenant_id=_session_tenant_ctx.get(),
            resolve_from_request=lambda: self._resolve_tool_tenant_id(identity),
        )

    async def _invoke_list_tenants(
        self,
        arguments: Dict[str, Any],
        *,
        identity: CallerIdentity | None,
    ) -> Dict[str, Any]:
        if not is_multitenancy_enabled():
            raise ValueError(
                f"Tool {LIST_TENANTS_TOOL!r} requires NW_MULTITENANCY_ENABLED=true"
            )
        raw_cid = arguments.get("connector_id")
        connector_id: Optional[str] = None
        if isinstance(raw_cid, str) and raw_cid.strip():
            connector_id = raw_cid.strip()

        tenants = self._list_tenant_ids(connector_id)
        current = self._pinned_tenant_id_or_none(identity)
        logger.info(
            "MCP tool resolved | tool=%s | connector_id=%s | pinned_tenant_id=%s",
            LIST_TENANTS_TOOL,
            connector_id or "(all)",
            current or "(none)",
            extra={
                "tool_name": LIST_TENANTS_TOOL,
                "connector_id": connector_id or "",
                "pinned_tenant_id": current or "",
            },
        )
        # Must be a dict: a bare str is iterated char-by-char into CallToolResult.content.
        return {
            "ok": True,
            "connector_id": connector_id,
            "pinned_tenant_id": current,
            "current_tenant_id": current,
            "tenants": tenants,
            "summary": format_list_tenants_text(
                tenants=tenants,
                connector_id=connector_id,
                pinned_tenant_id=current,
            ),
        }

    async def _invoke_select_tenant(
        self,
        arguments: Dict[str, Any],
        *,
        identity: CallerIdentity | None,
    ) -> Dict[str, Any]:
        if not is_multitenancy_enabled():
            raise ValueError(
                f"Tool {SELECT_TENANT_TOOL!r} requires NW_MULTITENANCY_ENABLED=true"
            )
        self._tenant_session.assert_switch_allowed()
        tenant_id = _optional_str(arguments.get("tenant_id"))
        if not tenant_id:
            raise ValueError(f"{SELECT_TENANT_TOOL} requires tenant_id")
        self._tenant_session.select_tenant(tenant_id)
        raw_cid = arguments.get("connector_id")
        connector_id: Optional[str] = None
        if isinstance(raw_cid, str) and raw_cid.strip():
            connector_id = raw_cid.strip()
        configs = self._list_configs_for_tenant(tenant_id, connector_id)
        have: List[str] = []
        missing: List[str] = []
        selected_name = self._tenant_session.selected_config_name
        if selected_name:
            have, missing = self._config_coverage(tenant_id, selected_name)
        logger.info(
            "MCP tool resolved | tool=%s | tenant_id=%s | connector_id=%s",
            SELECT_TENANT_TOOL,
            tenant_id,
            connector_id or "(all)",
            extra={
                "tool_name": SELECT_TENANT_TOOL,
                "tenant_id": tenant_id,
                "connector_id": connector_id or "",
            },
        )
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "connector_id": connector_id,
            "configs": configs,
            "selected_config_name": selected_name,
            "connectors_with_config": have,
            "connectors_missing_config": missing,
        }

    async def _invoke_select_config(
        self,
        arguments: Dict[str, Any],
        *,
        identity: CallerIdentity | None,
    ) -> Dict[str, Any]:
        if not is_multitenancy_enabled():
            raise ValueError(
                f"Tool {SELECT_CONFIG_TOOL!r} requires NW_MULTITENANCY_ENABLED=true"
            )
        config_name = _optional_str(arguments.get("config_name"))
        if not config_name:
            raise ValueError(f"{SELECT_CONFIG_TOOL} requires config_name")
        tenant_id = self._effective_tenant_id(identity)
        have, missing = self._tenant_session.select_config(tenant_id, config_name)
        logger.info(
            "MCP tool resolved | tool=%s | tenant_id=%s | config_name=%s",
            SELECT_CONFIG_TOOL,
            tenant_id,
            config_name,
            extra={
                "tool_name": SELECT_CONFIG_TOOL,
                "tenant_id": tenant_id,
                "config_name": config_name,
            },
        )
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "config_name": config_name,
            "connectors_with_config": have,
            "connectors_missing_config": missing,
        }

    def _ensure_identity(
        self,
        *,
        identity: CallerIdentity | None,
        meta: Mapping[str, Any] | None = None,
    ) -> CallerIdentity | None:
        if identity is not None:
            return identity
        request_identity = _streamable_http_identity_ctx.get()
        if request_identity is not None:
            return request_identity
        return authenticate_mcp_request(
            headers=_http_request_headers.get(),
            meta=meta,
            upstream_passthrough=self._upstream_passthrough,
            upstream_granted_scopes=self._upstream_passthrough_scopes,
        )

    def _request_meta_from_context(self) -> Mapping[str, Any] | None:
        try:
            from mcp.server.lowlevel.server import request_ctx

            ctx = request_ctx.get()
        except Exception:
            return None
        if ctx is None or ctx.meta is None:
            return None
        if hasattr(ctx.meta, "model_dump"):
            dumped = ctx.meta.model_dump()  # type: ignore[attr-defined]
            if isinstance(dumped, dict):
                return dumped
            return None
        if isinstance(ctx.meta, dict):
            return ctx.meta
        return None

    def _resolve_tool_name(self, name: str) -> Tuple[str, str]:
        """Map advertised or legacy MCP tool name to ``(connector_id, action)``."""
        if "." in name:
            connector_id, action = name.split(".", 1)
            return connector_id, action

        connectors = self._factory.list_for_protocol("mcp")
        manifest = build_manifest(connectors)
        matches: List[Tuple[str, str]] = []
        for entry in manifest:
            cid = str(entry["connector_id"])
            if self._connector_ids is not None and cid not in self._connector_ids:
                continue
            action = str(entry["action"])
            if mcp_advertised_tool_name(cid, action) == name:
                matches.append((cid, action))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous MCP tool name {name!r}")
        raise ValueError(
            f"Unknown tool {name!r}. Expected '<connector>_<action>' "
            f"(or legacy '<connector>.<action>')."
        )

    async def invoke_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        *,
        identity: CallerIdentity | None = None,
    ) -> Dict[str, Any]:
        identity = self._ensure_identity(identity=identity)
        try:
            # Skip rate limiting if disabled
            if os.environ.get("NW_RATE_LIMIT_DISABLED", "false").lower() not in (
                "true",
                "1",
                "yes",
            ):
                await global_rate_limiter.acquire()
        except RateLimitExceeded as e:
            raise ValueError(str(e))

        # Opt-in per-identity limiter (off by default; M-2, 2026-09-01 review).
        # Shared with REST/gRPC via node_wire_runtime.rate_limit so one noisy
        # caller can't exhaust the global bucket for every other MCP client.
        if per_identity_rate_limit_enabled():
            limiter = get_per_identity_rate_limiter()
            identity_key = identity_rate_limit_key(
                identity.principal if identity else None, fallback="mcp"
            )
            result = limiter.consume(f"mcp:{name}:{identity_key}")
            if not result.allowed:
                raise ValueError(
                    f"Rate limit exceeded, retry after {result.retry_after_seconds}s"
                )

        arguments = dict(arguments or {})
        # LLMs often fill optional schema keys with null; treat as omitted.
        arguments = {k: v for k, v in arguments.items() if v is not None}

        if name in LIST_CONFIGS_TOOL_ALIASES:
            return await self._invoke_list_configs(arguments, identity=identity)
        if name in LIST_TENANTS_TOOL_ALIASES:
            return await self._invoke_list_tenants(arguments, identity=identity)
        if name in SELECT_TENANT_TOOL_ALIASES:
            return await self._invoke_select_tenant(arguments, identity=identity)
        if name in SELECT_CONFIG_TOOL_ALIASES:
            return await self._invoke_select_config(arguments, identity=identity)

        connector_id, action = self._resolve_tool_name(name)

        if self._connector_ids is not None and connector_id not in self._connector_ids:
            raise ValueError(f"Connector {connector_id!r} is not allowed on this MCP server.")

        # tenant_id stays header/JWT-only here (matches REST's header, gRPC's
        # metadata) — strip any LLM-supplied value so it never reaches
        # connector.run() or overrides tenant resolution.
        arguments.pop("tenant_id", None)
        # config_name is a per-request tool-call argument (matches REST's
        # payload field, gRPC's request field) — pop it off the connector
        # payload but thread the value through to resolution; it outranks
        # the shared nw_select_config selection. Not just an LLM extra.
        config_arg = _optional_str(arguments.pop("config_name", None))
        config_name = self._tenant_session.effective_config_name(config_arg=config_arg)
        tenant_id = self._effective_tenant_id(identity)

        max_items = int(os.environ.get("NW_MCP_MAX_LIST_ITEMS", "50"))
        clamped_params: Dict[str, Any] = {}
        connector_cls = get_connector_registry().get(connector_id)
        if connector_cls is not None:
            meta = connector_cls.sdk_action_metas().get(action)
            if meta and hasattr(meta.input_model, "model_fields"):
                for page_param in ["page_size", "limit", "_count"]:
                    if page_param in meta.input_model.model_fields:
                        current_val = arguments.get(page_param)
                        if current_val is None:
                            arguments[page_param] = max_items
                            clamped_params[page_param] = max_items
                        else:
                            try:
                                val = int(current_val)
                                arguments[page_param] = min(val, max_items)
                                clamped_params[page_param] = arguments[page_param]
                            except (ValueError, TypeError):
                                logger.debug(
                                    "Ignoring non-numeric pagination parameter %s=%r",
                                    page_param,
                                    current_val,
                                )

        trace_id = arguments.get("trace_id") or str(uuid.uuid4())

        try:
            response = await invoke(
                self._factory,
                connector_id=connector_id,
                action=action,
                payload=arguments,
                protocol="mcp",
                tenant_id=tenant_id,
                config_name=config_name,
                principal=identity.principal if identity else None,
                scopes=identity.scopes if identity else None,
            )
            stream_completion_log(trace_id, True, connector_id=connector_id, action=action)
        except ConnectorNotExposed:
            raise ValueError(f"Connector {connector_id!r} is not available via MCP.")
        except ConfigNotFoundError:
            if config_name is not None:
                raise ValueError(
                    f"Config {config_name!r} is not defined for connector "
                    f"{connector_id!r} on tenant {tenant_id!r}."
                )
            raise ValueError(f"Connector {connector_id!r} is not available via MCP.")
        except Exception:
            stream_completion_log(trace_id, False, connector_id=connector_id, action=action)
            raise

        resolved_config_name = config_name
        if is_multitenancy_enabled():
            try:
                connector = await self._factory.get(
                    connector_id,
                    tenant_id=tenant_id,
                    config_name=config_name,
                )
                resolved_config_name = connector.config_name
            except ConfigNotFoundError:
                pass
            logger.info(
                "MCP tool resolved | tool=%s | tenant_id=%s | config_name=%s",
                name,
                tenant_id,
                resolved_config_name or "(default)",
                extra={
                    "tool_name": name,
                    "connector_id": connector_id,
                    "action": action,
                    "tenant_id": tenant_id,
                    "config_name": resolved_config_name or "",
                },
            )

        raw_response = response.model_dump()

        # Enforce MCP sampling guardrail
        processed_payload, was_truncated, item_count, next_token = _process_response_payload(
            raw_response, max_items
        )

        # Overwrite raw_response in place
        raw_response.clear()
        raw_response.update(processed_payload)

        # Add _system_pagination_used metadata (keeps old clients/MCP inspector working)
        if clamped_params:
            raw_response["_system_pagination_used"] = clamped_params

        # IMPORTANT: Inject metadata IN-BAND inside the "data" dictionary so client UIs
        # (like Toolhive / Agent chat) that only render the `data` block will explicitly see it.
        if "data" in raw_response and isinstance(raw_response["data"], dict):
            pagination_meta: dict[str, Any] = {}
            if clamped_params:
                pagination_meta["coerced_parameters"] = clamped_params
            pagination_meta["items_returned"] = item_count
            if was_truncated:
                pagination_meta["was_truncated_by_server"] = True
            if next_token:
                pagination_meta["next_page_token"] = next_token
            # Prepend it visually for the LLM
            raw_response["data"] = {
                "_server_pagination_metadata": pagination_meta,
                **raw_response["data"],
            }

        # We also inject explicitly into the root if it doesn't have a data block
        elif not isinstance(raw_response.get("data"), dict):
            raw_response["_server_pagination_metadata"] = {
                "coerced_parameters": clamped_params,
                "items_returned": item_count,
                "next_page_token": next_token,
            }

        # Build dynamic system message
        sys_msgs = []
        if clamped_params:
            sys_msgs.append(
                f"[System Pagination] Arguments coerced to safeguard limits: {json.dumps(clamped_params)}"
            )

        if item_count > 0:
            count_msg = f"[System Guardrail] The connector returned {item_count} items."
            if was_truncated:
                count_msg += f" (truncated to {max_items} to preserve context)"
            sys_msgs.append(count_msg)

        if next_token:
            sys_msgs.append(
                f"[System Pagination] nextPageToken available for next query: '{next_token}'"
            )

        if was_truncated and not next_token:
            sys_msgs.append(
                f"[System Guardrail WARNING] Data exceeded {max_items} items and was hard-truncated. "
                "No native next page token was found! You MUST retry this query with an explicit "
                f"`page_size` or limit parameter set to {max_items} to force the API to generate valid cursors."
            )

        if sys_msgs:
            combined_sys_msgs = "\n".join(sys_msgs)
            if raw_response.get("message"):
                raw_response["message"] = f"{raw_response['message']}\n\n{combined_sys_msgs}"
            else:
                raw_response["message"] = combined_sys_msgs

        return raw_response

    def _setup_lowlevel_server(self) -> Any:
        from mcp.server import Server as LowLevelServer
        from mcp.types import Tool

        low = LowLevelServer(self._server_name)

        @low.list_tools()
        async def handle_list_tools() -> list[Tool]:
            meta = self._request_meta_from_context()
            try:
                identity = self._ensure_identity(identity=None, meta=meta)
            except McpAuthError as exc:
                logger.warning(
                    "MCP tools/list denied by authentication",
                    extra={
                        "status_code": exc.status_code,
                        "error_code": exc.error_code,
                    },
                )
                raise RuntimeError(json.dumps(exc.to_payload())) from exc
            if identity:
                logger.info(
                    "MCP tools/list authorized",
                    extra={
                        "principal": identity.principal,
                        "tenant_id": identity.tenant_id or "",
                        "auth_type": identity.auth_type,
                    },
                )
            out: list[Tool] = []
            for t in self._list_tools_impl(identity=identity):
                out.append(
                    Tool(
                        name=t["name"],
                        description=t["description"],
                        inputSchema=mcp_llm_safe_input_schema(t["input_schema"]),
                    )
                )
            return out

        @low.call_tool()
        async def handle_call_tool(tool_name: str, arguments: dict) -> dict:
            meta = self._request_meta_from_context()
            try:
                identity = self._ensure_identity(identity=None, meta=meta)
            except McpAuthError as exc:
                logger.warning(
                    "MCP tools/call denied by authentication",
                    extra={
                        "tool_name": tool_name,
                        "status_code": exc.status_code,
                        "error_code": exc.error_code,
                    },
                )
                return ConnectorResponse(
                    success=False,
                    data=None,
                    error_code=exc.error_code,
                    error_category=ErrorCategory.AUTH,
                    message=exc.detail,
                    trace_id=f"mcp-auth-{uuid.uuid4()}",
                    details=exc.to_payload(),
                ).model_dump()

            if identity:
                logger.info(
                    "MCP tools/call authorized",
                    extra={
                        "tool_name": tool_name,
                        "principal": identity.principal,
                        "tenant_id": identity.tenant_id or "",
                        "auth_type": identity.auth_type,
                    },
                )
            return await self.invoke_tool(tool_name, arguments or {}, identity=identity)

        return low

    async def _run_stdio_async(self) -> None:
        from mcp.server.stdio import stdio_server
        from mcp.server import NotificationOptions
        from mcp.types import JSONRPCMessage, JSONRPCResponse
        from mcp.shared.message import SessionMessage

        import anyio

        # §6.4: one process, one tenant — pin from env at stdio start (not on HTTP).
        raw = os.getenv("NW_TENANT_ID")
        self._tenant_session.set_env_pin(raw.strip() if raw and raw.strip() else None)

        log_effective_mcp_auth_state()

        low = self._setup_lowlevel_server()
        server_name = self._server_name

        async with stdio_server() as (read_stream, write_stream):
            filt_send, filt_recv = anyio.create_memory_object_stream(0)

            async def filter_loop() -> None:
                try:
                    async with filt_send:
                        async for item in read_stream:
                            if (
                                not isinstance(item, Exception)
                                and _jsonrpc_method(item) == "server/discover"
                            ):
                                req_id = item.message.root.id
                                reply = SessionMessage(
                                    message=JSONRPCMessage(
                                        JSONRPCResponse(
                                            jsonrpc="2.0",
                                            id=req_id,
                                            result=_server_discover_result(server_name),
                                        )
                                    )
                                )
                                await write_stream.send(reply)
                                continue
                            await filt_send.send(item)
                except anyio.ClosedResourceError:
                    pass

            async with anyio.create_task_group() as tg:
                tg.start_soon(filter_loop)
                await low.run(
                    filt_recv,
                    write_stream,
                    low.create_initialization_options(
                        notification_options=NotificationOptions()
                    ),
                )
                tg.cancel_scope.cancel()

    def run_stdio(self) -> None:
        import anyio

        anyio.run(self._run_stdio_async)

    def _build_streamable_http_app(self, *, session_manager: Any, path: str) -> Any:
        from contextlib import asynccontextmanager

        from starlette.applications import Starlette
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        upstream_passthrough = self._upstream_passthrough
        upstream_granted_scopes = self._upstream_passthrough_scopes
        factory_store = self._factory.store

        @asynccontextmanager
        async def lifespan(app: Starlette):
            async with session_manager.run():
                yield

        class StreamableHttpAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):  # type: ignore[override]
                if request.url.path != path:
                    return await call_next(request)
                try:
                    identity = authenticate_mcp_request(
                        headers=request.headers,
                        upstream_passthrough=upstream_passthrough,
                        upstream_granted_scopes=upstream_granted_scopes,
                    )
                except McpAuthError as exc:
                    headers: Dict[str, str] = {}
                    if exc.www_authenticate:
                        headers["WWW-Authenticate"] = exc.www_authenticate
                    return JSONResponse(
                        status_code=exc.status_code,
                        content=exc.to_payload(),
                        headers=headers,
                    )

                setattr(request.state, "nw_mcp_identity", identity)
                # §6.3: pin tenant for this HTTP request/session context; never use
                # process NW_TENANT_ID (stdio-only) so it cannot override X-Tenant-ID.
                try:
                    session_tenant = resolve_tenant_id(
                        headers=request.headers,
                        jwt_identity=identity,
                        env_pin=None,
                    )
                except MissingTenantError as exc:
                    if DEFAULT_TENANT in factory_store.list_tenants():
                        session_tenant = DEFAULT_TENANT
                    else:
                        return JSONResponse(
                            status_code=400,
                            content={"detail": str(exc), "error_code": "MISSING_TENANT"},
                        )
                except TenantIdentityMismatchError as exc:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": str(exc), "error_code": "TENANT_IDENTITY_MISMATCH"},
                    )
                token = _streamable_http_identity_ctx.set(identity)
                tenant_token = _session_tenant_ctx.set(session_tenant)
                try:
                    return await call_next(request)
                finally:
                    _session_tenant_ctx.reset(tenant_token)
                    _streamable_http_identity_ctx.reset(token)
                    reset_upstream_passthrough_context()

        # Use a wrapper class to ensure Starlette treats this as an ASGI app
        # without the automatic redirection logic of Mount().
        class _ASGIApp:
            def __init__(self, handler):
                self.handler = handler

            async def __call__(self, scope, receive, send):
                headers = {
                    key.decode("latin-1"): value.decode("latin-1")
                    for key, value in scope.get("headers", [])
                }
                token = _http_request_headers.set(headers)
                try:
                    await self.handler(scope, receive, send)
                finally:
                    _http_request_headers.reset(token)

        starlette_app = Starlette(
            lifespan=lifespan,
            routes=[
                Route(
                    path,
                    endpoint=_ASGIApp(session_manager.handle_request),
                    methods=["GET", "POST"],
                )
            ],
        )
        starlette_app.add_middleware(StreamableHttpAuthMiddleware)
        return starlette_app

    async def _run_streamable_http_async(self) -> None:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        import uvicorn

        host = resolve_mcp_host()
        port = int(os.getenv("NW_MCP_PORT", "8081"))
        path = os.getenv("NW_MCP_PATH", "/mcp")

        if is_public_bind_host(host):
            logger.warning(
                "MCP streamable-http binding to all interfaces; "
                "set NW_MCP_HOST=127.0.0.1 for local-only access",
                extra={"host": host, "port": port},
            )

        log_effective_mcp_auth_state()

        low = self._setup_lowlevel_server()
        session_manager = StreamableHTTPSessionManager(low, json_response=True)
        starlette_app = self._build_streamable_http_app(session_manager=session_manager, path=path)

        logger.info(f"Starting MCP streamable-http server on {host}:{port}{path}")
        config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    def run_streamable_http(self) -> None:
        import anyio

        anyio.run(self._run_streamable_http_async)

    def run(self, transport: str = "stdio") -> None:
        transport = transport.strip().lower()
        if transport == "stdio":
            self.run_stdio()
        elif transport == "streamable-http":
            self.run_streamable_http()
        else:
            raise ValueError(f"Unsupported MCP transport: {transport}")


if __name__ == "__main__":
    # Simple demo runner that emits the tool list as JSON to stdout and exits.
    import sys

    server = McpServer()
    sys.stdout.write(json.dumps(server.list_tools(), indent=2) + "\n")
