#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import inspect
import logging
import os
import uuid
from abc import ABC
from dataclasses import dataclass
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    Dict,
    Optional,
    Tuple,
    Type,
    Union,
    get_type_hints,
    List,
)

from opentelemetry import trace
from opentelemetry.trace import Tracer
from pybreaker import CircuitBreaker
from pydantic import BaseModel, Field, RootModel, ValidationError

from .auth import AuthProvider, NoAuthProvider
from .errors import ErrorMapper
from .models import ConnectorResponse, ErrorCategory
from .policy import PolicyContext, PolicyHook, PolicyDenied
from .resilience import with_resilience
from .secrets import SecretProvider
from .sdk_action_spec import SdkActionSpec

logger = logging.getLogger("runtime.base_connector")
tracer: Tracer = trace.get_tracer("runtime")
ErrorMapper.register(PolicyDenied, ErrorCategory.AUTH, code="POLICY_DENIED")

# Populated by BaseConnector.__init_subclass__
_CONNECTOR_REGISTRY: Dict[str, Type["BaseConnector"]] = {}


def _make_spec_handler(
    action_name: str,
    input_model: Any,
    output_model: Any,
    cls_qualname: str,
    cls_module: str,
    alias_tolerant: bool = False,
    mcp_normalize: Optional[Callable[[Dict[str, Any]], None]] = None,
    requires_auth: bool = True,
    scopes: Optional[List[str]] = None,
    rate_limit: Optional[Dict[str, Any]] = None,
    deprecated: bool = False,
) -> Any:
    """
    Build a single async handler function for one action_specs entry.
    Using a factory function (rather than a loop + default-arg trick) ensures
    action_name is captured by value in the closure and does not appear in the
    method signature seen by inspect.signature / get_type_hints.
    """
    fn_name = action_name.replace(".", "_").replace("-", "_")

    async def _handler(self, params, *, trace_id: str):
        return await self._execute_action_spec(action_name, params, trace_id=trace_id)

    _handler.__name__ = fn_name
    _handler.__qualname__ = f"{cls_qualname}.{fn_name}"
    _handler.__module__ = cls_module
    # Set actual type objects (not strings) so get_type_hints() resolves correctly
    # even when `from __future__ import annotations` is active in the connector module.
    _handler.__annotations__ = {"params": input_model, "return": output_model}
    _handler._sdk_action_name = action_name
    _handler._alias_tolerant = alias_tolerant
    _handler._mcp_normalize = mcp_normalize
    _handler._requires_auth = requires_auth
    _handler._scopes = scopes
    _handler._rate_limit = rate_limit
    _handler._deprecated = deprecated
    # Backward-compatible alias for legacy callers/tests.
    _handler._nw_action_name = action_name
    return _handler


def _generate_methods_from_action_specs(cls: Any) -> None:
    """
    For each entry in cls.action_specs, generate an async @nw_action method and
    attach it to cls. Called at the top of BaseConnector.__init_subclass__ so the
    existing discovery loop picks up the generated methods.

    Opt-in: only triggers when the class defines action_specs in its own __dict__.
    """
    specs = cls.__dict__.get("action_specs")
    if specs is None:
        return

    fallback_output = getattr(cls, "output_model", None)

    for action_name, spec in specs.items():
        if not isinstance(spec, SdkActionSpec):
            raise TypeError(
                f"{cls.__name__}: action_specs[{action_name!r}] must be a SdkActionSpec instance"
            )
        input_model = spec.input_model
        if not (isinstance(input_model, type) and issubclass(input_model, BaseModel)):
            raise TypeError(
                f"{cls.__name__}: action_specs[{action_name!r}] requires "
                "input_model=<BaseModel subclass>"
            )

        output_model = spec.output_model if spec.output_model is not None else fallback_output
        if not (isinstance(output_model, type) and issubclass(output_model, BaseModel)):
            raise TypeError(
                f"{cls.__name__}: action_specs[{action_name!r}] has no resolvable "
                "output_model — set it on the SdkActionSpec or define cls.output_model"
            )

        fn_name = action_name.replace(".", "_").replace("-", "_")
        if fn_name in cls.__dict__:
            raise TypeError(
                f"{cls.__name__}: action_specs[{action_name!r}] conflicts with "
                f"existing method {fn_name!r}"
            )

        handler = _make_spec_handler(
            action_name,
            input_model,
            output_model,
            cls.__qualname__,
            cls.__module__,
            alias_tolerant=spec.alias_tolerant,
            mcp_normalize=spec.mcp_normalize,
            requires_auth=spec.requires_auth,
            scopes=spec.scopes,
            rate_limit=spec.rate_limit,
            deprecated=spec.deprecated,
        )
        setattr(cls, fn_name, handler)


def sdk_action(
    name: str,
    *,
    alias_tolerant: bool = False,
    mcp_normalize: Optional[Callable[[Dict[str, Any]], None]] = None,
    requires_auth: bool = True,
    scopes: Optional[List[str]] = None,
    rate_limit: Optional[Dict[str, Any]] = None,
    deprecated: bool = False,
):
    """
    Mark a connector method as a named, auto-discoverable action.

    The decorated method must be async and have full type annotations for its
    params (first arg after self) and return type.

    Set alias_tolerant=True for actions whose MCP input schema should accept
    extra/alias fields (e.g. LLM-generated aliases) before normalization runs.

    Optional mcp_normalize mutates tool argument dicts in place before connector.run.
    """

    def decorator(fn: Any) -> Any:
        fn._sdk_action_name = name
        fn._alias_tolerant = alias_tolerant
        fn._mcp_normalize = mcp_normalize
        fn._requires_auth = requires_auth
        fn._scopes = scopes
        fn._rate_limit = rate_limit
        fn._deprecated = deprecated
        # Backward-compatible alias for legacy callers/tests.
        fn._nw_action_name = name
        return fn

    return decorator


def nw_action(name: str):
    """Backward-compatible decorator alias for sdk_action()."""
    return sdk_action(name)


@dataclass
class NwActionMeta:
    """Metadata for one @nw_action method."""

    name: str
    fn_name: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    alias_tolerant: bool = False
    mcp_normalize: Optional[Callable[[Dict[str, Any]], None]] = None
    requires_auth: bool = True
    scopes: Optional[List[str]] = None
    rate_limit: Optional[Dict[str, Any]] = None
    deprecated: bool = False


class BaseConnector(ABC):
    """
    Base class for all connectors.

    Subclasses define:
      - connector_id: str
      - output_model: Type[BaseModel] (common output envelope for all actions)
      - error_map: optional mapping of exception -> (ErrorCategory, code)
      - build_client() / get_client() for vendor SDK lifecycle

    Actions are declared with @nw_action("resource.operation") on async methods.
    """

    connector_id: str
    action: str = "execute"

    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {}
    output_model: ClassVar[Type[BaseModel]]

    _action_registry: ClassVar[Dict[str, NwActionMeta]]
    _union_input_model: ClassVar[Type[RootModel[Any]]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        # Phase 0: auto-generate @nw_action methods from action_specs (opt-in).
        # Must run before the dir(cls) discovery loop below.
        _generate_methods_from_action_specs(cls)

        registry: Dict[str, NwActionMeta] = {}
        for attr_name in dir(cls):
            method = getattr(cls, attr_name, None)
            if not callable(method):
                continue
            action_name = getattr(method, "_sdk_action_name", None) or getattr(
                method, "_nw_action_name", None
            )
            if not action_name:
                continue

            try:
                hints = get_type_hints(method)
            except Exception:
                hints = {}

            try:
                sig_params = [
                    p
                    for p in inspect.signature(method).parameters.values()
                    if p.name not in ("self", "trace_id")
                ]
                input_param_name = sig_params[0].name if sig_params else None
            except (ValueError, TypeError):
                input_param_name = None

            if not input_param_name:
                raise TypeError(
                    f"{cls.__name__}.{attr_name}: @nw_action method must have a params argument "
                    "after self"
                )

            input_model = hints.get(input_param_name)
            output_model = hints.get("return")
            if (
                input_model is None
                or not isinstance(input_model, type)
                or not issubclass(input_model, BaseModel)
            ):
                raise TypeError(
                    f"{cls.__name__}.{attr_name}: missing or invalid type hint for "
                    f"parameter {input_param_name!r}"
                )
            if (
                output_model is None
                or not isinstance(output_model, type)
                or not issubclass(output_model, BaseModel)
            ):
                raise TypeError(f"{cls.__name__}.{attr_name}: missing or invalid return type hint")

            registry[action_name] = NwActionMeta(
                name=action_name,
                fn_name=attr_name,
                input_model=input_model,
                output_model=output_model,
                alias_tolerant=getattr(method, '_alias_tolerant', False),
                mcp_normalize=getattr(method, '_mcp_normalize', None),
                requires_auth=getattr(method, '_requires_auth', True),
                scopes=getattr(method, '_scopes', None),
                rate_limit=getattr(method, '_rate_limit', None),
                deprecated=getattr(method, '_deprecated', False),
            )

        cls._action_registry = registry

        valid_models = [m.input_model for m in registry.values()]
        if not valid_models:
            raise TypeError(f"{cls.__name__}: BaseConnector must define at least one @nw_action")

        if len(valid_models) == 1:
            root_type = valid_models[0]
        else:
            root_type = Annotated[
                Union[tuple(valid_models)],  # type: ignore[arg-type]
                Field(discriminator="action"),
            ]

        cls._union_input_model = RootModel[root_type]  # type: ignore[valid-type]
        cls._union_input_model.model_rebuild()

        own_error_map = cls.__dict__.get("error_map", {})
        for exc_type, (category, code) in own_error_map.items():
            ErrorMapper.register(exc_type, category, code=code)

        if "connector_id" in cls.__dict__:
            _CONNECTOR_REGISTRY[cls.connector_id] = cls
            logger.debug(
                "Registered BaseConnector subclass",
                extra={"connector_id": cls.connector_id},
            )

    def __init__(
        self,
        *,
        secret_provider: Optional[SecretProvider] = None,
        policy_hook: Optional[PolicyHook] = None,
        auth_provider: Optional[AuthProvider] = None,
    ) -> None:
        cls = type(self)
        self._input_model_cls = cls._union_input_model
        self._output_model_cls = cls.output_model
        self._secret_provider = secret_provider
        self._policy_hook = policy_hook
        # Default to NoAuthProvider (null-object) so connectors never receive None.
        self._auth_provider: AuthProvider = (
            auth_provider if auth_provider is not None else NoAuthProvider()
        )
        self._breaker = CircuitBreaker(
            fail_max=5,
            reset_timeout=30,
            name=f"{cls.__name__}_breaker",
        )
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._client: Any = None

    @property
    def secret_provider(self) -> SecretProvider:
        if self._secret_provider is None:
            raise RuntimeError("SecretProvider has not been configured for this connector.")
        return self._secret_provider

    @property
    def auth_provider(self) -> AuthProvider:
        """The :class:`AuthProvider` configured for this connector.

        Always returns a valid provider — defaults to :class:`NoAuthProvider`
        when none was injected, so callers never need a ``None`` guard.
        """
        return self._auth_provider

    async def get_auth_headers(self) -> Dict[str, str]:
        """Return authentication headers from the configured :class:`AuthProvider`.

        Connectors should call this instead of reading secrets directly::

            headers = await self.get_auth_headers()
            # merge with any connector-specific headers
            headers.update({"Content-Type": "application/json"})

        Returns an empty dict when the provider is :class:`NoAuthProvider`.
        """
        return await self._auth_provider.get_headers()

    async def run(
        self,
        raw_input: Dict[str, Any],
        principal: Optional[str] = None,
        tenant_id: Optional[str] = None,
        scopes: Optional[tuple[str, ...]] = None,
    ) -> ConnectorResponse:
        """
        Public execution entrypoint.

        - Generates a trace ID
        - Starts an OpenTelemetry span
        - Validates input
        - Executes policy hook
        - Wraps internal execution with retries and circuit breaking
        - Maps exceptions into the standard error taxonomy
        """
        trace_id = str(uuid.uuid4())

        with tracer.start_as_current_span(
            "connector.run",
            attributes={
                "connector.id": self.connector_id,
                "connector.action": self.action,
                "tenant.id": tenant_id or "",
                "principal.id": principal or "",
                "trace.id": trace_id,
            },
        ):
            logger.info(
                "Starting connector execution",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": self.action,
                },
            )

            try:
                try:
                    input_model = self._input_model_cls.model_validate(raw_input)
                except ValidationError as exc:
                    logger.error(
                        "Input validation failed",
                        extra={
                            "trace_id": trace_id,
                            "connector_id": self.connector_id,
                            "action": self.action,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        },
                    )
                    details = [
                        {"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()
                    ]
                    return ConnectorResponse(
                        success=False,
                        error_code="VALIDATION_ERROR",
                        error_category=ErrorCategory.BUSINESS,
                        message="Input validation failed; please check the request payload.",
                        trace_id=trace_id,
                        details=details,
                    )

                # Policy hook
                if self._policy_hook is not None:
                    input_payload = input_model.model_dump()
                    policy_action = str(input_payload.get("action", self.action))
                    context = PolicyContext(
                        connector_id=self.connector_id,
                        action=policy_action,
                        input_payload=input_payload,
                        principal=principal,
                        tenant_id=tenant_id,
                        scopes=scopes,
                    )
                    try:
                        self._policy_hook.check(context)
                    except PolicyDenied as exc:
                        logger.warning(
                            "AUDIT: Execution blocked by policy hook",
                            extra={
                                "trace_id": trace_id,
                                "connector_id": self.connector_id,
                                "action": self.action,
                                "error_type": type(exc).__name__,
                                "error_message": str(exc),
                                "audit": True,
                                "audit_event": "policy_denial",
                                "tenant_id": tenant_id,
                                "principal": principal,
                            },
                        )
                        mapped = ErrorMapper.resolve(exc)
                        return ConnectorResponse(
                            success=False,
                            error_code=mapped.code,
                            error_category=mapped.category,
                            message=str(exc),
                            trace_id=trace_id,
                        )

                tenant_key = tenant_id or "default"
                breaker_cache = getattr(self, "_breakers", None)
                if breaker_cache is None:
                    breaker_cache = {}
                    self._breakers = breaker_cache

                if tenant_key not in breaker_cache:
                    fail_max = int(os.environ.get("AOT_CIRCUIT_BREAKER_FAIL_MAX", "5"))
                    reset_timeout = int(os.environ.get("AOT_CIRCUIT_BREAKER_RESET_TIMEOUT", "30"))
                    breaker_cache[tenant_key] = CircuitBreaker(
                        fail_max=fail_max,
                        reset_timeout=reset_timeout,
                        name=f"{self.connector_id}_breaker_{tenant_key}",
                    )
                
                breaker = breaker_cache[tenant_key]
                execute_with_resilience = with_resilience(breaker)

                @execute_with_resilience
                async def _do_execute(*, trace_id: str) -> Any:
                    return await self.internal_execute(input_model, trace_id=trace_id)

                output_model = await _do_execute(trace_id=trace_id)

                logger.info(
                    "Connector execution completed successfully",
                    extra={
                        "trace_id": trace_id,
                        "connector_id": self.connector_id,
                        "action": self.action,
                    },
                )

                return ConnectorResponse(
                    success=True,
                    data=output_model.model_dump(),
                    trace_id=trace_id,
                )
            except Exception as exc:  # noqa: BLE001
                mapped = ErrorMapper.resolve(exc)
                logger.error(
                    "Connector execution failed",
                    extra={
                        "trace_id": trace_id,
                        "connector_id": self.connector_id,
                        "action": self.action,
                        "error_code": mapped.code,
                        "error_category": mapped.category.value,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
                return ConnectorResponse(
                    success=False,
                    error_code=mapped.code,
                    error_category=mapped.category,
                    message=str(exc),
                    trace_id=trace_id,
                )

    @classmethod
    def get_registry(cls) -> Dict[str, Type[BaseConnector]]:
        """Public access to the global connector registry."""
        return dict(_CONNECTOR_REGISTRY)

    @classmethod
    def sdk_action_metas(cls) -> Dict[str, NwActionMeta]:
        """Registry of action name -> metadata (for manifest/ingress)."""
        return dict(cls._action_registry)

    @classmethod
    def nw_action_metas(cls) -> Dict[str, NwActionMeta]:
        """Backward-compatible alias for sdk_action_metas()."""
        return dict(cls._action_registry)

    def build_client(self) -> Any:
        """Override in subclasses to build the vendor SDK client."""
        return None

    def get_client(self) -> Any:
        if self._client is None:
            self._client = self.build_client()
        return self._client

    async def internal_execute(self, params: Any, *, trace_id: str) -> Any:
        """Dispatch to the @nw_action method matching the validated input."""
        root = params.root if hasattr(params, "root") else params
        action_key = getattr(root, "action", None)
        if action_key is None:
            raise ValueError(f"Input model missing action discriminator: {type(root).__name__}")

        meta = self._action_registry.get(str(action_key))
        if meta is None:
            raise ValueError(
                f"Connector {self.connector_id!r} has no registered action {action_key!r}. "
                f"Available: {list(self._action_registry)}"
            )
        fn = getattr(self, meta.fn_name)
        logger.debug(
            "Dispatching action",
            extra={
                "connector_id": self.connector_id,
                "action": action_key,
                "trace_id": trace_id,
            },
        )
        return await fn(root, trace_id=trace_id)

    async def call_action(self, name: str, params_dict: Dict[str, Any]) -> Any:
        """Invoke another action by name (for composite operations)."""
        meta = self._action_registry.get(name)
        if meta is None:
            raise ValueError(
                f"call_action: unknown action {name!r} on connector {self.connector_id!r}"
            )
        validated = meta.input_model.model_validate(params_dict)
        fn = getattr(self, meta.fn_name)
        return await fn(validated, trace_id=str(uuid.uuid4()))
