#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.auth.service_account
=========================================

:class:`ServiceAccountAuthProvider` — Google service-account authentication.

Instead of injecting HTTP headers, this provider supplies a
``google.oauth2.credentials.Credentials`` object via
:meth:`get_client_credentials`, which vendor SDKs (e.g. ``google-api-python-client``)
consume directly.

The service-account JSON is stored as a secret and resolved at first use.
Credentials are refreshed automatically by the Google auth library when they
expire; this provider does not implement its own TTL.

By default this provider returns an empty dict from :meth:`get_headers`, because an SDK
connector authenticates at the SDK level, not via HTTP headers. A REST connector (one that
sends its own requests, such as the generated Google Drive connector) sets
``bearer_header=True``: :meth:`get_headers` then returns ``Authorization: Bearer <access token>``,
with the token fetched from Google and refreshed when it expires.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from node_wire_runtime.secrets import SecretProvider

from .base import AuthProvider, read_secrets_off_loop

logger = logging.getLogger("runtime.auth.service_account")


class ServiceAccountAuthProvider(AuthProvider):
    """
    Google service-account credentials provider.

    Parameters
    ----------
    secret_provider:
        Runtime :class:`SecretProvider` used to resolve the service-account JSON.
    sa_json_secret:
        Secret key whose value is either:
        - A JSON string containing the full service-account key, **or**
        - A filesystem path to the service-account JSON file.
    scopes:
        List of OAuth2 scopes to request. Default:
        ``["https://www.googleapis.com/auth/drive"]``.
    bearer_header:
        When true, :meth:`get_headers` returns ``Authorization: Bearer <access token>``
        for a REST connector. Default false: no headers (SDK connectors).
    """

    def __init__(
        self,
        *,
        secret_provider: SecretProvider,
        sa_json_secret: str,
        scopes: Optional[List[str]] = None,
        bearer_header: bool = False,
    ) -> None:
        self._bearer_header = bearer_header
        self._refresh_lock = asyncio.Lock()
        self._sp = secret_provider
        self._sa_json_secret = sa_json_secret
        self._scopes = scopes or ["https://www.googleapis.com/auth/drive"]
        self._credentials: Any = None

    def _build_credentials(self) -> Any:
        """
        Resolve the service-account secret and return a
        ``google.oauth2.service_account.Credentials`` object.

        Supports both inline JSON and a file-path fallback for local development.
        The import is deferred so that packages without the Google libraries
        installed do not fail at import time.
        """
        try:
            from google.oauth2 import service_account  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "ServiceAccountAuthProvider requires 'google-auth'. "
                "Install it with: pip install google-auth"
            ) from exc

        raw = self._sp.get_secret(self._sa_json_secret)
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            # Not inline JSON — treat as a file path.
            path = raw.strip()
            try:
                creds = service_account.Credentials.from_service_account_file(
                    path, scopes=self._scopes
                )
            except FileNotFoundError:
                raise ValueError(
                    f"Google service account file not found at path {path!r}. "
                    f"The secret '{self._sa_json_secret}' must be either a valid service account "
                    f"JSON string or a path to an existing key file."
                ) from None
            except Exception as exc:
                raise ValueError(
                    f"Failed to load Google service account credentials from file {path!r}: {exc}. "
                    f"Ensure the file contains a valid service account key in JSON format."
                ) from exc
        else:
            try:
                creds = service_account.Credentials.from_service_account_info(
                    info, scopes=self._scopes
                )
            except Exception as exc:
                raise ValueError(
                    f"Google service account JSON (secret '{self._sa_json_secret}') is invalid "
                    f"or missing required fields: {exc}"
                ) from exc

        logger.debug(
            "ServiceAccountAuthProvider: credentials built",
            extra={"scopes": self._scopes},
        )
        return creds

    @staticmethod
    def _refresh_token(creds: Any) -> None:
        """Fetch an access token from Google (blocking; call it off the event loop)."""
        try:
            from google.auth.transport.requests import Request
        except ImportError as exc:
            raise ImportError(
                "ServiceAccountAuthProvider(bearer_header=True) requires 'google-auth' and 'requests'."
            ) from exc
        creds.refresh(Request())

    async def get_headers(self) -> Dict[str, str]:
        """
        Return ``Authorization: Bearer <token>`` when ``bearer_header`` is set, else an empty dict.

        An SDK connector gets its credentials from :meth:`get_client_credentials` and needs no
        headers. A REST connector needs the header, so the token is fetched here and refreshed when
        it has expired. Concurrent callers wait for one refresh.
        """
        if not self._bearer_header:
            return {}
        creds = await self.get_client_credentials()
        if not creds.valid:
            async with self._refresh_lock:
                if not creds.valid:  # another caller may have refreshed while this one waited
                    await asyncio.to_thread(self._refresh_token, creds)
        return {"Authorization": f"Bearer {creds.token}"}

    async def get_client_credentials(self) -> Any:
        """
        Return a ``google.oauth2.service_account.Credentials`` instance.

        The credentials object is built once and cached for the lifetime of
        this provider. The Google auth library handles token refresh internally.
        """
        if self._credentials is None:
            self._credentials = await read_secrets_off_loop(self._sp, self._build_credentials)
        return self._credentials

    async def refresh(self) -> None:
        """
        Invalidate the cached credentials object.

        Forces :meth:`get_client_credentials` to rebuild from the secret on the
        next call, picking up any rotated service-account JSON.
        """
        logger.debug("ServiceAccountAuthProvider: credentials cache invalidated")
        self._credentials = None
