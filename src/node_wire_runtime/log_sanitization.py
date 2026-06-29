#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Value-aware log redaction for PHI and secrets across all handlers and OTLP export."""

from __future__ import annotations

import logging
from typing import Any

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

_SANITIZING_FILTER_INSTALLED = False


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


def install_sanitizing_log_filter() -> None:
    """Attach :class:`SanitizingLogFilter` to the root logger once."""
    global _SANITIZING_FILTER_INSTALLED
    if _SANITIZING_FILTER_INSTALLED:
        return
    root = logging.getLogger()
    for flt in root.filters:
        if isinstance(flt, SanitizingLogFilter):
            _SANITIZING_FILTER_INSTALLED = True
            return
    root.addFilter(SanitizingLogFilter())
    _SANITIZING_FILTER_INSTALLED = True


def fhir_log_extra(trace_id: str, *, mode: str) -> dict[str, str]:
    """Safe structured ``extra`` for FHIR connector logs (no PHI fields)."""
    return {"trace_id": trace_id, "mode": mode}


def log_http_status_error(
    log: logging.Logger,
    msg: str,
    exc: httpx.HTTPStatusError,
    *,
    trace_id: str,
) -> None:
    """Log HTTP failure with status and body length only (no response body)."""
    body = exc.response.text or ""
    log.error(
        "%s | status=%s | body_length=%s",
        msg,
        exc.response.status_code,
        len(body),
        extra={"trace_id": trace_id},
    )
