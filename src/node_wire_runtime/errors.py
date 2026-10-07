#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""The error taxonomy: codes, categories, and how a failure reaches a caller.

The runtime owns every code, whether the failure happens inside a connector run
(:class:`ErrorMapper`, from each connector's ``error_map``) or before it (a binding raises
:class:`NodeWireError`). Bindings turn the resulting :class:`ConnectorResponse` into their
transport (an HTTP status, an MCP ``isError`` result) and never invent a code or a format.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Type, Union

from pydantic import ValidationError

from .log_sanitization import scrub_secrets
from .models import ConnectorResponse, ErrorCategory

logger = logging.getLogger("runtime.errors")


class ErrorCode:
    """Runtime-wide error codes. Connectors add their own through ``error_map``."""

    # The call itself
    VALIDATION_ERROR = "VALIDATION_ERROR"
    #: gRPC's name, shipped in 1.0.0, for a payload rejected before the connector ran; the other
    #: surfaces say VALIDATION_ERROR. Kept through 1.x.
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    INVALID_JSON = "INVALID_JSON"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    CONNECTOR_NOT_AVAILABLE = "CONNECTOR_NOT_AVAILABLE"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    # Tenants and named configs
    MISSING_TENANT = "MISSING_TENANT"
    TENANT_NOT_ALLOWED = "TENANT_NOT_ALLOWED"
    TENANT_PIN_LOCKED = "TENANT_PIN_LOCKED"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    TENANT_IDENTITY_MISMATCH = "TENANT_IDENTITY_MISMATCH"
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    CONFIG_NAME_CONFLICT = "CONFIG_NAME_CONFLICT"
    CONFIG_DEFAULT_REQUIRED = "CONFIG_DEFAULT_REQUIRED"
    CONFIG_INVALID = "CONFIG_INVALID"
    # Authorization and credentials
    POLICY_DENIED = "POLICY_DENIED"
    PROXY_AUTH_FAILED = "PROXY_AUTH_FAILED"
    UPSTREAM_TOKEN_MISSING = "UPSTREAM_TOKEN_MISSING"
    SECRET_NOT_FOUND = "SECRET_NOT_FOUND"
    TENANT_SECRET_NOT_FOUND = "TENANT_SECRET_NOT_FOUND"


#: Each runtime-wide code's category. Codes shipped in 1.0.0 keep theirs through 1.x.
CATALOGUE: Dict[str, ErrorCategory] = {
    ErrorCode.VALIDATION_ERROR: ErrorCategory.BUSINESS,
    ErrorCode.INVALID_PAYLOAD: ErrorCategory.BUSINESS,
    ErrorCode.INVALID_JSON: ErrorCategory.BUSINESS,
    ErrorCode.UNKNOWN_TOOL: ErrorCategory.BUSINESS,
    ErrorCode.CONNECTOR_NOT_AVAILABLE: ErrorCategory.BUSINESS,
    ErrorCode.RATE_LIMIT_EXCEEDED: ErrorCategory.RETRYABLE,
    ErrorCode.MISSING_TENANT: ErrorCategory.AUTH,
    ErrorCode.TENANT_NOT_ALLOWED: ErrorCategory.AUTH,
    ErrorCode.TENANT_PIN_LOCKED: ErrorCategory.AUTH,
    ErrorCode.TENANT_MISMATCH: ErrorCategory.AUTH,
    ErrorCode.TENANT_IDENTITY_MISMATCH: ErrorCategory.AUTH,
    # AUTH, not BUSINESS: an unknown config and an unknown scope are indistinguishable on
    # purpose (config names cannot be enumerated), and REST answers both with 403.
    ErrorCode.CONFIG_NOT_FOUND: ErrorCategory.AUTH,
    ErrorCode.CONFIG_NAME_CONFLICT: ErrorCategory.BUSINESS,
    ErrorCode.CONFIG_DEFAULT_REQUIRED: ErrorCategory.BUSINESS,
    ErrorCode.CONFIG_INVALID: ErrorCategory.BUSINESS,
    ErrorCode.POLICY_DENIED: ErrorCategory.AUTH,
    ErrorCode.PROXY_AUTH_FAILED: ErrorCategory.AUTH,
    ErrorCode.UPSTREAM_TOKEN_MISSING: ErrorCategory.AUTH,
    # The server lacks a secret it is configured to read: a misconfiguration (HTTP 500), not a
    # failure of the caller's authentication.
    ErrorCode.SECRET_NOT_FOUND: ErrorCategory.FATAL,
    ErrorCode.TENANT_SECRET_NOT_FOUND: ErrorCategory.FATAL,
}


class NodeWireError(ValueError):
    """A failure outside the connector (bad arguments, no tenant, unknown config, ...), with a
    catalogue code. A ``ValueError``, so callers that catch ``ValueError`` keep working."""

    def __init__(self, code: str, message: str, *, details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.code = code
        self.category = CATALOGUE[code]
        self.message = message
        self.details = details


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
        0. a :class:`NodeWireError` carries its own code
        1. ``connector_id``'s own registry (closest MRO match)
        2. the runtime-wide global registry (closest MRO match)
        3. default ``FATAL`` with the exception's type name
        """
        if isinstance(exc, NodeWireError):
            return MappedError(code=exc.code, category=exc.category)
        scoped = cls._connector_registries.get(connector_id)
        if scoped:
            hit = _closest_match(exc, scoped)
            if hit is not None:
                return hit
        hit = _closest_match(exc, cls._global_registry)
        if hit is not None:
            return hit
        return MappedError(code=type(exc).__name__, category=ErrorCategory.FATAL)


_Problem = Union[Mapping[str, Any], Tuple[Sequence[Any], str]]


def validation_error(problems: Union[ValidationError, Iterable[_Problem]]) -> NodeWireError:
    """Arguments that don't fit: one ``VALIDATION_ERROR`` naming every bad field.

    ``problems`` is a pydantic ``ValidationError``, or ``(location, message)`` pairs (e.g. from
    a JSON Schema check). ``details`` lists them as ``{"loc": [...], "msg": ...}``.
    """
    items: list[tuple[Sequence[Any], Any]]
    if isinstance(problems, ValidationError):
        items = [(e["loc"], e["msg"]) for e in problems.errors()]
    else:
        items = [(p["loc"], p["msg"]) if isinstance(p, Mapping) else (p[0], p[1]) for p in problems]
    details = [{"loc": list(loc), "msg": str(msg)} for loc, msg in items]
    listed = "; ".join(
        f"{'.'.join(str(part) for part in d['loc']) or 'arguments'}: {d['msg']}" for d in details
    )
    return NodeWireError(
        ErrorCode.VALIDATION_ERROR, f"Input validation failed; {listed}", details=details
    )


def reject(
    exc: BaseException,
    *,
    connector_id: str = "",
    action: str = "",
    tenant_id: str = "",
) -> ConnectorResponse:
    """The response for a call that fails before its connector runs, logged under a new trace id.

    One warning line per rejected call: ``audit_event`` ``invocation_validation_failure`` for
    bad arguments, else ``invocation_rejected``. Any exception maps through :class:`ErrorMapper`;
    raise :class:`NodeWireError` to pick the code.
    """
    mapped = ErrorMapper.resolve(exc, connector_id=connector_id)
    message = scrub_secrets(exc.message if isinstance(exc, NodeWireError) else str(exc))
    details = exc.details if isinstance(exc, NodeWireError) else None
    trace_id = str(uuid.uuid4())
    logger.warning(
        "Call rejected before the connector ran",
        extra={
            "trace_id": trace_id,
            "connector_id": connector_id,
            "action": action,
            "tenant_id": tenant_id,
            "error_code": mapped.code,
            "error_category": mapped.category.value,
            "error_type": type(exc).__name__,
            "error_message": message,
            "audit": True,
            "audit_event": (
                "invocation_validation_failure"
                if mapped.code == ErrorCode.VALIDATION_ERROR
                else "invocation_rejected"
            ),
        },
    )
    return ConnectorResponse(
        success=False,
        error_code=mapped.code,
        error_category=mapped.category,
        message=message,
        trace_id=trace_id,
        details=details,
    )


def error_text(response: ConnectorResponse) -> str:
    """A failed call in one line, as MCP clients get it: ``CODE [CATEGORY]: message (trace_id=…)``."""
    category = response.error_category.value if response.error_category else ""
    head = f"{response.error_code} [{category}]" if category else str(response.error_code)
    text = f"{head}: {response.message or 'Call failed'}"
    return f"{text} (trace_id={response.trace_id})" if response.trace_id else text


def http_status(category: Optional[ErrorCategory]) -> int:
    """The HTTP status for a response of ``category`` (``None``: success)."""
    if category is None:
        return 200
    return {
        ErrorCategory.BUSINESS: 400,
        ErrorCategory.AUTH: 401,
        ErrorCategory.RETRYABLE: 503,
    }.get(category, 500)
