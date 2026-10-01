#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import copy
import logging
import os
from collections.abc import Mapping, MutableMapping
from typing import Optional, cast

from opentelemetry._logs import set_logger_provider
from opentelemetry import metrics, trace
from opentelemetry.attributes import BoundedAttributes
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from node_wire_runtime.log_sanitization import (
    LOG_RECORD_STANDARD_KEYS,
    add_filter_once,
    install_sanitizing_log_filter,
    sanitize_value,
)

logger = logging.getLogger("runtime.observability")

# Mutable holder so init_observability can flip the flag without rebinding a
# module global (tests reset it via _STATE["initialized"] = False).
_STATE: dict[str, bool] = {"initialized": False}


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


def _sanitize_otlp_attributes(attributes: MutableMapping[str, object]) -> None:
    # Not ``dict``: span attributes are OpenTelemetry's BoundedAttributes, and the compiled
    # (Cython) wheel enforces a ``dict`` annotation at run time, failing every span export.
    for key in list(attributes.keys()):
        attributes[key] = sanitize_value(str(key), attributes[key])


def _sanitized_copy(attributes: Mapping[str, object]) -> BoundedAttributes:
    """``attributes`` with sensitive values redacted, frozen like the span's own."""
    return BoundedAttributes(
        attributes={key: sanitize_value(str(key), value) for key, value in attributes.items()},
        immutable=True,
        max_value_len=getattr(attributes, "max_value_len", None),
    )


_OTEL_SCALARS = (str, bool, int, float)


def _otel_value(value: object) -> bool:
    """Whether OpenTelemetry can carry ``value`` as a log attribute."""
    if isinstance(value, _OTEL_SCALARS):
        return True
    return isinstance(value, (list, tuple)) and all(isinstance(v, _OTEL_SCALARS) for v in value)


class OtlpLoggingHandler(LoggingHandler):
    """OpenTelemetry's LoggingHandler, exporting only attributes OpenTelemetry can carry.

    Other libraries put objects on log records: structlog's ``_logger`` (the logger itself) on
    every line of a stacklok-built server. The SDK would drop each one with an "Invalid type ...
    for attribute value" warning per line. The export gets a copy without them; the record keeps
    them for the other handlers (structlog's console formatter reads ``_logger``).
    """

    def emit(self, record: logging.LogRecord) -> None:
        extra = [
            key
            for key, value in vars(record).items()
            if key not in LOG_RECORD_STANDARD_KEYS and not _otel_value(value)
        ]
        if extra:
            record = copy.copy(record)
            for key in extra:
                delattr(record, key)
        super().emit(record)


class SanitizingSpanExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter):
        self._delegate = delegate

    def export(self, spans):
        for span in spans:
            attributes = getattr(span, "_attributes", None)
            if attributes:
                # A finished span's attributes are frozen (BoundedAttributes, immutable in newer
                # SDKs): swap in a sanitized copy instead of writing into them.
                span._attributes = _sanitized_copy(attributes)
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
                _sanitize_otlp_attributes(record.attributes)
        return self._delegate.export(batch)

    def shutdown(self):
        return self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        if hasattr(self._delegate, "force_flush"):
            return self._delegate.force_flush(timeout_millis)
        return True


class SanitizingMetricExporter(MetricExporter):
    def __init__(self, delegate: MetricExporter):
        self._delegate = delegate

    @property
    def _preferred_temporality(self):  # type: ignore[override]
        return self._delegate._preferred_temporality

    @property
    def _preferred_aggregation(self):  # type: ignore[override]
        return self._delegate._preferred_aggregation

    def export(self, metrics_data, timeout_millis=10_000, **kwargs):
        for rm in metrics_data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    if hasattr(metric.data, "data_points"):
                        for dp in metric.data.data_points:
                            attrs = getattr(dp, "attributes", None)
                            if attrs and isinstance(attrs, dict):
                                _sanitize_otlp_attributes(attrs)
        return self._delegate.export(metrics_data, timeout_millis=timeout_millis, **kwargs)

    def shutdown(self, timeout_millis=30_000, **kwargs):
        return self._delegate.shutdown(timeout_millis=timeout_millis, **kwargs)

    def force_flush(self, timeout_millis: float = 10_000):
        return self._delegate.force_flush(timeout_millis)


def init_observability(app_name: str = "node_wire") -> None:
    """
    Initialize OpenTelemetry + OpenLLMetry/Traceloop for the process.

    This is intended to be called once at process startup (e.g. from the
    bindings_entrypoint main()) and is safe to call multiple times.
    """
    if _STATE["initialized"]:
        return

    install_sanitizing_log_filter()

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
    root_logger.addHandler(
        OtlpLoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    )
    # On the handlers as well: propagated records skip root-logger filters.
    for target in (root_logger, *root_logger.handlers):
        add_filter_once(target, _OtelContextFilter())
    install_sanitizing_log_filter()

    # Metrics: export to the local OTLP collector alongside traces and logs.
    metric_interval_str: str = os.getenv("AOT_METRIC_EXPORT_INTERVAL_MS", "60000")
    try:
        metric_interval_ms = int(metric_interval_str)
    except ValueError:
        logger.warning(
            "Invalid AOT_METRIC_EXPORT_INTERVAL_MS %r, falling back to 60000", metric_interval_str
        )
        metric_interval_ms = 60000

    metric_exporter = SanitizingMetricExporter(
        OTLPMetricExporter(
            headers=dict(header.split("=", 1) for header in otlp_headers.split(","))
            if otlp_headers
            else None,
        )
    )
    metric_reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=metric_interval_ms,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # Initialize Traceloop/OpenLLMetry in metadata-only mode. Advanced AI features
    # (prompt logging, workflows, tools) are intentionally deferred.
    # Skip silently when no API key is configured — Traceloop is optional.
    if os.environ.get("TRACELOOP_API_KEY"):
        try:
            from traceloop.sdk import Traceloop

            Traceloop.init(
                app_name=app_name,
            )
        except Exception as exc:  # pragma: no cover - defensive; should not fail app startup
            logger.warning("Failed to initialize Traceloop/OpenLLMetry: %s", exc)

    _STATE["initialized"] = True
    logger.info("Observability initialized for app %s", app_name)
