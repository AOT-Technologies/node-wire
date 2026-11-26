#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from typing import Any, Mapping


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

    #: True when ``get_secret`` does network I/O per call (e.g. Azure Key Vault), so
    #: async callers run it in a worker thread. In-memory providers leave it False.
    blocking_io: bool = False

    @abstractmethod
    def get_secret(self, key: str) -> str:
        """Return the secret value for the given key, or raise SecretNotFoundError."""
        raise NotImplementedError


class SecretStore(ABC):
    """Writable port: the durable home of tenant secrets, kept as one nested document.

    The document is ``{tenant: {connector: {config_name: {logical_key: value}}}}``.
    Simplified: whole-document ``load``/``save`` rather than per-key CRUD; a cloud backend maps
    it to a single JSON bundle.
    """

    @abstractmethod
    def load(self) -> dict[str, Any]:
        """Return the stored document (``{}`` when nothing was stored yet)."""
        raise NotImplementedError

    @abstractmethod
    def save(self, data: Mapping[str, Any]) -> None:
        """Replace the stored document. Must be durable before returning."""
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


def tenant_scoped_secret_key(
    tenant_id: str,
    connector_id: str,
    logical_key: str,
    *,
    config_name: str | None = None,
) -> str:
    """Build ``NW_{TENANT}_{CONNECTOR}_{KEY}`` or ``NW_{TENANT}_{CONNECTOR}_{CONFIG}_{KEY}``.

    When ``config_name`` is set (named multitenant configs), secrets are isolated
    per config. Omit ``config_name`` for legacy tenant+connector scoping.
    """
    parts = [
        "NW",
        _sanitize_secret_segment(tenant_id),
        _sanitize_secret_segment(connector_id),
    ]
    if config_name is not None and str(config_name).strip():
        parts.append(_sanitize_secret_segment(str(config_name).strip()))
    parts.append(_sanitize_secret_segment(logical_key))
    return "_".join(parts)


class OverlaySecretProvider(SecretProvider):
    """In-memory secret map checked before env (tenant credentials overlay).

    Keys match :func:`tenant_scoped_secret_key` (
    ``NW_{TENANT}_{CONNECTOR}_{KEY}`` or ``NW_{TENANT}_{CONNECTOR}_{CONFIG}_{KEY}``).
    Process-wide singleton via :meth:`instance`.

    Several writers share it (tenant secrets, MCP OAuth tokens, rotated refresh tokens).
    A bulk writer names a ``namespace`` and :meth:`replace_all` only replaces the keys
    written under that namespace, so reloading tenant secrets never drops another
    writer's keys. Every read and write takes one lock.
    """

    _instance: "OverlaySecretProvider | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, str] = {}
        # namespace -> keys written under it (see replace_all).
        self._owned: dict[str, set[str]] = {}

    @classmethod
    def instance(cls) -> "OverlaySecretProvider":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_secret(self, key: str) -> str:
        with self._lock:
            val = self._data.get(key)
            if val is None:
                val = self._data.get(key.upper())
        if val is not None:
            return val
        raise SecretNotFoundError(key)

    def set_secret(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value

    def unset(self, key: str) -> None:
        """Remove a single key, if present. No-op if absent — callers never need to
        check existence first."""
        with self._lock:
            self._data.pop(key, None)
            for keys in self._owned.values():
                keys.discard(key)

    def set_many(self, mapping: dict[str, str], *, namespace: str | None = None) -> None:
        """Set every key in ``mapping``; with ``namespace``, those keys join it."""
        with self._lock:
            for k, v in mapping.items():
                self._data[str(k)] = str(v)
            if namespace is not None:
                self._owned.setdefault(namespace, set()).update(str(k) for k in mapping)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._owned.clear()

    def replace_all(self, mapping: dict[str, str], *, namespace: str) -> None:
        """Make ``mapping`` the whole of ``namespace``: its previous keys that are not in
        ``mapping`` are removed; keys outside the namespace are untouched."""
        with self._lock:
            for key in self._owned.get(namespace, set()) - set(map(str, mapping)):
                self._data.pop(key, None)
            self._owned[namespace] = set()
            self.set_many(mapping, namespace=namespace)

    def export(self) -> dict[str, str]:
        with self._lock:
            return dict(self._data)

    def logical_keys_for(
        self,
        tenant_id: str,
        connector_id: str,
        *,
        config_name: str | None = None,
    ) -> list[str]:
        """Return logical key names present for this tenant/connector[/config] scope."""
        parts = [
            "NW",
            _sanitize_secret_segment(tenant_id),
            _sanitize_secret_segment(connector_id),
        ]
        if config_name is not None and str(config_name).strip():
            parts.append(_sanitize_secret_segment(str(config_name).strip()))
        prefix = "_".join(parts) + "_"
        out: list[str] = []
        with self._lock:
            keys = list(self._data)
        for full in keys:
            if not full.startswith(prefix):
                continue
            logical = full[len(prefix) :]
            if logical:
                out.append(logical)
        return sorted(out)


class TenantSecretProvider(SecretProvider):
    """Scopes secret lookups to ``{tenant}/{connector}[/{config}]/{key}``.

    Delegates to an inner :class:`SecretProvider`, translating the logical path to
    ``NW_{TENANT}_{CONNECTOR}_{KEY}`` or ``NW_{TENANT}_{CONNECTOR}_{CONFIG}_{KEY}``.
    Strict: a missing secret raises :class:`TenantSecretNotFoundError`.

    ``key`` is the bare logical name carried by a config's reference field
    (e.g. ``GOOGLE_DRIVE_SA_JSON``).
    """

    def __init__(
        self,
        inner: SecretProvider,
        tenant_id: str,
        connector_id: str,
        *,
        config_name: str | None = None,
    ) -> None:
        self._inner = inner
        self._tenant_id = tenant_id
        self._connector_id = connector_id
        self._config_name = (config_name or "").strip() or None

    @property
    def blocking_io(self) -> bool:  # type: ignore[override]
        return getattr(self._inner, "blocking_io", False)

    def _scoped_key(self, key: str) -> str:
        return tenant_scoped_secret_key(
            self._tenant_id,
            self._connector_id,
            key,
            config_name=self._config_name,
        )

    def get_secret(self, key: str) -> str:
        scoped = self._scoped_key(key)
        try:
            return self._inner.get_secret(scoped)
        except SecretNotFoundError as exc:
            scope = f"{self._tenant_id}/{self._connector_id}"
            if self._config_name:
                scope = f"{scope}/{self._config_name}"
            raise TenantSecretNotFoundError(
                f"tenant secret not found: {scope}/{key} (resolved key {scoped!r})"
            ) from exc
