#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Authorization code flow with PKCE (RFC 7636) and resource indicators (RFC 8707)."""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import webbrowser
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode, urlsplit, parse_qs

import httpx

from .config import McpClientConfig, RedirectMode
from .discovery import DiscoveryResult, discover, discovery_cache_for_config
from .dcr import resolve_client_registration, resolve_redirect_uris
from .exceptions import (
    McpOAuthConfigurationError,
    McpOAuthFlowAborted,
    McpOAuthSecurityError,
)
from .redirect_listener import AuthorizationCallback, LoopbackRedirectListener
from .storage import ClientRegistration, RegistrationStore

logger = logging.getLogger("runtime.mcp_client.oauth_flow")

_PKCE_UNRESERVED = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


@dataclass(frozen=True)
class PkcePair:
    code_verifier: str
    code_challenge: str


@dataclass(frozen=True)
class AuthorizationSession:
    """In-flight authorization state bound to a user session."""

    state: str
    pkce: PkcePair
    redirect_uri: str
    resource: str


@dataclass(frozen=True)
class OAuthTokenSet:
    """Token response from authorization server (Section 6.1 step 8)."""

    access_token: str
    token_type: str
    expires_in: Optional[int]
    refresh_token: Optional[str]
    scope: Optional[str]
    raw: Dict[str, object]


def generate_pkce_pair() -> PkcePair:
    """RFC 7636 PKCE with S256 only (43–128 unreserved characters)."""
    verifier = "".join(secrets.choice(_PKCE_UNRESERVED) for _ in range(64))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkcePair(code_verifier=verifier, code_challenge=challenge)


def generate_state() -> str:
    """Cryptographically random state with at least 128 bits of entropy."""
    return secrets.token_urlsafe(16)


def build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    pkce: PkcePair,
    resource: str,
) -> str:
    """Construct authorization request URL (Section 6.1 step 3)."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": resource,
    }
    if scope.strip():
        params["scope"] = scope.strip()
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{urlencode(params)}"


class AuthorizationCodeFlow:
    """
    MCP authorization code + PKCE flow for a single MCP server configuration.

    Returns :class:`OAuthTokenSet`; persistence is handled by :class:`TokenManager`.
    """

    def __init__(
        self,
        config: McpClientConfig,
        *,
        discovery: Optional[DiscoveryResult] = None,
        registration: Optional[ClientRegistration] = None,
        registration_store: Optional[RegistrationStore] = None,
    ) -> None:
        self._config = config
        self._discovery = discovery
        self._registration = registration
        self._registration_store = registration_store
        self._pending_session: Optional[AuthorizationSession] = None

    @property
    def pending_session(self) -> Optional[AuthorizationSession]:
        return self._pending_session

    async def ensure_discovery(self, *, www_authenticate: Optional[str] = None) -> DiscoveryResult:
        if self._discovery is not None:
            return self._discovery
        cache = discovery_cache_for_config(self._config)
        self._discovery = await discover(
            self._config,
            www_authenticate=www_authenticate,
            cache=cache,
        )
        return self._discovery

    async def prepare_authorization_session(
        self,
        *,
        redirect_uri: str,
    ) -> Tuple[AuthorizationSession, str]:
        """Create PKCE/state session and return (session, authorize_url)."""
        discovery = await self.ensure_discovery()
        registration = self._registration
        if registration is None:
            registration = await resolve_client_registration(
                self._config,
                discovery,
                redirect_uris=[redirect_uri],
                store=self._registration_store,
            )
            self._registration = registration

        pkce = generate_pkce_pair()
        state = generate_state()
        session = AuthorizationSession(
            state=state,
            pkce=pkce,
            redirect_uri=redirect_uri,
            resource=self._config.canonical_server_url,
        )
        self._pending_session = session
        url = build_authorization_url(
            authorization_endpoint=discovery.authorization_server.authorization_endpoint,
            client_id=registration.client_id,
            redirect_uri=redirect_uri,
            scope=self._config.auth.scopes,
            state=state,
            pkce=pkce,
            resource=session.resource,
        )
        return session, url

    def validate_callback(
        self,
        callback: AuthorizationCallback,
        *,
        expected_state: str,
    ) -> str:
        """Validate state and return authorization code."""
        if callback.state != expected_state:
            raise McpOAuthSecurityError("OAuth state mismatch — possible CSRF")
        if callback.error:
            raise McpOAuthFlowAborted(callback.error_description or callback.error)
        if not callback.code:
            raise McpOAuthFlowAborted("Authorization callback missing code")
        return callback.code

    async def exchange_code(
        self,
        code: str,
        *,
        session: AuthorizationSession,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> OAuthTokenSet:
        """Exchange authorization code at token endpoint (Section 6.1 step 7)."""
        discovery = await self.ensure_discovery()
        if self._registration is None:
            raise McpOAuthConfigurationError("Client registration not resolved")

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": session.redirect_uri,
            "client_id": self._registration.client_id,
            "code_verifier": session.pkce.code_verifier,
            "resource": session.resource,
        }

        own_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=30.0, verify=True)
        try:
            headers: Dict[str, str] = {}
            auth = _basic_auth_header(self._registration)
            if auth:
                headers["Authorization"] = auth
            resp = await client.post(
                discovery.authorization_server.token_endpoint,
                data=data,
                headers=headers,
            )
            if resp.status_code != 200:
                raise McpOAuthFlowAborted(f"Token exchange failed with HTTP {resp.status_code}")
            body = resp.json()
            access = body.get("access_token")
            if not access:
                raise McpOAuthFlowAborted("Token response missing access_token")
            return OAuthTokenSet(
                access_token=str(access),
                token_type=str(body.get("token_type") or "Bearer"),
                expires_in=_optional_int(body.get("expires_in")),
                refresh_token=body.get("refresh_token"),
                scope=body.get("scope"),
                raw=body,
            )
        except httpx.HTTPError as exc:
            raise McpOAuthFlowAborted(f"Token exchange HTTP error: {exc}") from exc
        finally:
            if own_client:
                await client.aclose()

    async def run_loopback_authorization(
        self,
        *,
        open_browser: bool = True,
        timeout: float = 300.0,
    ) -> OAuthTokenSet:
        """
        Full loopback flow: listen → browser → callback → token exchange.

        For ``configured-url`` mode use :meth:`start_authorization` and
        :meth:`complete_authorization_with_callback_url` instead.
        """
        if self._config.auth.production:
            raise McpOAuthConfigurationError(
                "auth.production disables HTTP loopback; use configured-url mode with "
                "start_authorization / complete_authorization_with_callback_url"
            )
        if self._config.auth.redirect.mode != RedirectMode.LOOPBACK:
            raise McpOAuthConfigurationError(
                "run_loopback_authorization requires auth.redirect.mode=loopback"
            )

        listener = LoopbackRedirectListener(
            host=self._config.auth.redirect.loopback_host,
            path=self._config.auth.redirect.loopback_path,
        )
        binding = await listener.start()
        try:
            session, authorize_url = await self.prepare_authorization_session(
                redirect_uri=binding.redirect_uri,
            )
            if open_browser:
                webbrowser.open(authorize_url)
            else:
                logger.info("Open this URL to authorize: %s", authorize_url)

            callback = await listener.wait_for_callback(timeout=timeout)
            code = self.validate_callback(callback, expected_state=session.state)
            return await self.exchange_code(code, session=session)
        finally:
            await listener.close()

    async def start_authorization(
        self,
        *,
        redirect_uri: Optional[str] = None,
        open_browser: bool = False,
    ) -> str:
        """
        Begin authorization for configured-url mode; returns URL to open in browser.

        Operator must later call :meth:`complete_authorization_with_callback_url`.
        """
        if self._config.auth.redirect.mode == RedirectMode.LOOPBACK:
            raise McpOAuthConfigurationError(
                "start_authorization is for configured-url mode; use run_loopback_authorization"
            )
        uris = resolve_redirect_uris(self._config)
        uri = redirect_uri or uris[0]
        session, url = await self.prepare_authorization_session(redirect_uri=uri)
        if open_browser:
            webbrowser.open(url)
        return url

    async def complete_authorization_with_callback_url(
        self,
        callback_url: str,
    ) -> OAuthTokenSet:
        """Complete flow from full redirect URL (configured-url deployments)."""
        session = self._pending_session
        if session is None:
            raise McpOAuthConfigurationError(
                "No pending authorization session; call start_authorization first"
            )

        parsed = urlsplit(callback_url.strip())
        if parsed.scheme and parsed.netloc:
            redirect_base = urlsplit(session.redirect_uri)
            if (
                parsed.scheme != redirect_base.scheme
                or parsed.netloc != redirect_base.netloc
                or parsed.path != redirect_base.path
            ):
                raise McpOAuthSecurityError("Callback URL does not match registered redirect_uri")

        qs = parse_qs(parsed.query)
        callback = AuthorizationCallback(
            code=_qs_first(qs, "code"),
            state=_qs_first(qs, "state"),
            error=_qs_first(qs, "error"),
            error_description=_qs_first(qs, "error_description"),
        )
        code = self.validate_callback(callback, expected_state=session.state)
        tokens = await self.exchange_code(code, session=session)
        self._pending_session = None
        return tokens


def _qs_first(qs: dict, key: str) -> Optional[str]:
    vals = qs.get(key)
    return vals[0] if vals else None


def _optional_int(value: object) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, str, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _basic_auth_header(registration: ClientRegistration) -> Optional[str]:
    if not registration.client_secret:
        return None
    import base64 as b64

    raw = f"{registration.client_id}:{registration.client_secret}".encode()
    return "Basic " + b64.standard_b64encode(raw).decode("ascii")
