#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import os
from abc import ABC, abstractmethod


class SecretNotFoundError(KeyError):
    """The requested key does not exist in this provider."""


class TenantSecretNotFoundError(SecretNotFoundError):
    """A tenant-scoped secret is absent. Strict: never falls back to a shared value."""


class SecretProviderError(RuntimeError):
    """The provider itself failed (auth, network, config). Do not swallow."""


class SecretProvider(ABC):
    """
    Abstract port for secret resolution.

    Implementations may use environment variables, a cloud secrets manager,
    or any other secure storage backend.
    """

    @abstractmethod
    def get_secret(self, key: str) -> str:
        """Return the secret value for the given key, or raise SecretNotFoundError."""
        raise NotImplementedError


class EnvSecretProvider(SecretProvider):
    """SecretProvider backed by environment variables.

    Strips surrounding whitespace and quotes from values.
    Tries the key as-is, then uppercased.
    Raises :class:`SecretNotFoundError` if the key is absent (fail-closed).

    Set ``NW_ENV_SECRET_LEGACY_EMPTY=true`` to restore legacy behaviour of returning
    ``""`` when a variable is missing (not recommended for production).
    """

    def __init__(self, *, legacy_empty_on_missing: bool | None = None) -> None:
        self._env = os.environ
        if legacy_empty_on_missing is None:
            legacy_empty_on_missing = os.environ.get("NW_ENV_SECRET_LEGACY_EMPTY", "").lower() in (
                "1",
                "true",
                "yes",
            )
        self._legacy_empty_on_missing = legacy_empty_on_missing

    def get_secret(self, key: str) -> str:
        val = self._env.get(key)
        if val is not None:
            return val.strip(" '\"")
        val = self._env.get(key.upper())
        if val is not None:
            return val.strip(" '\"")
        if self._legacy_empty_on_missing:
            return ""
        raise SecretNotFoundError(key)


def _sanitize_secret_segment(segment: str) -> str:
    """Uppercase and replace every non-alphanumeric char with ``_`` for env names."""
    return "".join(ch if ch.isalnum() else "_" for ch in segment).upper()


class TenantSecretProvider(SecretProvider):
    """Scopes secret lookups to a ``{tenant_id}/{connector_id}/{key}`` path.

    Delegates to an inner :class:`SecretProvider`, translating the logical path to
    the backend key ``NW_{TENANT}_{CONNECTOR}_{KEY}`` (uppercased; non-alphanumeric
    characters become ``_``). Strict: a missing secret raises
    :class:`TenantSecretNotFoundError` rather than resolving a shared value.

    ``key`` is the bare logical name carried by a config's reference field
    (e.g. ``announcement_token``).
    """

    def __init__(self, inner: SecretProvider, tenant_id: str, connector_id: str) -> None:
        self._inner = inner
        self._tenant_id = tenant_id
        self._connector_id = connector_id

    def _scoped_key(self, key: str) -> str:
        return "_".join(
            (
                "NW",
                _sanitize_secret_segment(self._tenant_id),
                _sanitize_secret_segment(self._connector_id),
                _sanitize_secret_segment(key),
            )
        )

    def get_secret(self, key: str) -> str:
        scoped = self._scoped_key(key)
        try:
            return self._inner.get_secret(scoped)
        except SecretNotFoundError as exc:
            raise TenantSecretNotFoundError(
                f"tenant secret not found: {self._tenant_id}/{self._connector_id}/{key} "
                f"(resolved key {scoped!r})"
            ) from exc
