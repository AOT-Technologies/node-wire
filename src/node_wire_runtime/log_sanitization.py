#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Value-aware log redaction for PHI and secrets across all handlers and OTLP export."""

from __future__ import annotations

import contextvars
import logging
from typing import Any, Optional

import httpx

REDACTED = "***REDACTED***"

_SENSITIVE_SUBSTRINGS = {
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

_ALWAYS_REDACT_KEYS = frozenset(
    {
        "search_params",
        "query_params",
        "body",
        "params",
        "payload",
        "raw_body",
        "given_name",
        "family_name",
        "birthdate",
        "name",
    }
)

_LOG_RECORD_STANDARD_KEYS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
    }
)


def _normalize_key(key: str) -> str:
    return key.lower().replace("_", "").replace("-", "").replace(" ", "")


def is_sensitive_key(key: str) -> bool:
    """Return True when an attribute/key should be fully redacted."""
    k = _normalize_key(key)
    if key.lower() in _ALWAYS_REDACT_KEYS:
        return True
    return any(s in k for s in _SENSITIVE_SUBSTRINGS)


def sanitize_value(key: str, value: Any) -> Any:
    """Recursively redact sensitive values."""
    if is_sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: sanitize_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_value(key, item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_value(key, item) for item in value)
    if isinstance(value, str) and key.lower() in {"body", "raw_body", "payload"}:
        return REDACTED
    return value


def sanitize_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    return {k: sanitize_value(str(k), v) for k, v in mapping.items()}


def _redact_sensitive_string_arg(value: str) -> str:
    if len(value) > 100:
        return REDACTED
    lowered = value.lower()
    if "phi_marker" in lowered:
        return REDACTED
    return value


def sanitize_log_record(record: logging.LogRecord) -> None:
    """Sanitize message args and dynamic attributes on a log record in place."""
    if record.args:
        if isinstance(record.args, dict):
            record.args = sanitize_mapping(record.args)  # type: ignore[assignment]
        elif isinstance(record.args, tuple):
            record.args = tuple(
                _redact_sensitive_string_arg(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )

    for key in list(record.__dict__.keys()):
        if key in _LOG_RECORD_STANDARD_KEYS:
            continue
        record.__dict__[key] = sanitize_value(key, record.__dict__[key])


class SanitizingLogFilter(logging.Filter):
    """Apply value-aware redaction to every log record before handlers/export."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        sanitize_log_record(record)
        return True


# Stamped onto log records during BaseConnector.run() so nested logs (FHIR inner
# lines, auth, Slack helpers) carry connector_id for Grafana/Loki filters.
_log_connector_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "nw_log_connector_id", default=None
)

# Longest-first so google_drive matches before a hypothetical google_ prefix.
_KNOWN_CONNECTOR_IDS = tuple(
    sorted(
        (
            "google_drive",
            "fhir_cerner",
            "fhir_epic",
            "http_generic",
            "salesforce",
            "stripe",
            "smtp",
            "slack",
        ),
        key=len,
        reverse=True,
    )
)


def set_log_connector_id(connector_id: str) -> contextvars.Token:
    """Bind ``connector_id`` for the current task; reset with the returned token."""
    return _log_connector_id_ctx.set((connector_id or "").strip() or None)


def reset_log_connector_id(token: contextvars.Token) -> None:
    _log_connector_id_ctx.reset(token)


def get_log_connector_id() -> Optional[str]:
    return _log_connector_id_ctx.get()


def connector_id_from_tool_name(tool_name: str) -> Optional[str]:
    """Map an MCP tool name to a connector id, or None for platform/unknown tools."""
    raw = (tool_name or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered.startswith("nw_") or lowered.startswith("nw."):
        return None
    if "." in raw:
        prefix = raw.split(".", 1)[0]
        if prefix in _KNOWN_CONNECTOR_IDS:
            return prefix
    for cid in _KNOWN_CONNECTOR_IDS:
        if raw == cid or raw.startswith(f"{cid}_"):
            return cid
    return None


class ConnectorIdLogFilter(logging.Filter):
    """Copy the run-scoped connector id onto records that do not already have one."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        existing = getattr(record, "connector_id", None)
        if existing:
            return True
        cid = get_log_connector_id()
        if cid:
            record.connector_id = cid
        return True


def install_sanitizing_log_filter() -> None:
    """Attach sanitizing + connector-id filters to the root logger once."""
    root = logging.getLogger()
    if not any(isinstance(flt, ConnectorIdLogFilter) for flt in root.filters):
        root.addFilter(ConnectorIdLogFilter())
    if any(isinstance(flt, SanitizingLogFilter) for flt in root.filters):
        return
    root.addFilter(SanitizingLogFilter())


def fhir_log_extra(
    trace_id: str,
    *,
    mode: str,
    connector_id: Optional[str] = None,
) -> dict[str, str]:
    """Safe structured ``extra`` for FHIR connector logs (no PHI fields)."""
    cid = (connector_id or get_log_connector_id() or "").strip()
    extra: dict[str, str] = {"trace_id": trace_id, "mode": mode}
    if cid:
        extra["connector_id"] = cid
    return extra


def log_http_status_error(
    log: logging.Logger,
    msg: str,
    exc: httpx.HTTPStatusError,
    *,
    trace_id: str,
    connector_id: Optional[str] = None,
) -> None:
    """Log HTTP failure with status and body length only (no response body)."""
    body = exc.response.text or ""
    cid = (connector_id or get_log_connector_id() or "").strip()
    extra: dict[str, str] = {"trace_id": trace_id}
    if cid:
        extra["connector_id"] = cid
    log.error(
        "%s | status=%s | body_length=%s",
        msg,
        exc.response.status_code,
        len(body),
        extra=extra,
    )
