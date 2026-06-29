#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""RFC 7591 Dynamic Client Registration for MCP OAuth client."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from .config import McpClientConfig, RedirectMode
from .discovery import DiscoveryResult
from .exceptions import McpOAuthConfigurationError, McpOAuthRegistrationError
from .storage import ClientRegistration, RegistrationStore

logger = logging.getLogger("runtime.mcp_client.dcr")

_DEFAULT_GRANT_TYPES = ["authorization_code", "refresh_token"]
_DEFAULT_RESPONSE_TYPES = ["code"]


def resolve_redirect_uris(
    config: McpClientConfig, *, loopback_uri: Optional[str] = None
) -> List[str]:
    """Redirect URIs for DCR and authorization requests."""
    if config.auth.production:
        if config.auth.redirect.mode == RedirectMode.LOOPBACK:
            raise McpOAuthConfigurationError(
                "auth.production does not allow auth.redirect.mode=loopback"
            )
        uri = config.auth.redirect.url.strip()
        if not uri:
            raise McpOAuthConfigurationError(
                "auth.redirect.url is required for configured-url mode"
            )
        if not uri.startswith("https://"):
            raise McpOAuthConfigurationError(
                "auth.production requires auth.redirect.url to use https://"
            )
        return [uri]

    if config.auth.redirect.mode == RedirectMode.LOOPBACK:
        if not loopback_uri:
            raise McpOAuthConfigurationError(
                "loopback redirect URI is required for DCR in loopback mode"
            )
        return [loopback_uri]
    uri = config.auth.redirect.url.strip()
    if not uri:
        raise McpOAuthConfigurationError("auth.redirect.url is required for configured-url mode")
    if not uri.startswith("https://") and not _is_loopback_uri(uri):
        raise McpOAuthConfigurationError(
            "auth.redirect.url must be HTTPS unless it is a loopback IP literal"
        )
    return [uri]


def _is_loopback_uri(uri: str) -> bool:
    from urllib.parse import urlsplit

    host = urlsplit(uri).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def token_endpoint_auth_method(config: McpClientConfig) -> str:
    if config.auth.client.secret:
        return "client_secret_basic"
    return "none"


async def register_dynamic_client(
    config: McpClientConfig,
    discovery: DiscoveryResult,
    *,
    redirect_uris: List[str],
    http_client: Optional[httpx.AsyncClient] = None,
) -> ClientRegistration:
    """
    POST RFC 7591 registration to ``registration_endpoint``.

    Raises if DCR is disabled, endpoint missing, or registration fails.
    """
    if not config.auth.dcr.enabled:
        raise McpOAuthRegistrationError("Dynamic Client Registration is disabled in config")
    endpoint = discovery.authorization_server.registration_endpoint
    if not endpoint:
        raise McpOAuthRegistrationError(
            "Authorization server does not advertise registration_endpoint"
        )

    auth_method = token_endpoint_auth_method(config)
    payload = {
        "client_name": config.auth.dcr.client_name,
        "redirect_uris": redirect_uris,
        "grant_types": _DEFAULT_GRANT_TYPES,
        "response_types": _DEFAULT_RESPONSE_TYPES,
        "token_endpoint_auth_method": auth_method,
        "scope": config.auth.scopes.strip(),
    }

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0, verify=True)
    try:
        resp = await client.post(endpoint, json=payload)
        if resp.status_code not in (200, 201):
            body = resp.text[:500]
            raise McpOAuthRegistrationError(f"DCR failed with HTTP {resp.status_code}: {body}")
        data = resp.json()
        client_id = data.get("client_id")
        if not client_id:
            raise McpOAuthRegistrationError("DCR response missing client_id")
        return ClientRegistration(
            issuer=discovery.issuer,
            client_id=str(client_id),
            client_secret=data.get("client_secret"),
            redirect_uris=tuple(redirect_uris),
            token_endpoint_auth_method=str(data.get("token_endpoint_auth_method") or auth_method),
            registered_at=datetime.now(timezone.utc).isoformat(),
        )
    except httpx.HTTPError as exc:
        raise McpOAuthRegistrationError(f"DCR HTTP error: {exc}") from exc
    finally:
        if own_client:
            await client.aclose()


async def resolve_client_registration(
    config: McpClientConfig,
    discovery: DiscoveryResult,
    *,
    redirect_uris: List[str],
    store: Optional[RegistrationStore] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> ClientRegistration:
    """
    Return persisted or configured client registration; register via DCR when needed.

    Order: operator ``auth.client.id`` override → stored registration → DCR.
    """
    issuer = discovery.issuer
    if config.auth.client.id:
        return ClientRegistration(
            issuer=issuer,
            client_id=config.auth.client.id,
            client_secret=config.auth.client.secret or None,
            redirect_uris=tuple(redirect_uris),
            token_endpoint_auth_method=token_endpoint_auth_method(config),
            registered_at="",
        )

    reg_store = store or RegistrationStore(
        config.auth.registration_store_path,
    )
    existing = reg_store.get(issuer)
    if existing is not None:
        if set(existing.redirect_uris) != set(redirect_uris):
            logger.info(
                "Redirect URIs changed for issuer %s; re-registering via DCR",
                issuer,
            )
        else:
            return existing

    if discovery.authorization_server.registration_endpoint and config.auth.dcr.enabled:
        registration = await register_dynamic_client(
            config,
            discovery,
            redirect_uris=redirect_uris,
            http_client=http_client,
        )
        reg_store.save(registration)
        return registration

    raise McpOAuthConfigurationError(
        "No client_id configured and authorization server does not support DCR. "
        "Set auth.client.id (and optional auth.client.secret) in configuration."
    )
