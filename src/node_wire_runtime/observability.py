#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
import os
from typing import Optional, cast

from opentelemetry._logs import set_logger_provider
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

logger = logging.getLogger("runtime.observability")

_INITIALIZED: bool = False


class _OtelContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        span = trace.get_current_span()
        ctx = span.get_span_context() if span is not None else None
        if ctx is not None and ctx.is_valid:
            record.otel_trace_id = format(ctx.trace_id, "032x")
            record.otel_span_id = format(ctx.span_id, "016x")
        else:
            record.otel_trace_id = ""
            record.otel_span_id = ""
        return True


_SENSITIVE_KEYS = {
    "patient",
    "ssn",
    "secret",
    "password",
    "email",
    "phone",
    "dob",
    "encounter",
    "resourceid",
}


def _is_sensitive(key: str) -> bool:
    k = key.lower().replace("_", "").replace("-", "").replace(" ", "")
    for s in _SENSITIVE_KEYS:
        if s in k:
            return True
    return False


class SanitizingSpanExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter):
        self._delegate = delegate

    def export(self, spans):
        for span in spans:
            if hasattr(span, "_attributes") and span._attributes:
                for k in list(span._attributes.keys()):
                    if _is_sensitive(k):
                        span._attributes[k] = "***REDACTED***"
        return self._delegate.export(spans)

    def shutdown(self):
        return self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        if hasattr(self._delegate, "force_flush"):
            return self._delegate.force_flush(timeout_millis)
        return True


class SanitizingLogExporter(LogExporter):
    def __init__(self, delegate: LogExporter):
        self._delegate = delegate

    def export(self, batch):
        for record in batch:
            if hasattr(record, "attributes") and record.attributes:
                for k in list(record.attributes.keys()):
                    if _is_sensitive(k):
                        record.attributes[k] = "***REDACTED***"
        return self._delegate.export(batch)

    def shutdown(self):
        return self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        if hasattr(self._delegate, "force_flush"):
            return self._delegate.force_flush(timeout_millis)
        return True


def init_observability(app_name: str = "node_wire") -> None:
    """
    Initialize OpenTelemetry + OpenLLMetry/Traceloop for the process.

    This is intended to be called once at process startup (e.g. from the
    bindings_entrypoint main()) and is safe to call multiple times.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    # Sampling ratio can be tuned per environment. Default to full sampling in dev-like setups.
    sampling_ratio_str: str = os.getenv("AOT_TRACING_SAMPLING_RATIO", "1.0")
    try:
        sampling_ratio = float(sampling_ratio_str)
    except ValueError:
        logger.warning(
            "Invalid AOT_TRACING_SAMPLING_RATIO %r, falling back to 1.0", sampling_ratio_str
        )
        sampling_ratio = 1.0

    resource = Resource.create(
        {
            "service.name": app_name,
        }
    )

    tracer_provider = TracerProvider(
        sampler=ParentBased(TraceIdRatioBased(sampling_ratio)),
        resource=resource,
    )

    otlp_headers: Optional[str] = os.getenv("OTEL_EXPORTER_OTLP_HEADERS")

    span_exporter = SanitizingSpanExporter(
        OTLPSpanExporter(
            headers=dict(header.split("=", 1) for header in otlp_headers.split(","))
            if otlp_headers
            else None,
        )
    )

    span_processor = BatchSpanProcessor(span_exporter)
    tracer_provider.add_span_processor(span_processor)
    trace.set_tracer_provider(tracer_provider)

    # Logs: export Python logging records via OTLP/HTTP to the local collector.
    # This enables Loki ingestion when using grafana/otel-lgtm.
    log_exporter = SanitizingLogExporter(
        cast(
            LogExporter,
            OTLPLogExporter(
                headers=dict(header.split("=", 1) for header in otlp_headers.split(","))
                if otlp_headers
                else None,
            ),
        )
    )
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(logger_provider)

    root_logger = logging.getLogger()
    root_logger.addFilter(_OtelContextFilter())
    root_logger.addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider))

    # Initialize Traceloop/OpenLLMetry in metadata-only mode. Advanced AI features
    # (prompt logging, workflows, tools) are intentionally deferred.
    try:
        from traceloop.sdk import Traceloop

        Traceloop.init(
            app_name=app_name,
        )
    except Exception as exc:  # pragma: no cover - defensive; should not fail app startup
        logger.warning("Failed to initialize Traceloop/OpenLLMetry: %s", exc)

    _INITIALIZED = True
    logger.info("Observability initialized for app %s", app_name)
