#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Token lifecycle: storage, refresh, audience validation, MCP 401/403 handling."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, Optional, Awaitable

import httpx
import jwt

from .challenges import parse_www_authenticate
from .config import McpClientConfig
from .discovery import DiscoveryResult, discover, discovery_cache_for_config
from .exceptions import (
    McpAudienceMismatch,
    McpOAuthConfigurationError,
    McpTokenRefreshError,
)
from .oauth_flow import AuthorizationCodeFlow, OAuthTokenSet
from .storage import ClientRegistration
from .token_storage import (
    StoredOAuthTokens,
    TokenStore,
    make_token_store,
    stored_from_oauth_response,
    token_partition_key,
)

logger = logging.getLogger("runtime.mcp_client.token_manager")


class TokenManager:
    """
    Manages access tokens for one MCP server + user partition.

    Proactive refresh before expiry; reactive refresh on MCP 401; re-authorization
    when refresh fails.
    """

    def __init__(
        self,
        config: McpClientConfig,
        *,
        user_id: str,
        token_store: Optional[TokenStore] = None,
        discovery: Optional[DiscoveryResult] = None,
        registration: Optional[ClientRegistration] = None,
        auth_flow: Optional[AuthorizationCodeFlow] = None,
        reauthorize: Optional[Callable[[], Awaitable[OAuthTokenSet]]] = None,
    ) -> None:
        self._config = config
        self._user_id = user_id
        self._store = token_store or make_token_store(config)
        self._discovery = discovery
        self._registration = registration
        self._flow = auth_flow or AuthorizationCodeFlow(
            config,
            discovery=discovery,
            registration=registration,
        )
        self._reauthorize = reauthorize
        self._refresh_lock = asyncio.Lock()
        self._memory: Optional[StoredOAuthTokens] = None

    @property
    def partition_key(self) -> str:
        issuer = self._discovery.issuer if self._discovery else ""
        return token_partition_key(
            self._user_id,
            self._config.canonical_server_url,
            issuer,
        )

    async def ensure_discovery(
        self,
        *,
        www_authenticate: Optional[str] = None,
    ) -> DiscoveryResult:
        if self._discovery is not None:
            return self._discovery
        cache = discovery_cache_for_config(self._config)
        self._discovery = await discover(
            self._config,
            www_authenticate=www_authenticate,
            cache=cache,
        )
        self._flow._discovery = self._discovery  # noqa: SLF001 — shared flow state
        return self._discovery

    def load_stored(self) -> Optional[StoredOAuthTokens]:
        if self._memory is not None:
            return self._memory
        if not self._discovery:
            return None
        key = token_partition_key(
            self._user_id,
            self._config.canonical_server_url,
            self._discovery.issuer,
        )
        return self._store.get(key)

    def save_tokens(self, tokens: StoredOAuthTokens) -> None:
        self._memory = tokens
        self._store.save(tokens)

    def discard_tokens(self) -> None:
        self._memory = None
        if self._discovery:
            key = token_partition_key(
                self._user_id,
                self._config.canonical_server_url,
                self._discovery.issuer,
            )
            self._store.delete(key)

    def persist_oauth_token_set(
        self,
        token_set: OAuthTokenSet,
        *,
        issuer: str,
    ) -> StoredOAuthTokens:
        stored = stored_from_oauth_response(
            user_id=self._user_id,
            mcp_server_url=self._config.canonical_server_url,
            issuer=issuer,
            access_token=token_set.access_token,
            token_type=token_set.token_type,
            expires_in=token_set.expires_in,
            refresh_token=token_set.refresh_token,
            scope=token_set.scope,
        )
        self.save_tokens(stored)
        return stored

    def validate_access_token_audience(self, access_token: str) -> None:
        """When token is JWT, ensure ``aud`` matches target MCP server URL."""
        if access_token.count(".") != 2:
            return
        try:
            claims = jwt.decode(
                access_token,
                options={"verify_signature": False, "verify_aud": False},
                algorithms=["RS256", "RS384", "ES256", "HS256"],
            )
        except jwt.PyJWTError:
            return
        aud = claims.get("aud")
        if aud is None:
            return
        expected = self._config.canonical_server_url
        audiences = aud if isinstance(aud, list) else [aud]
        if expected not in audiences and expected.rstrip("/") not in audiences:
            raise McpAudienceMismatch(
                f"Token audience {audiences!r} does not match MCP server {expected!r}"
            )

    async def get_bearer_token(
        self,
        *,
        force_refresh: bool = False,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> str:
        discovery = await self.ensure_discovery()
        stored = self.load_stored()
        lead = self._config.auth.token.refresh_lead_seconds

        if stored and not force_refresh and not stored.is_expired(lead_seconds=lead):
            self.validate_access_token_audience(stored.access_token)
            return stored.access_token

        if stored and stored.refresh_token and not force_refresh:
            try:
                refreshed = await self.refresh_tokens(stored, http_client=http_client)
                self.validate_access_token_audience(refreshed.access_token)
                return refreshed.access_token
            except McpTokenRefreshError:
                self.discard_tokens()

        token_set = await self._run_reauthorize()
        persisted = self.persist_oauth_token_set(token_set, issuer=discovery.issuer)
        self.validate_access_token_audience(persisted.access_token)
        return persisted.access_token

    async def _run_reauthorize(self) -> OAuthTokenSet:
        if self._reauthorize is not None:
            return await self._reauthorize()
        if self._config.auth.production:
            raise McpOAuthConfigurationError(
                "Production mode requires a reauthorize callback; complete OAuth at "
                "auth.redirect.url via start_authorization / "
                "complete_authorization_with_callback_url, or inject "
                "TokenManager(reauthorize=...)."
            )
        return await self._flow.run_loopback_authorization(open_browser=True)

    async def refresh_tokens(
        self,
        stored: StoredOAuthTokens,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> StoredOAuthTokens:
        if not stored.refresh_token:
            raise McpTokenRefreshError("No refresh token available")

        discovery = await self.ensure_discovery()
        registration = self._flow._registration  # noqa: SLF001
        if registration is None:
            raise McpTokenRefreshError("Client registration not available for refresh")

        data = {
            "grant_type": "refresh_token",
            "refresh_token": stored.refresh_token,
            "resource": self._config.canonical_server_url,
            "client_id": registration.client_id,
        }

        own_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=30.0, verify=True)
        try:
            headers: Dict[str, str] = {}
            from .oauth_flow import _basic_auth_header

            auth = _basic_auth_header(registration)
            if auth:
                headers["Authorization"] = auth

            async with self._refresh_lock:
                resp = await client.post(
                    discovery.authorization_server.token_endpoint,
                    data=data,
                    headers=headers,
                )
                if resp.status_code != 200:
                    body = (
                        resp.json()
                        if resp.headers.get("content-type", "").startswith("application/json")
                        else {}
                    )
                    if body.get("error") == "invalid_grant":
                        self.discard_tokens()
                    raise McpTokenRefreshError(f"Refresh failed with HTTP {resp.status_code}")
                body = resp.json()
                access = body.get("access_token")
                if not access:
                    raise McpTokenRefreshError("Refresh response missing access_token")

                new_refresh = body.get("refresh_token") or stored.refresh_token
                updated = stored_from_oauth_response(
                    user_id=stored.user_id,
                    mcp_server_url=stored.mcp_server_url,
                    issuer=stored.issuer,
                    access_token=str(access),
                    token_type=str(body.get("token_type") or stored.token_type),
                    expires_in=_optional_int(body.get("expires_in")),
                    refresh_token=new_refresh,
                    scope=body.get("scope") or stored.scope,
                )
                self.save_tokens(updated)
                return updated
        except httpx.HTTPError as exc:
            raise McpTokenRefreshError(f"Refresh HTTP error: {exc}") from exc
        finally:
            if own_client:
                await client.aclose()

    async def handle_mcp_response(
        self,
        status_code: int,
        www_authenticate: Optional[str],
    ) -> str:
        """
        Map MCP HTTP errors to token actions (Section 9).

        Returns action: ``retry``, ``reauthorize``, or ``forbidden``.
        """
        if status_code == 403:
            return "forbidden"
        if status_code != 401:
            return "ok"

        challenge = parse_www_authenticate(www_authenticate)
        if challenge and challenge.is_insufficient_scope:
            if challenge.scope:
                self._config = self._config.model_copy(
                    update={
                        "auth": self._config.auth.model_copy(update={"scopes": challenge.scope})
                    }
                )
            return "reauthorize"
        if challenge and challenge.treat_as_unauthorized:
            return "retry"
        return "retry"


def _optional_int(value: object) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, str, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
