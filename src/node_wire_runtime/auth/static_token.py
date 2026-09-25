#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.auth.static_token
======================================

:class:`StaticTokenAuthProvider` — reads a single secret via
:class:`~node_wire_runtime.secrets.SecretProvider` and injects it as an HTTP
request header.

Suitable for:
  - API-key authentication (e.g. Stripe, generic HTTP connectors)
  - Pre-issued bearer tokens that do not expire (``cache=True``, the default)
  - Host-supplied OAuth access tokens the host rotates out-of-band
    (``cache=False`` — re-read the secret on every call)
  - HTTP Basic authentication (set ``encoding="base64"``)

With ``cache=True`` (default) the secret is fetched **once** and held in memory
for the lifetime of the provider instance. Call :meth:`refresh` or recreate
the provider if the secret is rotated.

With ``cache=False`` every :meth:`get_headers` call re-reads the secret. Whether
that actually surfaces a rotated value depends on the configured
:class:`SecretProvider` — this provider adds no caching of its own, but several
backends cache internally:

  - ``EnvSecretProvider`` / ``OverlaySecretProvider`` resolve live, so a rotated
    value is seen on the next call. This is the case the host-supplied tier
    targets.
  - ``AwsSecretsManagerProvider`` / ``GcpSecretManagerProvider`` /
    ``HashiCorpVaultProvider`` fetch their whole bundle in ``__init__`` and serve
    later reads from memory — re-reading returns the same value until the
    provider itself is recreated.
  - ``AzureKeyVaultProvider`` resolves live, but ``get_secret`` is a *blocking*
    network call: with ``cache=False`` every outbound request performs a Key
    Vault round-trip inside the async path. Prefer ``cache=True`` plus an
    explicit :meth:`refresh` there.
"""

from __future__ import annotations

import base64
import logging
from typing import Dict, Optional

from node_wire_runtime.secrets import SecretProvider

from .base import AuthProvider

logger = logging.getLogger("runtime.auth.static_token")


class StaticTokenAuthProvider(AuthProvider):
    """
    Injects a static secret as an HTTP ``Authorization`` (or custom) header.

    Parameters
    ----------
    secret_provider:
        The runtime :class:`SecretProvider` used to resolve secrets.
    secret_key:
        The secret key passed to ``secret_provider.get_secret()``.
    header_name:
        The HTTP header to set. Default: ``"Authorization"``.
    prefix:
        String prepended to the secret value (with a space separator).
        Pass ``""`` for raw injection (e.g. some proprietary API-key headers).
        Default: ``"Bearer"``.
    encoding:
        Optional encoding applied to the raw secret before injection.
        Currently supports ``"base64"`` (for HTTP Basic auth pairs that are
        already formatted as ``user:password``). Default: ``None``.
    cache:
        When ``True`` (default), resolve the secret once and reuse the header.
        When ``False``, re-read the secret on every :meth:`get_headers` call —
        used for host-supplied OAuth access tokens the host may rotate. Only
        surfaces rotations for live-resolving secret providers (env / overlay);
        see the module docstring for the per-backend caveats.
    """

    def __init__(
        self,
        *,
        secret_provider: SecretProvider,
        secret_key: str,
        header_name: str = "Authorization",
        prefix: str = "Bearer",
        encoding: Optional[str] = None,
        cache: bool = True,
    ) -> None:
        self._secret_provider = secret_provider
        self._secret_key = secret_key
        self._header_name = header_name
        self._prefix = prefix
        self._encoding = encoding
        self._cache = cache
        self._cached_header: Optional[Dict[str, str]] = None

    def _build_header(self) -> Dict[str, str]:
        raw = self._secret_provider.get_secret(self._secret_key)

        if self._encoding == "base64":
            raw = base64.b64encode(raw.encode()).decode()

        value = f"{self._prefix} {raw}".strip() if self._prefix else raw
        return {self._header_name: value}

    async def get_headers(self) -> Dict[str, str]:
        """Return the header dict; cache when ``cache=True``, else re-read."""
        if not self._cache:
            logger.debug(
                "StaticTokenAuthProvider: resolving secret (uncached)",
                extra={"header": self._header_name},
            )
            return self._build_header()
        if self._cached_header is None:
            logger.debug(
                "StaticTokenAuthProvider: resolving secret",
                extra={"header": self._header_name},
            )
            self._cached_header = self._build_header()
        return dict(self._cached_header)

    async def refresh(self) -> None:
        """Invalidate the cached header so the secret is re-read on the next call."""
        logger.debug("StaticTokenAuthProvider: cache invalidated")
        self._cached_header = None
