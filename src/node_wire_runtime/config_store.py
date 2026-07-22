#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.config_store
==============================

In-memory connector configuration store for header-based multi-tenancy.

The embedding application pushes connector configuration at runtime (there is no
file/DB/Vault read at request time). Each ``(tenant_id, connector_id)`` scope may
hold any number of uniquely named configs; exactly one is the default.

The store is plain in-process memory and thread-safe. Every mutating call
synchronously invalidates the affected factory instances via a factory attached
with :meth:`ConnectorConfigStore.attach_factory` (same process, so no TTL and no
change-detection polling).

Redaction: inline secret-bearing fields (suffix markers below) are masked on every
read surface (:meth:`get`, :meth:`list`). Only the factory's internal
:meth:`resolve` sees the unredacted record.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_TENANT = "__default__"

# Inline secret fields end with one of these suffixes; reference fields end with
# ``_secret`` / ``_key`` and carry bare logical names (never redacted).
SECRET_FIELD_MARKERS = (
    "_value",
    "_secret_value",
    "password",
    "token_value",
    "private_key_value",
)

_REDACTION_MASK = "\u2022" * 8  # eight bullet characters


class ConfigStoreError(Exception):
    """Base class for config store errors."""


class ConfigNotFoundError(ConfigStoreError):
    """No config exists for the scope, or the named config is unknown.

    Deliberately raised for both cases so config names cannot be enumerated
    (unknown scope and unknown name are indistinguishable to the caller).
    """


class ConfigNameConflictError(ConfigStoreError):
    """A config with the same name already exists in the scope."""


class DefaultDeletionError(ConfigStoreError):
    """Deleting the current default requires nominating ``new_default``."""


@dataclass
class ConfigRecord:
    """A single resolved config for one ``(tenant, connector)`` scope.

    ``raw`` is the full config document (``config`` / ``auth`` blocks). ``exposed_via``
    carries protocol gating from the YAML bootstrap; it is internal and not part of
    the public config-document shape.
    """

    tenant_id: str
    connector_id: str
    name: str
    default: bool
    raw: Dict[str, Any]
    exposed_via: List[str] = field(default_factory=list)


def redact(doc: Any) -> Any:
    """Return a deep copy of ``doc`` with inline secret values masked.

    Reference fields (bare logical names) pass through unchanged. Applied to
    every read surface: GET, list responses, logs, error messages.
    """
    if isinstance(doc, dict):
        out: Dict[str, Any] = {}
        for k, v in doc.items():
            if isinstance(v, (dict, list)):
                out[k] = redact(v)
            elif isinstance(k, str) and any(k.endswith(m) for m in SECRET_FIELD_MARKERS):
                out[k] = _REDACTION_MASK
            else:
                out[k] = v
        return out
    if isinstance(doc, list):
        return [redact(item) for item in doc]
    return doc


def _validate_doc(doc: Any) -> Dict[str, Any]:
    """Validate a single config document at the trust boundary.

    Callers (bindings) may pass arbitrary JSON; enforce shape here rather than
    failing obscurely deeper in the factory.
    """
    if not isinstance(doc, dict):
        raise ConfigStoreError("config document must be a JSON object")
    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigStoreError("config document requires a non-empty string 'name'")
    if "default" in doc and not isinstance(doc["default"], bool):
        raise ConfigStoreError("config 'default' must be a boolean")
    if "config" in doc and not isinstance(doc["config"], dict):
        raise ConfigStoreError("config 'config' block must be a JSON object")
    if "auth" in doc and not isinstance(doc["auth"], dict):
        raise ConfigStoreError("config 'auth' block must be a JSON object")
    return doc


class ConnectorConfigStore:
    """In-memory store. Thread-safe. Every write synchronously invalidates the
    affected factory instances (same process, so no TTL and no polling)."""

    def __init__(self) -> None:
        # tenant_id -> connector_id -> name -> ConfigRecord (insertion-ordered)
        self._data: Dict[str, Dict[str, Dict[str, ConfigRecord]]] = {}
        self._lock = threading.RLock()
        self._factory: Any = None

    def attach_factory(self, factory: Any) -> None:
        """Attach the factory that receives synchronous invalidation on writes."""
        self._factory = factory

    # ---- write path -----------------------------------------------------

    def init(self, payload: Dict[str, Any]) -> None:
        """Bulk load: replaces ALL configs.

        Shape: ``{ tenant_id: { connector_id: [ config_doc, ... ] } }``. Exactly
        one doc per ``(tenant, connector)`` may set ``default=true``; if none does,
        the first in the list becomes default. Invalidates every previously cached
        instance.
        """
        if not isinstance(payload, dict):
            raise ConfigStoreError("init payload must be a JSON object")

        new_data: Dict[str, Dict[str, Dict[str, ConfigRecord]]] = {}
        for tenant_id, connectors in payload.items():
            if not isinstance(connectors, dict):
                raise ConfigStoreError(
                    f"init payload for tenant {tenant_id!r} must be a JSON object"
                )
            tenant_map: Dict[str, Dict[str, ConfigRecord]] = {}
            for connector_id, docs in connectors.items():
                if not isinstance(docs, list):
                    raise ConfigStoreError(
                        f"init payload for {tenant_id!r}/{connector_id!r} must be a list"
                    )
                tenant_map[connector_id] = self._build_scope(tenant_id, connector_id, docs)
            new_data[tenant_id] = tenant_map

        with self._lock:
            # Collect the full previous key set so init() invalidates everything.
            previous: List[tuple[str, str, str]] = []
            for tenant_id, connectors in self._data.items():
                for connector_id, records in connectors.items():
                    for name in records:
                        previous.append((tenant_id, connector_id, name))
            self._data = new_data
            for tenant_id, connector_id, name in previous:
                self._invalidate(tenant_id, connector_id, [name])

    def _build_scope(
        self, tenant_id: str, connector_id: str, docs: List[Any]
    ) -> Dict[str, ConfigRecord]:
        """Build the name -> record map for one scope, enforcing default rules."""
        records: Dict[str, ConfigRecord] = {}
        default_seen: Optional[str] = None
        for doc in docs:
            doc = _validate_doc(doc)
            name = doc["name"]
            if name in records:
                raise ConfigNameConflictError(
                    f"duplicate config name {name!r} for {tenant_id!r}/{connector_id!r}"
                )
            is_default = bool(doc.get("default", False))
            if is_default and default_seen is not None:
                raise ConfigStoreError(
                    f"more than one default config for {tenant_id!r}/{connector_id!r} "
                    f"({default_seen!r} and {name!r})"
                )
            if is_default:
                default_seen = name
            records[name] = ConfigRecord(
                tenant_id=tenant_id,
                connector_id=connector_id,
                name=name,
                default=is_default,
                raw=copy.deepcopy(doc),
                exposed_via=list(doc.get("exposed_via", []) or []),
            )
        if records and default_seen is None:
            # First config becomes the default when none is explicitly marked.
            first_name = next(iter(records))
            records[first_name].default = True
        return records

    def create(self, tenant_id: str, connector_id: str, doc: Dict[str, Any]) -> ConfigRecord:
        """Add one named config. Name must be unique in scope. The first config
        for a scope becomes the default automatically."""
        doc = _validate_doc(doc)
        name = doc["name"]
        with self._lock:
            scope = self._data.setdefault(tenant_id, {}).setdefault(connector_id, {})
            if name in scope:
                raise ConfigNameConflictError(
                    f"config {name!r} already exists for {tenant_id!r}/{connector_id!r}"
                )
            is_first = len(scope) == 0
            requested_default = bool(doc.get("default", False))
            make_default = is_first or requested_default
            record = ConfigRecord(
                tenant_id=tenant_id,
                connector_id=connector_id,
                name=name,
                default=make_default,
                raw=copy.deepcopy(doc),
                exposed_via=list(doc.get("exposed_via", []) or []),
            )
            if make_default:
                for other in scope.values():
                    other.default = False
            scope[name] = record
            self._invalidate(tenant_id, connector_id, [name])
            return record

    def update(
        self, tenant_id: str, connector_id: str, name: str, doc: Dict[str, Any]
    ) -> ConfigRecord:
        """Replace the named config's contents. The name itself is immutable
        (delete + create to rename), keeping instance keys unambiguous."""
        doc = _validate_doc(doc)
        if doc["name"] != name:
            raise ConfigStoreError(
                f"config name is immutable: cannot rename {name!r} to {doc['name']!r} "
                "(delete + create instead)"
            )
        with self._lock:
            record = self._require(tenant_id, connector_id, name)
            record.raw = copy.deepcopy(doc)
            record.exposed_via = list(doc.get("exposed_via", []) or [])
            # 'default' flag is managed via set_default/delete, not update.
            self._invalidate(tenant_id, connector_id, [name])
            return record

    def set_default(self, tenant_id: str, connector_id: str, name: str) -> None:
        """Make ``name`` the sole default for the scope."""
        with self._lock:
            scope = self._scope(tenant_id, connector_id)
            if scope is None or name not in scope:
                raise ConfigNotFoundError(
                    f"no config {name!r} for {tenant_id!r}/{connector_id!r}"
                )
            changed: List[str] = []
            for other_name, other in scope.items():
                should_be = other_name == name
                if other.default != should_be:
                    other.default = should_be
                    changed.append(other_name)
            # Moving the default reroutes which concrete key default calls resolve
            # to; invalidate the previous default so cached default routing refreshes.
            if changed:
                self._invalidate(tenant_id, connector_id, changed)

    def delete(
        self,
        tenant_id: str,
        connector_id: str,
        name: str,
        new_default: Optional[str] = None,
    ) -> None:
        """Delete the named config.

        Deleting the current default requires ``new_default`` unless it is the last
        config, in which case the connector is removed for the tenant entirely
        (subsequent invokes fail closed with :class:`ConfigNotFoundError`).
        """
        with self._lock:
            scope = self._scope(tenant_id, connector_id)
            if scope is None or name not in scope:
                raise ConfigNotFoundError(
                    f"no config {name!r} for {tenant_id!r}/{connector_id!r}"
                )
            record = scope[name]
            is_last = len(scope) == 1

            if record.default and not is_last:
                if new_default is None:
                    raise DefaultDeletionError(
                        f"config {name!r} is the default for {tenant_id!r}/{connector_id!r}; "
                        "provide new_default to nominate a replacement"
                    )
                if new_default == name or new_default not in scope:
                    raise ConfigNotFoundError(
                        f"new_default {new_default!r} is not a config for "
                        f"{tenant_id!r}/{connector_id!r}"
                    )

            del scope[name]
            invalidated = [name]
            if not scope:
                # Last config removed: drop the scope so has_config() -> False.
                del self._data[tenant_id][connector_id]
                if not self._data[tenant_id]:
                    del self._data[tenant_id]
            elif record.default:
                scope[new_default].default = True  # type: ignore[index]
                invalidated.append(new_default)  # type: ignore[arg-type]
            self._invalidate(tenant_id, connector_id, invalidated)

    # ---- read path (redacted) ------------------------------------------

    def get(self, tenant_id: str, connector_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Read one config with secret-bearing fields redacted. ``None`` if absent."""
        with self._lock:
            scope = self._scope(tenant_id, connector_id)
            if scope is None or name not in scope:
                return None
            return self._public_view(scope[name])

    def list(
        self, tenant_id: str, connector_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Redacted list. ``connector_id=None`` lists all configs for the tenant."""
        with self._lock:
            tenant_map = self._data.get(tenant_id)
            if tenant_map is None:
                return []
            out: List[Dict[str, Any]] = []
            connector_ids = [connector_id] if connector_id is not None else list(tenant_map)
            for cid in connector_ids:
                scope = tenant_map.get(cid)
                if not scope:
                    continue
                for record in scope.values():
                    out.append(self._public_view(record))
            return out

    def has_config(self, tenant_id: str, connector_id: str) -> bool:
        """True when at least one config exists for the scope (entitlement check)."""
        with self._lock:
            scope = self._scope(tenant_id, connector_id)
            return bool(scope)

    # ---- internal (unredacted) -----------------------------------------

    def resolve(
        self, tenant_id: str, connector_id: str, name: Optional[str]
    ) -> ConfigRecord:
        """INTERNAL (factory use). ``name=None`` resolves the default. Missing scope
        or name raises :class:`ConfigNotFoundError`. Returns the UNREDACTED record."""
        with self._lock:
            scope = self._scope(tenant_id, connector_id)
            if not scope:
                raise ConfigNotFoundError(
                    f"no config for tenant {tenant_id!r} / connector {connector_id!r}"
                )
            if name is None:
                for record in scope.values():
                    if record.default:
                        return record
                # Defensive: a non-empty scope always has a default by construction.
                return next(iter(scope.values()))
            record = scope.get(name)
            if record is None:
                raise ConfigNotFoundError(
                    f"no config for tenant {tenant_id!r} / connector {connector_id!r}"
                )
            return record

    # ---- helpers --------------------------------------------------------

    def _scope(
        self, tenant_id: str, connector_id: str
    ) -> Optional[Dict[str, ConfigRecord]]:
        tenant_map = self._data.get(tenant_id)
        if tenant_map is None:
            return None
        return tenant_map.get(connector_id)

    def _require(self, tenant_id: str, connector_id: str, name: str) -> ConfigRecord:
        scope = self._scope(tenant_id, connector_id)
        if scope is None or name not in scope:
            raise ConfigNotFoundError(
                f"no config {name!r} for {tenant_id!r}/{connector_id!r}"
            )
        return scope[name]

    @staticmethod
    def _public_view(record: ConfigRecord) -> Dict[str, Any]:
        view = redact(record.raw)
        view["name"] = record.name
        view["default"] = record.default
        return view

    def _invalidate(self, tenant_id: str, connector_id: str, names: List[str]) -> None:
        if self._factory is not None:
            self._factory.invalidate_configs(tenant_id, connector_id, names)
