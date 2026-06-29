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
  - Pre-issued bearer tokens that do not expire
  - HTTP Basic authentication (set ``encoding="base64"``)

The secret is fetched **once** and held in memory for the lifetime of the
provider instance. Because these secrets are long-lived and do not expire, no
TTL or refresh mechanism is implemented — tear down and recreate the provider
if the secret is rotated.
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
    """

    def __init__(
        self,
        *,
        secret_provider: SecretProvider,
        secret_key: str,
        header_name: str = "Authorization",
        prefix: str = "Bearer",
        encoding: Optional[str] = None,
    ) -> None:
        self._secret_provider = secret_provider
        self._secret_key = secret_key
        self._header_name = header_name
        self._prefix = prefix
        self._encoding = encoding
        self._cached_header: Optional[Dict[str, str]] = None

    def _build_header(self) -> Dict[str, str]:
        raw = self._secret_provider.get_secret(self._secret_key)

        if self._encoding == "base64":
            raw = base64.b64encode(raw.encode()).decode()

        value = f"{self._prefix} {raw}".strip() if self._prefix else raw
        return {self._header_name: value}

    async def get_headers(self) -> Dict[str, str]:
        """Return the header dict, computing it once and caching thereafter."""
        if self._cached_header is None:
            logger.debug(
                "StaticTokenAuthProvider: resolving secret",
                extra={"secret_key": self._secret_key, "header": self._header_name},
            )
            self._cached_header = self._build_header()
        return dict(self._cached_header)

    async def refresh(self) -> None:
        """Invalidate the cached header so the secret is re-read on the next call."""
        logger.debug(
            "StaticTokenAuthProvider: cache invalidated",
            extra={"secret_key": self._secret_key},
        )
        self._cached_header = None
