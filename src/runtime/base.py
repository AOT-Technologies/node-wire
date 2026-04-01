from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Generic, Optional, Type, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Tracer
from pybreaker import CircuitBreaker
from pydantic import BaseModel, ValidationError

from .errors import ErrorMapper
from .models import ConnectorResponse, ErrorCategory
from .policy import PolicyContext, PolicyHook, PolicyDenied
from .resilience import with_resilience
from .secrets import SecretProvider

logger = logging.getLogger("runtime.base")
tracer: Tracer = trace.get_tracer("runtime")

InputModelT = TypeVar("InputModelT", bound=BaseModel)
OutputModelT = TypeVar("OutputModelT", bound=BaseModel)


class BaseConnector(ABC, Generic[InputModelT, OutputModelT]):
    """
    Base class for all connectors.

    This is the single execution entrypoint used by all bindings.
    """

    connector_id: str
    action: str

    def __init__(
        self,
        input_model: Type[InputModelT],
        output_model: Type[OutputModelT],
        secret_provider: Optional[SecretProvider] = None,
        policy_hook: Optional[PolicyHook] = None,
        breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        self._input_model_cls = input_model
        self._output_model_cls = output_model
        self._secret_provider = secret_provider
        self._policy_hook = policy_hook
        self._breaker = breaker or CircuitBreaker(
            fail_max=5,
            reset_timeout=30,
            name=f"{self.__class__.__name__}_breaker",
        )

    @property
    def secret_provider(self) -> SecretProvider:
        if self._secret_provider is None:
            raise RuntimeError("SecretProvider has not been configured for this connector.")
        return self._secret_provider

    async def run(
        self,
        raw_input: Dict[str, Any],
        principal: Optional[str] = None,
        tenant_id: Optional[str] = None,
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
                    # Expose validation details so clients know which fields failed.
                    details = [
                        {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                        for e in exc.errors()
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
                    context = PolicyContext(
                        connector_id=self.connector_id,
                        action=self.action,
                        input_payload=input_model.model_dump(),
                        principal=principal,
                        tenant_id=tenant_id,
                    )
                    try:
                        self._policy_hook.check(context)
                    except PolicyDenied as exc:
                        logger.warning(
                            "Execution blocked by policy hook",
                            extra={
                                "trace_id": trace_id,
                                "connector_id": self.connector_id,
                                "action": self.action,
                                "error_type": type(exc).__name__,
                                "error_message": str(exc),
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

                execute_with_resilience = with_resilience(self._breaker)

                @execute_with_resilience
                async def _do_execute(*, trace_id: str) -> OutputModelT:
                    return await self.internal_execute(input_model, trace_id=trace_id)

                output_model = await _do_execute(trace_id=trace_id)

                logger.info(
                    "Connector execution completed successfully - runtime.base",
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

    @abstractmethod
    async def internal_execute(self, params: InputModelT, *, trace_id: str) -> OutputModelT:
        """
        Implement connector-specific logic here.

        All external calls must be wrapped in try/except blocks with clear,
        human-readable logging messages. Any raised exceptions will be
        standardized by the ErrorMapper.
        """
        raise NotImplementedError
