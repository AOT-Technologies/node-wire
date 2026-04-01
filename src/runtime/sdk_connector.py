from __future__ import annotations

import inspect
import logging
import uuid
from dataclasses import dataclass
from typing import (
    Annotated,
    Any,
    ClassVar,
    Dict,
    Optional,
    Tuple,
    Type,
    Union,
    get_type_hints,
)

from pydantic import BaseModel, Field, RootModel

from .base import BaseConnector
from .errors import ErrorMapper
from .models import ErrorCategory
from .secrets import SecretProvider
from .sdk_action_spec import SdkActionSpec

logger = logging.getLogger("runtime.sdk_connector")

# Populated by SDKConnector.__init_subclass__
_CONNECTOR_REGISTRY: Dict[str, Type["SDKConnector"]] = {}


def _make_spec_handler(
    action_name: str,
    input_model: Any,
    output_model: Any,
    cls_qualname: str,
    cls_module: str,
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
    return _handler


def _generate_methods_from_action_specs(cls: type) -> None:
    """
    For each entry in cls.action_specs, generate an async @sdk_action method and
    attach it to cls. Called at the top of SDKConnector.__init_subclass__ so the
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
            action_name, input_model, output_model, cls.__qualname__, cls.__module__
        )
        setattr(cls, fn_name, handler)


def sdk_action(name: str):
    """
    Mark a connector method as a named, auto-discoverable SDK action.

    The decorated method must be async and have full type annotations for its
    params (first arg after self) and return type.
    """

    def decorator(fn: Any) -> Any:
        fn._sdk_action_name = name
        return fn

    return decorator


@dataclass
class SdkActionMeta:
    """Metadata for one @sdk_action method."""

    name: str
    fn_name: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]


class SDKConnector(BaseConnector):
    """
    Base class for SDK-backed connectors.

    Subclasses define:
      - connector_id: str
      - output_model: Type[BaseModel] (common output envelope for all actions)
      - error_map: optional mapping of exception -> (ErrorCategory, code)
      - build_client() / get_client() for vendor SDK lifecycle

    Actions are declared with @sdk_action("resource.operation") on async methods.
    """

    connector_id: str
    action: str = "execute"

    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {}
    output_model: ClassVar[Type[BaseModel]]

    _action_registry: ClassVar[Dict[str, SdkActionMeta]]
    _union_input_model: ClassVar[Type[RootModel[Any]]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        # Phase 0: auto-generate @sdk_action methods from action_specs (opt-in).
        # Must run before the dir(cls) discovery loop below.
        _generate_methods_from_action_specs(cls)

        registry: Dict[str, SdkActionMeta] = {}
        for attr_name in dir(cls):
            method = getattr(cls, attr_name, None)
            if not callable(method) or not hasattr(method, "_sdk_action_name"):
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
                    f"{cls.__name__}.{attr_name}: @sdk_action method must have a params argument "
                    "after self"
                )

            input_model = hints.get(input_param_name)
            output_model = hints.get("return")
            if input_model is None or not isinstance(input_model, type) or not issubclass(
                input_model, BaseModel
            ):
                raise TypeError(
                    f"{cls.__name__}.{attr_name}: missing or invalid type hint for "
                    f"parameter {input_param_name!r}"
                )
            if output_model is None or not isinstance(output_model, type) or not issubclass(
                output_model, BaseModel
            ):
                raise TypeError(
                    f"{cls.__name__}.{attr_name}: missing or invalid return type hint"
                )

            action_name = method._sdk_action_name
            registry[action_name] = SdkActionMeta(
                name=action_name,
                fn_name=attr_name,
                input_model=input_model,
                output_model=output_model,
            )

        cls._action_registry = registry

        valid_models = [m.input_model for m in registry.values()]
        if not valid_models:
            raise TypeError(f"{cls.__name__}: SDKConnector must define at least one @sdk_action")

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
                "Registered SDKConnector subclass",
                extra={"connector_id": cls.connector_id},
            )

    def __init__(self, *, secret_provider: Optional[SecretProvider] = None) -> None:
        cls = type(self)
        super().__init__(
            cls._union_input_model,
            cls.output_model,
            secret_provider=secret_provider,
        )
        self._client: Any = None

    @classmethod
    def sdk_action_metas(cls) -> Dict[str, SdkActionMeta]:
        """Registry of action name -> metadata (for manifest)."""
        return dict(cls._action_registry)

    def build_client(self) -> Any:
        """Override in subclasses to build the vendor SDK client."""
        return None

    def get_client(self) -> Any:
        if self._client is None:
            self._client = self.build_client()
        return self._client

    async def internal_execute(self, params: Any, *, trace_id: str) -> Any:
        """Dispatch to the @sdk_action method matching the validated input."""
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
            "Dispatching sdk_action",
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
