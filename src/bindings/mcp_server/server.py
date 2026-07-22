#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import contextvars
import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any, Dict, List, Mapping, Optional, Tuple

from bindings.factory import ConnectorFactory
from bindings.mcp_server.auth import (
    McpAuthError,
    authenticate_mcp_request,
    reset_upstream_passthrough_context,
    log_effective_mcp_auth_state,
)
from node_wire_runtime.caller_identity import CallerIdentity
from node_wire_runtime.config_store import ConfigNotFoundError
from node_wire_runtime.identity import resolve_tenant_id
from node_wire_runtime.policies.mcp_scope_policy import (
    action_allowed_for_identity_scopes,
    load_scope_map_from_env,
    load_scope_policy_default_from_env,
    resolve_required_scope_for_action,
)
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime.manifest import MCP_MANIFEST_CONTRACT_VERSION, build_manifest
from node_wire_runtime import ConnectorResponse, ErrorCategory
from node_wire_runtime.ingress import enforce_authoritative_action, normalize_mcp_tool_arguments
from node_wire_runtime.rate_limit import global_rate_limiter, RateLimitExceeded
from node_wire_runtime.streaming import stream_completion_log

logger = logging.getLogger("bindings.mcp_server")

_DEFAULT_MCP_HOST = "127.0.0.1"
_PUBLIC_BIND_HOSTS = frozenset({"0.0.0.0", "::"})


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
    ) -> None:
        self._server_name = server_name
        self._connector_ids: Optional[frozenset[str]] = (
            None if connector_ids is None else frozenset(connector_ids)
        )
        auto_register()
        self._factory = ConnectorFactory()
        self._factory.load()
        self._upstream_passthrough = _resolve_upstream_passthrough(
            self._factory, self._connector_ids
        )
        self._upstream_passthrough_scopes = (
            _upstream_passthrough_scopes(self._factory, self._connector_ids)
            if self._upstream_passthrough
            else ()
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
            # config_name is an optional resolution-time argument (§6.3): an agent
            # may target a named config; omitting it uses the tenant's default.
            input_schema = entry["input_schema"]
            props = input_schema.setdefault("properties", {})
            props.setdefault(
                "config_name",
                {
                    "type": "string",
                    "description": "Optional named connector configuration; omit for the default.",
                },
            )

            tools.append(
                {
                    "name": f"{cid}.{entry['action']}",
                    "description": tool_desc,
                    "input_schema": input_schema,
                    "output_schema": entry["output_schema"],
                }
            )
        return tools

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

        try:
            connector_id, action = name.split(".", 1)
        except ValueError:
            raise ValueError("Tool name must be in the form '<connector>.<action>'")

        if self._connector_ids is not None and connector_id not in self._connector_ids:
            raise ValueError(f"Connector {connector_id!r} is not allowed on this MCP server.")

        if not self._factory.is_exposed(connector_id, "mcp"):
            raise ValueError(f"Connector {connector_id!r} is not available via MCP.")

        # Tenant pinned from the session (SSE headers) / stdio env; config_name is a
        # resolution-time argument, never a connector input.
        arguments = dict(arguments or {})
        config_name = arguments.pop("config_name", None)
        tenant_id = resolve_tenant_id(
            headers=_http_request_headers.get(),
            jwt_identity=identity,
            env_pin=os.getenv("NW_TENANT_ID"),
        )

        try:
            connector = await self._factory.get(
                connector_id,
                tenant_id=tenant_id,
                config_name=config_name,
                action=action,
            )
        except ConfigNotFoundError:
            raise ValueError(f"Connector {connector_id!r} is not available via MCP.")

        run_args = normalize_mcp_tool_arguments(connector, action, arguments)
        enforce_authoritative_action(run_args, action)
        run_args["action"] = action

        trace_id = run_args.get("trace_id") or str(uuid.uuid4())

        # Proactively inject/clamp pagination parameters to prevent native token desync
        # caused by the post-execution truncation guardrail
        max_items = int(os.environ.get("NW_MCP_MAX_LIST_ITEMS", "50"))
        meta = connector.sdk_action_metas().get(action)
        clamped_params = {}
        if meta and hasattr(meta.input_model, "model_fields"):
            for page_param in ["page_size", "limit", "_count"]:
                if page_param in meta.input_model.model_fields:
                    current_val = run_args.get(page_param)
                    if current_val is None:
                        run_args[page_param] = max_items
                        clamped_params[page_param] = max_items
                    else:
                        try:
                            val = int(current_val)
                            run_args[page_param] = min(val, max_items)
                            clamped_params[page_param] = run_args[page_param]
                        except (ValueError, TypeError):
                            pass

        try:
            response = await connector.run(
                run_args,
                principal=identity.principal if identity else None,
                tenant_id=tenant_id,
                scopes=identity.scopes if identity else None,
            )
            stream_completion_log(trace_id, True, connector_id=connector_id, action=action)
        except Exception:
            stream_completion_log(trace_id, False, connector_id=connector_id, action=action)
            raise

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
                kwargs: Dict[str, Any] = {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["input_schema"],
                    "outputSchema": t["output_schema"],
                }
                out.append(Tool(**kwargs))
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

        log_effective_mcp_auth_state()

        low = self._setup_lowlevel_server()

        async with stdio_server() as (read_stream, write_stream):
            await low.run(
                read_stream,
                write_stream,
                low.create_initialization_options(notification_options=NotificationOptions()),
            )

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
                token = _streamable_http_identity_ctx.set(identity)
                try:
                    return await call_next(request)
                finally:
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
