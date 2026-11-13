#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""HTTP MCP client with OAuth 2.1 authorization (streamable HTTP transport)."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from node_wire_runtime import __version__

from .config import McpClientConfig
from .env_config import (
    config_from_env,
    legacy_static_mcp_token,
    mcp_oauth_enabled,
    mcp_oauth_user_id,
)
from .oauth_flow import OAuthTokenSet
from .session import McpHttpSession
from .token_manager import McpRecoveryAction, TokenManager
from .token_storage import make_token_store

logger = logging.getLogger("runtime.mcp_client.client")


class McpOAuthClient:
    """
    Async MCP client over streamable HTTP with spec-compliant outbound OAuth.

    Implements ``list_tools`` / ``call_tool`` compatible with :class:`agents.toolhive.McpClient`.
    """

    def __init__(
        self,
        base_url: str,
        *,
        config: Optional[McpClientConfig] = None,
        user_id: Optional[str] = None,
        token_manager: Optional[TokenManager] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        reauthorize: Optional[Callable[[], Awaitable[OAuthTokenSet]]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._config = config or config_from_env(server_url=base_url)
        self._user_id = user_id or mcp_oauth_user_id()
        self._session = McpHttpSession()
        self._http = http_client
        self._owns_http = http_client is None
        self._extra_headers = dict(extra_headers or {})
        self._token_manager = token_manager or TokenManager(
            self._config,
            user_id=self._user_id,
            token_store=make_token_store(self._config),
            reauthorize=reauthorize,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=60.0, verify=True)
        return self._http

    async def aclose(self) -> None:
        self._session.close()
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def ensure_authorized(self, *, www_authenticate: Optional[str] = None) -> None:
        """Obtain or refresh tokens; run authorization code flow if needed."""
        http = await self._get_client()
        await self._token_manager.ensure_discovery(www_authenticate=www_authenticate)
        await self._token_manager.get_bearer_token(http_client=http)

    def _headers(self, token: str) -> Dict[str, str]:
        out = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        }
        out.update(self._extra_headers)
        if self._session.session_id:
            out["Mcp-Session-Id"] = self._session.session_id
        return out

    async def _request(
        self,
        method: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """Send one request; on 401/403 recover the token once and resend."""
        client = await self._get_client()
        token = await self._token_manager.get_bearer_token(http_client=client)
        resp = await client.request(
            method, self._base_url, json=json_body, headers=self._headers(token)
        )
        if resp.status_code not in (401, 403):
            return resp

        www = resp.headers.get("WWW-Authenticate")
        action = await self._token_manager.handle_mcp_response(resp.status_code, www)
        if action is McpRecoveryAction.FORBIDDEN:
            resp.raise_for_status()
        if action is McpRecoveryAction.REAUTHORIZE:
            token = await self._token_manager.reauthorize(rejected_token=token, http_client=client)
        else:
            token = await self._token_manager.get_bearer_token(
                force_refresh=True, rejected_token=token, http_client=client
            )
        resp = await client.request(
            method, self._base_url, json=json_body, headers=self._headers(token)
        )
        if resp.status_code == 403:
            resp.raise_for_status()
        return resp

    async def _initialize(self) -> None:
        init_payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "node-wire", "version": __version__},
            },
        }
        resp = await self._request("POST", json_body=init_payload)
        resp.raise_for_status()
        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._session.session_id = session_id
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP initialize error: {data['error']}")

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            await self._request("POST", json_body=notif)
        except Exception as exc:
            logger.debug("MCP initialized notification failed (non-fatal): %s", exc)

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        resp = await self._session.send(
            lambda: self._request("POST", json_body=payload), self._initialize
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result")

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._rpc("tools/list", {})
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if c.get("type") == "text"]
            return "\n".join(parts)
        return str(content)


def create_http_mcp_client(
    base_url: str,
    *,
    user_id: Optional[str] = None,
    force_oauth: bool = False,
    reauthorize: Optional[Callable[[], Awaitable[OAuthTokenSet]]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
):
    """
    Factory: OAuth client when enabled, else legacy static-token HTTP client.

    Priority:
    1. Legacy ``TOOLHIVE_MCP_BEARER_TOKEN`` / ``TOOLHIVE_MCP_API_KEY`` → :class:`ToolHiveMcpClient`
    2. ``NW_MCP_OAUTH_ENABLED=true`` or ``force_oauth`` → :class:`McpOAuthClient`
    3. Default → :class:`ToolHiveMcpClient`
    """
    from agents.toolhive import ToolHiveMcpClient

    if legacy_static_mcp_token() and not force_oauth:
        return ToolHiveMcpClient(base_url, extra_headers=extra_headers)

    if mcp_oauth_enabled() or force_oauth:
        return McpOAuthClient(
            base_url,
            user_id=user_id,
            reauthorize=reauthorize,
            extra_headers=extra_headers,
        )

    return ToolHiveMcpClient(base_url, extra_headers=extra_headers)


def create_http_mcp_clients_for_urls(
    urls: List[str],
    *,
    user_id: Optional[str] = None,
    reauthorize: Optional[Callable[[], Awaitable[OAuthTokenSet]]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> list:
    return [
        create_http_mcp_client(
            u,
            user_id=user_id,
            reauthorize=reauthorize,
            extra_headers=extra_headers,
        )
        for u in urls
    ]
