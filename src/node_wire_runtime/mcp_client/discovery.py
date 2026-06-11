#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""RFC 9728 Protected Resource Metadata and RFC 8414 Authorization Server Metadata."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import httpx

from .config import McpClientConfig, canonicalize_mcp_server_url
from .exceptions import McpOAuthDiscoveryError

logger = logging.getLogger("runtime.mcp_client.discovery")

_RESOURCE_METADATA_RE = re.compile(
    r'resource_metadata\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProtectedResourceMetadata:
    """RFC 9728 document for an MCP protected resource."""

    resource: str
    authorization_servers: tuple[str, ...]
    raw: Dict[str, Any]

    @classmethod
    def from_json(
        cls, data: Dict[str, Any], *, fallback_resource: str
    ) -> ProtectedResourceMetadata:
        servers = data.get("authorization_servers")
        if not isinstance(servers, list) or not servers:
            raise McpOAuthDiscoveryError(
                "Protected Resource Metadata missing authorization_servers"
            )
        resource = str(data.get("resource") or fallback_resource)
        return cls(
            resource=resource,
            authorization_servers=tuple(str(s) for s in servers),
            raw=dict(data),
        )


@dataclass(frozen=True)
class AuthorizationServerMetadata:
    """RFC 8414 / OpenID Provider Metadata subset used by the MCP client."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: Optional[str]
    scopes_supported: Optional[tuple[str, ...]]
    raw: Dict[str, Any]

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> AuthorizationServerMetadata:
        issuer = data.get("issuer")
        authz = data.get("authorization_endpoint")
        token = data.get("token_endpoint")
        if not issuer or not authz or not token:
            raise McpOAuthDiscoveryError(
                "Authorization Server Metadata missing issuer, authorization_endpoint, "
                "or token_endpoint"
            )
        scopes = data.get("scopes_supported")
        scopes_tuple: Optional[tuple[str, ...]] = None
        if isinstance(scopes, list):
            scopes_tuple = tuple(str(s) for s in scopes)
        reg = data.get("registration_endpoint")
        return cls(
            issuer=str(issuer).rstrip("/"),
            authorization_endpoint=str(authz),
            token_endpoint=str(token),
            registration_endpoint=str(reg) if reg else None,
            scopes_supported=scopes_tuple,
            raw=dict(data),
        )


@dataclass
class DiscoveryResult:
    """Combined discovery output for one MCP server URL."""

    mcp_server_url: str
    protected_resource: ProtectedResourceMetadata
    authorization_server: AuthorizationServerMetadata
    issuer: str


@dataclass
class _CacheEntry:
    result: DiscoveryResult
    expires_at: float


class DiscoveryCache:
    """In-memory discovery cache keyed by canonical MCP server URL."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = max(1, ttl_seconds)
        self._entries: Dict[str, _CacheEntry] = {}

    def get(self, mcp_server_url: str) -> Optional[DiscoveryResult]:
        key = canonicalize_mcp_server_url(mcp_server_url)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del self._entries[key]
            return None
        return entry.result

    def set(self, result: DiscoveryResult) -> None:
        key = canonicalize_mcp_server_url(result.mcp_server_url)
        self._entries[key] = _CacheEntry(
            result=result,
            expires_at=time.monotonic() + self._ttl,
        )

    def invalidate(self, mcp_server_url: str) -> None:
        key = canonicalize_mcp_server_url(mcp_server_url)
        self._entries.pop(key, None)


def parse_resource_metadata_url(www_authenticate: Optional[str]) -> Optional[str]:
    """Extract ``resource_metadata`` URL from a WWW-Authenticate Bearer challenge."""
    if not www_authenticate:
        return None
    match = _RESOURCE_METADATA_RE.search(www_authenticate)
    if match:
        return match.group(1).strip()
    return None


def protected_resource_metadata_well_known_url(mcp_server_url: str) -> str:
    """Derive PRM URL when no WWW-Authenticate challenge is available."""
    parsed = urlsplit(canonicalize_mcp_server_url(mcp_server_url))
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return urljoin(origin + "/", ".well-known/oauth-protected-resource")


def authorization_server_metadata_urls(issuer: str) -> List[str]:
    """RFC 8414 primary URL, then OpenID Configuration fallback."""
    base = issuer.rstrip("/")
    return [
        f"{base}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration",
    ]


def select_issuer(
    authorization_servers: tuple[str, ...],
    *,
    override: Optional[str] = None,
) -> str:
    if override:
        normalized = override.rstrip("/")
        allowed = {s.rstrip("/") for s in authorization_servers}
        if normalized not in allowed:
            raise McpOAuthDiscoveryError(
                f"Configured issuer override {override!r} is not listed in "
                f"authorization_servers: {list(authorization_servers)}"
            )
        return normalized
    return authorization_servers[0].rstrip("/")


async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    context: str,
) -> Dict[str, Any]:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise McpOAuthDiscoveryError(f"{context}: HTTP error fetching {url}") from exc
    except ValueError as exc:
        raise McpOAuthDiscoveryError(f"{context}: invalid JSON from {url}") from exc
    if not isinstance(data, dict):
        raise McpOAuthDiscoveryError(f"{context}: expected JSON object from {url}")
    return data


async def fetch_protected_resource_metadata(
    client: httpx.AsyncClient,
    mcp_server_url: str,
    *,
    www_authenticate: Optional[str] = None,
) -> ProtectedResourceMetadata:
    prm_url = parse_resource_metadata_url(www_authenticate)
    if not prm_url:
        prm_url = protected_resource_metadata_well_known_url(mcp_server_url)
    data = await fetch_json(
        client,
        prm_url,
        context="Protected Resource Metadata",
    )
    return ProtectedResourceMetadata.from_json(
        data,
        fallback_resource=canonicalize_mcp_server_url(mcp_server_url),
    )


async def fetch_authorization_server_metadata(
    client: httpx.AsyncClient,
    issuer: str,
) -> AuthorizationServerMetadata:
    errors: List[str] = []
    for url in authorization_server_metadata_urls(issuer):
        try:
            data = await fetch_json(
                client,
                url,
                context="Authorization Server Metadata",
            )
            meta = AuthorizationServerMetadata.from_json(data)
            if meta.issuer.rstrip("/") != issuer.rstrip("/"):
                raise McpOAuthDiscoveryError(
                    f"Issuer mismatch: metadata issuer {meta.issuer!r} != expected {issuer!r}"
                )
            return meta
        except McpOAuthDiscoveryError as exc:
            errors.append(str(exc))
            continue
    raise McpOAuthDiscoveryError(
        f"Could not fetch Authorization Server Metadata for {issuer!r}: {'; '.join(errors)}"
    )


async def discover(
    config: McpClientConfig,
    *,
    www_authenticate: Optional[str] = None,
    cache: Optional[DiscoveryCache] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> DiscoveryResult:
    """
    Full discovery chain: PRM (RFC 9728) then AS metadata (RFC 8414).

    Uses cache when provided and not expired. Pass ``www_authenticate`` from an MCP 401.
    """
    mcp_url = config.canonical_server_url
    if cache is not None:
        cached = cache.get(mcp_url)
        if cached is not None:
            return cached

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0, verify=True)
    try:
        prm = await fetch_protected_resource_metadata(
            client,
            mcp_url,
            www_authenticate=www_authenticate,
        )
        issuer = select_issuer(
            prm.authorization_servers,
            override=config.auth.issuer_override,
        )
        as_meta = await fetch_authorization_server_metadata(client, issuer)
        result = DiscoveryResult(
            mcp_server_url=mcp_url,
            protected_resource=prm,
            authorization_server=as_meta,
            issuer=issuer,
        )
        if cache is not None:
            cache.set(result)
        return result
    finally:
        if own_client:
            await client.aclose()


def discovery_cache_for_config(config: McpClientConfig) -> DiscoveryCache:
    return DiscoveryCache(config.auth.discovery.cache_ttl_seconds)
