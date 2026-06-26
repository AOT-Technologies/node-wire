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

from .config import McpClientConfig
from .env_config import (
    config_from_env,
    legacy_static_mcp_token,
    mcp_oauth_enabled,
    mcp_oauth_user_id,
)
from .exceptions import McpOAuthFlowAborted, McpTokenRefreshError
from .oauth_flow import OAuthTokenSet
from .token_manager import TokenManager
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
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._config = config or config_from_env(server_url=base_url)
        self._user_id = user_id or mcp_oauth_user_id()
        self._session_id: Optional[str] = None
        self._initialized = False
        self._http = http_client
        self._owns_http = http_client is None
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
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def ensure_authorized(self, *, www_authenticate: Optional[str] = None) -> None:
        """Obtain or refresh tokens; run authorization code flow if needed."""
        http = await self._get_client()
        await self._token_manager.ensure_discovery(www_authenticate=www_authenticate)
        await self._token_manager.get_bearer_token(http_client=http)

    async def _auth_headers(self) -> Dict[str, str]:
        http = await self._get_client()
        token = await self._token_manager.get_bearer_token(http_client=http)
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        }

    def _merge_headers(self, extra: Dict[str, str]) -> Dict[str, str]:
        out = dict(extra)
        if self._session_id:
            out["Mcp-Session-Id"] = self._session_id
        return out

    async def _request(
        self,
        method: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        retried_auth: bool = False,
    ) -> httpx.Response:
        client = await self._get_client()
        headers = self._merge_headers(await self._auth_headers())
        resp = await client.request(method, self._base_url, json=json_body, headers=headers)

        if resp.status_code == 401 and not retried_auth:
            www = resp.headers.get("WWW-Authenticate")
            action = await self._token_manager.handle_mcp_response(401, www)
            if action == "forbidden":
                resp.raise_for_status()
            if action == "reauthorize":
                await self.ensure_authorized(www_authenticate=www)
                return await self._request(
                    method,
                    json_body=json_body,
                    retried_auth=True,
                )
            stored = self._token_manager.load_stored()
            if stored and stored.refresh_token:
                try:
                    await self._token_manager.refresh_tokens(stored, http_client=client)
                    return await self._request(
                        method,
                        json_body=json_body,
                        retried_auth=True,
                    )
                except McpTokenRefreshError:
                    self._token_manager.discard_tokens()
            else:
                self._token_manager.discard_tokens()
            try:
                await self._token_manager.get_bearer_token(http_client=client)
            except McpOAuthFlowAborted:
                await self.ensure_authorized(www_authenticate=www)
            return await self._request(
                method,
                json_body=json_body,
                retried_auth=True,
            )

        if resp.status_code == 403:
            await self._token_manager.handle_mcp_response(403, None)
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
                "clientInfo": {"name": "node-wire", "version": "1.0.0"},
            },
        }
        resp = await self._request("POST", json_body=init_payload)
        resp.raise_for_status()
        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP initialize error: {data['error']}")

        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            await self._request("POST", json_body=notif)
        except Exception:
            pass
        self._initialized = True

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        if not self._initialized:
            await self._initialize()

        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        resp = await self._request("POST", json_body=payload)
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
        return ToolHiveMcpClient(base_url)

    if mcp_oauth_enabled() or force_oauth:
        return McpOAuthClient(base_url, user_id=user_id, reauthorize=reauthorize)

    return ToolHiveMcpClient(base_url)


def create_http_mcp_clients_for_urls(
    urls: List[str],
    *,
    user_id: Optional[str] = None,
    reauthorize: Optional[Callable[[], Awaitable[OAuthTokenSet]]] = None,
) -> list:
    return [create_http_mcp_client(u, user_id=user_id, reauthorize=reauthorize) for u in urls]
