#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Type

from .models import ErrorCategory


@dataclass
class MappedError:
    code: str
    category: ErrorCategory


def _closest_match(
    exc: BaseException, registry: Mapping[Type[BaseException], MappedError]
) -> Optional[MappedError]:
    """Return the mapping for the exception type nearest ``exc`` in its own MRO.

    Walking the MRO (most-specific first) and taking the first registered hit
    means a broadly-registered ancestor type (e.g. ``httpx.RequestError``) can
    never shadow a more specific registration (e.g. ``httpx.ConnectError``)
    just because it happens to be inserted into the registry first.
    """
    for klass in type(exc).__mro__:
        mapped = registry.get(klass)
        if mapped is not None:
            return mapped
    # Fallback for virtual subclasses that satisfy isinstance() without
    # appearing in the concrete MRO (e.g. ABC.register()). No specificity
    # ordering is possible here, so this is a last-resort, first-hit scan.
    for exc_type, mapped in registry.items():
        if isinstance(exc, exc_type):
            return mapped
    return None


class ErrorMapper:
    """
    Registry mapping exception classes to a standardized error taxonomy.

    Connector-specific mappings live in a registry **scoped to the connector
    that owns them**, keyed by ``connector_id`` — so one connector's
    ``httpx.HTTPStatusError`` mapping (say) can never leak into another
    connector's response just because both happen to be loaded in the same
    process. Only exceptions raised by the runtime itself, not by connector
    code (``PolicyDenied``, ``TenantMismatchError``), belong in the separate
    global registry via :meth:`register_global`.

    Connectors never call this class directly: ``BaseConnector.__init_subclass__``
    is the sole caller of :meth:`register`, driven by each connector's
    declarative ``error_map`` class attribute. That leaves no call site where a
    connector's exception can be registered without a ``connector_id``.
    """

    _global_registry: Dict[Type[BaseException], MappedError] = {}
    _connector_registries: Dict[str, Dict[Type[BaseException], MappedError]] = {}

    @classmethod
    def register(
        cls,
        connector_id: str,
        exc_type: Type[BaseException],
        category: ErrorCategory,
        code: Optional[str] = None,
    ) -> None:
        """Register an exception type as owned by ``connector_id``."""
        mapped = MappedError(code=code or exc_type.__name__, category=category)
        cls._connector_registries.setdefault(connector_id, {})[exc_type] = mapped

    @classmethod
    def register_global(
        cls,
        exc_type: Type[BaseException],
        category: ErrorCategory,
        code: Optional[str] = None,
    ) -> None:
        """Register a runtime-wide exception type, not owned by any connector."""
        cls._global_registry[exc_type] = MappedError(
            code=code or exc_type.__name__, category=category
        )

    @classmethod
    def resolve(cls, exc: BaseException, *, connector_id: str) -> MappedError:
        """
        Resolve an exception instance to a mapped error.

        Lookup order:
        1. ``connector_id``'s own registry (closest MRO match)
        2. the runtime-wide global registry (closest MRO match)
        3. default ``FATAL`` with the exception's type name
        """
        scoped = cls._connector_registries.get(connector_id)
        if scoped:
            hit = _closest_match(exc, scoped)
            if hit is not None:
                return hit
        hit = _closest_match(exc, cls._global_registry)
        if hit is not None:
            return hit
        return MappedError(code=type(exc).__name__, category=ErrorCategory.FATAL)
