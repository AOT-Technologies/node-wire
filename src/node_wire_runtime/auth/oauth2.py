"""
node_wire_runtime.auth.oauth2
================================

:class:`OAuth2AuthProvider` — implements the OAuth 2.0 Client Credentials
grant with two assertion methods:

  ``private_key_jwt``   — SMART Backend Services / RS384 JWT-bearer assertion.
                           Used by Epic and Cerner FHIR endpoints.
  ``client_secret_post`` — standard ``client_id`` + ``client_secret`` POST body.

Security design:
  - Tokens are cached in memory using the ``expires_in`` value from the
    token response, minus a configurable buffer (default 60 s).
  - An ``asyncio.Lock`` serialises concurrent refresh calls to prevent
    the thundering-herd problem under high concurrency.
  - ``refresh()`` clears the cache so callers can force re-issue after a 401.
  - Private keys, client IDs, and token URLs are resolved at call-time via
    :class:`~node_wire_runtime.secrets.SecretProvider` so they are never held
    in plain text in config files.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx
import jwt

from node_wire_runtime.secrets import SecretProvider

from .base import AuthProvider

logger = logging.getLogger("runtime.auth.oauth2")

_DEFAULT_BUFFER_SECS = 60
_DEFAULT_TOKEN_TTL_SECS = 3600  # fallback when expires_in absent from response


class OAuth2AuthProvider(AuthProvider):
    """
    OAuth 2.0 Client Credentials provider with token caching.

    Parameters
    ----------
    secret_provider:
        Runtime :class:`SecretProvider` used to resolve all secret references.
    grant_method:
        ``"private_key_jwt"`` (default) or ``"client_secret_post"``.
    token_url_secret:
        Secret key whose value is the token endpoint URL.
    client_id_secret:
        Secret key whose value is ``client_id``.
    algorithm:
        JWT signing algorithm. Default: ``"RS384"`` (required by SMART).
    private_key_secret:
        *(private_key_jwt only)* Secret key for the PEM private key.
    kid_secret:
        *(private_key_jwt only)* Secret key for the JWT ``kid`` header.
    client_secret_secret:
        *(client_secret_post only)* Secret key for ``client_secret``.
    scopes:
        List of OAuth scopes. If ``None``, no ``scope`` param is sent.
    scopes_secret:
        Alternative: secret key whose value is a space-separated scope string.
        Overrides ``scopes`` if set.
    extra_content_type_headers:
        Additional fixed headers merged into the response (e.g. FHIR content-type).
        Default: ``{"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}``.
    buffer_secs:
        Seconds before ``expires_in`` to treat the token as expired.
        Default: 60.
    jwt_ttl_secs:
        Lifetime of the JWT assertion in seconds. Default: 300.
    """

    def __init__(
        self,
        *,
        secret_provider: SecretProvider,
        grant_method: str = "private_key_jwt",
        token_url_secret: str,
        client_id_secret: str,
        algorithm: str = "RS384",
        private_key_secret: Optional[str] = None,
        kid_secret: Optional[str] = None,
        client_secret_secret: Optional[str] = None,
        refresh_token_secret: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        scopes_secret: Optional[str] = None,
        extra_content_type_headers: Optional[Dict[str, str]] = None,
        buffer_secs: int = _DEFAULT_BUFFER_SECS,
        jwt_ttl_secs: int = 300,
    ) -> None:
        if grant_method not in ("private_key_jwt", "client_secret_post", "refresh_token"):
            raise ValueError(
                f"Unsupported grant_method {grant_method!r}. "
                "Use 'private_key_jwt', 'client_secret_post', or 'refresh_token'."
            )
        self._sp = secret_provider
        self._grant_method = grant_method
        self._token_url_secret = token_url_secret
        self._client_id_secret = client_id_secret
        self._algorithm = algorithm
        self._private_key_secret = private_key_secret
        self._kid_secret = kid_secret
        self._client_secret_secret = client_secret_secret
        self._refresh_token_secret = refresh_token_secret

        self._static_scopes = scopes
        self._scopes_secret = scopes_secret
        self._extra_headers: Dict[str, str] = (
            extra_content_type_headers
            if extra_content_type_headers is not None
            else {
                "Content-Type": "application/fhir+json",
                "Accept": "application/fhir+json",
            }
        )
        self._buffer_secs = buffer_secs
        self._jwt_ttl_secs = jwt_ttl_secs

        # Cache state — protected by _lock.
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_headers(self) -> Dict[str, str]:
        """
        Return ``Authorization: Bearer <token>`` plus any extra fixed headers.

        The token is fetched from the upstream IdP only when necessary
        (first call, expiry, or after an explicit :meth:`refresh` call).
        Concurrent callers block on an ``asyncio.Lock`` so only one HTTP
        request is issued per refresh cycle.
        """
        token = await self._get_or_refresh_token()
        headers: Dict[str, str] = {"Authorization": f"Bearer {token}"}
        headers.update(self._extra_headers)
        return headers

    async def refresh(self) -> None:
        """
        Invalidate the cached token.

        Call this after receiving a 401/403 so the next :meth:`get_headers`
        call fetches a fresh token instead of reusing the (now-rejected) one.
        """
        async with self._lock:
            logger.debug("OAuth2AuthProvider: cache invalidated by refresh()")
            self._access_token = None
            self._expires_at = 0.0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_valid(self) -> bool:
        """Return True if the cached token is still within its TTL window."""
        return (
            self._access_token is not None
            and time.monotonic() < self._expires_at - self._buffer_secs
        )

    async def _get_or_refresh_token(self) -> str:
        """
        Return a valid access token, fetching one if necessary.

        Uses a double-checked locking pattern:
          1. Fast-path: check validity outside the lock (no contention).
          2. Acquire lock and re-check (another coroutine may have refreshed).
          3. Fetch if still invalid.
        """
        # Fast path — no lock, no contention.
        if self._is_valid():
            return self._access_token  # type: ignore[return-value]

        async with self._lock:
            # Re-check after acquiring the lock.
            if self._is_valid():
                return self._access_token  # type: ignore[return-value]

            logger.debug(
                "OAuth2AuthProvider: fetching new access token",
                extra={"grant_method": self._grant_method},
            )
            token_data = await self._fetch_token()

            access_token = token_data.get("access_token")
            if not access_token:
                raise ValueError(
                    "OAuth2 token response did not contain an 'access_token'. "
                    f"Response keys: {list(token_data.keys())}"
                )

            expires_in = int(token_data.get("expires_in", _DEFAULT_TOKEN_TTL_SECS))
            self._access_token = access_token
            self._expires_at = time.monotonic() + expires_in

            logger.debug(
                "OAuth2AuthProvider: token cached",
                extra={"expires_in": expires_in, "buffer_secs": self._buffer_secs},
            )
            return self._access_token

    def _resolve_scopes(self) -> Optional[str]:
        """Resolve the scope string from secret or static list. Returns None if absent."""
        if self._scopes_secret:
            try:
                val = self._sp.get_secret(self._scopes_secret)
                if val and val.strip():
                    return val.strip()
            except Exception:
                pass
        if self._static_scopes:
            return " ".join(self._static_scopes)
        return None

    async def _fetch_token(self) -> Dict[str, Any]:
        """Dispatch to the appropriate grant method implementation."""
        if self._grant_method == "private_key_jwt":
            return await self._fetch_private_key_jwt()
        if self._grant_method == "refresh_token":
            return await self._fetch_refresh_token()
        return await self._fetch_client_secret_post()

    async def _fetch_refresh_token(self) -> Dict[str, Any]:
        """Exchange refresh_token for a new access token."""
        if not self._refresh_token_secret:
            raise ValueError(
                "OAuth2AuthProvider (refresh_token): 'refresh_token_secret' must be configured."
            )

        client_id = self._sp.get_secret(self._client_id_secret)
        client_secret = (
            self._sp.get_secret(self._client_secret_secret) if self._client_secret_secret else None
        )
        refresh_token = self._sp.get_secret(self._refresh_token_secret)
        token_url = self._sp.get_secret(self._token_url_secret)

        post_data: Dict[str, str] = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        }
        if client_secret:
            post_data["client_secret"] = client_secret

        scope = self._resolve_scopes()
        if scope:
            post_data["scope"] = scope

        logger.debug(
            "OAuth2AuthProvider: refresh_token token request",
            extra={"token_url": token_url},
        )
        return await self._post_token(token_url, post_data)

    async def _fetch_private_key_jwt(self) -> Dict[str, Any]:
        """
        Exchange a signed JWT assertion for an access token.

        Follows RFC 7523 / SMART Backend Services specification.
        """
        if not self._private_key_secret or not self._kid_secret:
            raise ValueError(
                "OAuth2AuthProvider (private_key_jwt): "
                "both 'private_key_secret' and 'kid_secret' must be configured."
            )

        private_key_raw = self._sp.get_secret(self._private_key_secret)
        kid = self._sp.get_secret(self._kid_secret)
        client_id = self._sp.get_secret(self._client_id_secret)
        token_url = self._sp.get_secret(self._token_url_secret)

        # Normalise PEM keys stored as single-line env vars with escaped newlines.
        private_key_pem = (
            private_key_raw.replace("\\n", "\n") if "\\n" in private_key_raw else private_key_raw
        )

        now = int(time.time())
        claims: Dict[str, Any] = {
            "iss": client_id,
            "sub": client_id,
            "aud": token_url,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "nbf": now,
            "exp": now + self._jwt_ttl_secs,
        }

        scope = self._resolve_scopes()
        if scope:
            claims["scope"] = scope

        jwt_token = jwt.encode(
            claims,
            private_key_pem,
            algorithm=self._algorithm,
            headers={"alg": self._algorithm, "typ": "JWT", "kid": kid},
        )

        post_data: Dict[str, str] = {
            "grant_type": "client_credentials",
            "client_assertion_type": ("urn:ietf:params:oauth:client-assertion-type:jwt-bearer"),
            "client_assertion": jwt_token,
        }
        if scope:
            post_data["scope"] = scope

        logger.debug(
            "OAuth2AuthProvider: private_key_jwt token request",
            extra={"token_url": token_url, "client_id": client_id},
        )
        return await self._post_token(token_url, post_data)

    async def _fetch_client_secret_post(self) -> Dict[str, Any]:
        """Exchange client_id + client_secret for an access token."""
        if not self._client_secret_secret:
            raise ValueError(
                "OAuth2AuthProvider (client_secret_post): "
                "'client_secret_secret' must be configured."
            )

        client_id = self._sp.get_secret(self._client_id_secret)
        client_secret = self._sp.get_secret(self._client_secret_secret)
        token_url = self._sp.get_secret(self._token_url_secret)

        post_data: Dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        scope = self._resolve_scopes()
        if scope:
            post_data["scope"] = scope

        logger.debug(
            "OAuth2AuthProvider: client_secret_post token request",
            extra={"token_url": token_url},
        )
        return await self._post_token(token_url, post_data)

    @staticmethod
    async def _post_token(token_url: str, data: Dict[str, str]) -> Dict[str, Any]:
        """POST to the token endpoint and return the parsed JSON body."""
        timeout = float(os.getenv("NW_TIMEOUT", "30.0"))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.status_code != 200:
            logger.error(
                "OAuth2 token request failed | status=%s | body_length=%s",
                response.status_code,
                len(response.text or ""),
            )
            response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]
