from __future__ import annotations

import os
from abc import ABC, abstractmethod


class SecretNotFoundError(KeyError):
    """The requested key does not exist in this provider."""


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
