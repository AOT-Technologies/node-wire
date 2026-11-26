#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Token lifecycle: storage, refresh, audience validation, MCP 401/403 handling."""

from __future__ import annotations

import asyncio
import enum
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
from .dcr import token_endpoint_auth_method
from .oauth_flow import AuthorizationCodeFlow, OAuthTokenSet, _basic_auth_header
from .storage import ClientRegistration
from .token_storage import (
    StoredOAuthTokens,
    TokenStore,
    make_token_store,
    stored_from_oauth_response,
    token_partition_key,
)

logger = logging.getLogger("runtime.mcp_client.token_manager")


class TokenState(str, enum.Enum):
    """What the stored token set allows, as computed by :func:`compute_token_state`."""

    NONE = "none"  # nothing stored
    VALID = "valid"  # usable and outside the refresh lead window
    NEAR_EXPIRY = "near_expiry"  # inside the lead window but not expired yet
    REFRESHABLE = "refreshable"  # expired, refresh token + client registration known
    NEEDS_REAUTH = "needs_reauth"  # expired and cannot be refreshed


class McpRecoveryAction(str, enum.Enum):
    """What to do after an MCP HTTP response (see :meth:`TokenManager.handle_mcp_response`)."""

    OK = "ok"
    RETRY = "retry"  # token rejected: refresh (or re-authorize) and resend once
    REAUTHORIZE = "reauthorize"  # scope step-up: new authorization, then resend once
    FORBIDDEN = "forbidden"


def compute_token_state(
    stored: Optional[StoredOAuthTokens],
    registration: Optional[ClientRegistration],
    *,
    lead_seconds: int,
) -> TokenState:
    if stored is None:
        return TokenState.NONE
    if not stored.is_expired(lead_seconds=lead_seconds):
        return TokenState.VALID
    if not stored.is_expired(lead_seconds=0):
        return TokenState.NEAR_EXPIRY
    if stored.refresh_token and registration is not None:
        return TokenState.REFRESHABLE
    return TokenState.NEEDS_REAUTH


class TokenManager:
    """
    Manages access tokens for one MCP server + user partition.

    Proactive refresh before expiry; reactive refresh on MCP 401; re-authorization
    when refresh fails. The client registration is stored with the tokens, so a
    refresh works after a restart without a new authorization.
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
        # An injected discovery result is used as-is; otherwise discovery goes
        # through the shared cache and is re-fetched when its TTL runs out.
        self._discovery_pinned = discovery is not None
        self._registration = registration
        self._flow = auth_flow or AuthorizationCodeFlow(
            config,
            discovery=discovery,
            registration=registration,
        )
        self._reauthorize = reauthorize
        self._refresh_lock = asyncio.Lock()
        self._memory: Optional[StoredOAuthTokens] = None
        self._aud_ok_token: Optional[str] = None

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
        if self._discovery_pinned and self._discovery is not None:
            return self._discovery
        result = await discover(
            self._config,
            www_authenticate=www_authenticate,
            cache=discovery_cache_for_config(self._config),
        )
        if self._discovery is not None and result.issuer != self._discovery.issuer:
            self._memory = None  # tokens are partitioned per issuer
        self._discovery = result
        self._flow._discovery = result  # noqa: SLF001 — shared flow state
        return result

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
        # Cache what the store returned: a keychain or encrypted-file read on every
        # MCP request would block the event loop. save/discard keep _memory in sync.
        stored = self._store.get(key)
        self._memory = stored
        return stored

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

    def _registration_for(
        self, stored: Optional[StoredOAuthTokens]
    ) -> Optional[ClientRegistration]:
        """Client to present on refresh: injected, then this process's authorization,
        then the one stored with the tokens, then the configured client id."""
        if self._registration is not None:
            return self._registration
        if self._flow.registration is not None:
            return self._flow.registration
        if stored is not None and stored.client_registration is not None:
            return stored.client_registration
        if self._config.auth.client.id and self._discovery is not None:
            return ClientRegistration(
                issuer=self._discovery.issuer,
                client_id=self._config.auth.client.id,
                client_secret=self._config.auth.client.secret or None,
                redirect_uris=(),
                token_endpoint_auth_method=token_endpoint_auth_method(self._config),
                registered_at="",
            )
        return None

    def persist_oauth_token_set(
        self,
        token_set: OAuthTokenSet,
        *,
        issuer: str,
    ) -> StoredOAuthTokens:
        self.validate_access_token_audience(token_set.access_token)
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
        stored.client_registration = self._registration_for(None)
        self.save_tokens(stored)
        return stored

    def validate_access_token_audience(self, access_token: str) -> None:
        """When token is JWT, ensure ``aud`` matches target MCP server URL."""
        if access_token == self._aud_ok_token:
            return
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
        self._aud_ok_token = access_token

    def _load_usable(self) -> Optional[StoredOAuthTokens]:
        """Stored tokens, after discarding a set whose audience is not this server."""
        stored = self.load_stored()
        if stored is None:
            return None
        try:
            self.validate_access_token_audience(stored.access_token)
        except McpAudienceMismatch:
            logger.warning("Discarding stored MCP token issued for a different audience")
            self.discard_tokens()
            return None
        return stored

    async def get_bearer_token(
        self,
        *,
        force_refresh: bool = False,
        rejected_token: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> str:
        """Return a usable access token.

        ``force_refresh`` replaces the current token: refresh token first, full
        re-authorization only if that fails. With ``rejected_token`` the force only
        applies while that token is still the stored one, so callers that saw the
        same 401 share one refresh.
        """
        await self.ensure_discovery()
        lead = self._config.auth.token.refresh_lead_seconds

        # Fast path: a still-valid token needs no lock.
        if not force_refresh:
            stored = self._load_usable()
            state = compute_token_state(stored, self._registration_for(stored), lead_seconds=lead)
            if stored is not None and state is TokenState.VALID:
                return stored.access_token

        # Slow path. The lock spans re-check *and* refresh so concurrent callers
        # issue one token request between them. Checking expiry outside the lock
        # and taking it only around the POST lets every racing caller decide to
        # refresh: against an IdP that rotates single-use refresh tokens the first
        # POST invalidates the shared token, and the rest get invalid_grant and
        # discard the partition into a full re-authorization.
        async with self._refresh_lock:
            stored = self._load_usable()
            if (
                force_refresh
                and rejected_token is not None
                and stored is not None
                and stored.access_token != rejected_token
            ):
                return stored.access_token  # another caller already replaced it
            registration = self._registration_for(stored)
            state = compute_token_state(stored, registration, lead_seconds=lead)
            if stored is not None and state is TokenState.VALID and not force_refresh:
                return stored.access_token

            can_refresh = stored is not None and bool(stored.refresh_token) and registration
            if (
                stored is not None
                and can_refresh
                and (force_refresh or state in (TokenState.NEAR_EXPIRY, TokenState.REFRESHABLE))
            ):
                try:
                    refreshed = await self._refresh_tokens_locked(
                        stored,
                        http_client=http_client,
                    )
                    return refreshed.access_token
                except McpTokenRefreshError as exc:
                    if state is TokenState.NEAR_EXPIRY and not force_refresh:
                        logger.info("MCP token refresh failed; using current token: %s", exc)
                        return stored.access_token
                    self.discard_tokens()
            elif stored is not None and state is TokenState.NEAR_EXPIRY and not force_refresh:
                return stored.access_token

            return await self._reauthorize_locked()

    async def reauthorize(
        self,
        *,
        rejected_token: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> str:
        """Run a new authorization even if the stored token is still valid (scope
        step-up). With ``rejected_token``, a caller that finds the token already
        replaced by a concurrent step-up uses the new one instead."""
        del http_client  # the authorization flow owns its HTTP calls
        await self.ensure_discovery()
        async with self._refresh_lock:
            stored = self.load_stored()
            if (
                rejected_token is not None
                and stored is not None
                and stored.access_token != rejected_token
            ):
                return stored.access_token
            return await self._reauthorize_locked()

    async def _reauthorize_locked(self) -> str:
        """Full authorization; caller holds ``_refresh_lock``. The token is checked
        against this server's audience before it is stored."""
        discovery = await self.ensure_discovery()
        token_set = await self._run_reauthorize()
        persisted = self.persist_oauth_token_set(token_set, issuer=discovery.issuer)
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
        """Exchange the stored refresh token for a new token set.

        Public entry point — takes the refresh lock itself. Callers already
        holding it (``get_bearer_token``) must use ``_refresh_tokens_locked``:
        ``asyncio.Lock`` is not reentrant.
        """
        async with self._refresh_lock:
            return await self._refresh_tokens_locked(stored, http_client=http_client)

    async def _refresh_tokens_locked(
        self,
        stored: StoredOAuthTokens,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> StoredOAuthTokens:
        """Refresh-token exchange. Caller must hold ``_refresh_lock``."""
        if not stored.refresh_token:
            raise McpTokenRefreshError("No refresh token available")

        # Someone may have refreshed while we waited for the lock, which leaves the
        # token set we were handed stale — and its refresh_token already spent at an
        # IdP that rotates them. Hand back the newer one instead of POSTing a
        # consumed token and tripping the invalid_grant path below.
        current = self.load_stored()
        if current is not None and current.access_token != stored.access_token:
            return current

        discovery = await self.ensure_discovery()
        registration = self._registration_for(stored)
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
            auth = _basic_auth_header(registration)
            if auth:
                headers["Authorization"] = auth

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
            updated.client_registration = registration
            self.validate_access_token_audience(updated.access_token)
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
    ) -> McpRecoveryAction:
        """
        Map an MCP HTTP status to a recovery action (Section 9).

        ``insufficient_scope`` (on 401 or 403) asks for a scope step-up: the scopes
        from the challenge are used by the next authorization request.
        """
        if status_code not in (401, 403):
            return McpRecoveryAction.OK
        challenge = parse_www_authenticate(www_authenticate)
        if challenge and challenge.is_insufficient_scope:
            if challenge.scope:
                self._request_scopes(challenge.scope)
            return McpRecoveryAction.REAUTHORIZE
        if status_code == 403:
            return McpRecoveryAction.FORBIDDEN
        return McpRecoveryAction.RETRY

    def _request_scopes(self, scopes: str) -> None:
        # Manager and flow must agree: the flow builds the authorization request.
        self._flow.request_scopes(scopes)
        self._config = self._config.model_copy(
            update={"auth": self._config.auth.model_copy(update={"scopes": scopes})}
        )


def _optional_int(value: object) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, str, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
