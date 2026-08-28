#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""API key authentication via query-string parameter."""

from __future__ import annotations

import logging
from typing import Dict, Optional

from node_wire_runtime.secrets import SecretProvider

from .base import AuthProvider

logger = logging.getLogger("runtime.auth.apikey_query")


class ApiKeyQueryAuthProvider(AuthProvider):
    """Injects an API key as a query parameter (``get_headers`` returns ``{}``).

    Parameters
    ----------
    secret_provider:
        Runtime secret resolver.
    secret_key:
        Key passed to ``secret_provider.get_secret()``.
    name:
        Query parameter name (OpenAPI ``apiKey`` ``name``).
    """

    def __init__(
        self,
        *,
        secret_provider: SecretProvider,
        secret_key: str,
        name: str,
    ) -> None:
        self._secret_provider = secret_provider
        self._secret_key = secret_key
        self._name = name
        self._cached: Optional[Dict[str, str]] = None

    async def get_headers(self) -> Dict[str, str]:
        return {}

    async def get_query_params(self) -> Dict[str, str]:
        if self._cached is None:
            logger.debug(
                "ApiKeyQueryAuthProvider: resolving secret",
                extra={"param": self._name},
            )
            secret = self._secret_provider.get_secret(self._secret_key)
            self._cached = {self._name: secret}
        return dict(self._cached)

    async def refresh(self) -> None:
        self._cached = None
