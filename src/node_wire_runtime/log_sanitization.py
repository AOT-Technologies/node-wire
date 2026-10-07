#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Value-aware log redaction for PHI and secrets across all handlers and OTLP export."""

from __future__ import annotations

import contextvars
import logging
import re
from typing import Any, Optional

import httpx

from .http_safety import sanitize_url_for_log

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

# Credential-bearing keys. Matched like _SENSITIVE_SUBSTRINGS, except numbers are kept so
# usage counts (``prompt_tokens``, ``total_tokens``) still log.
_SECRET_SUBSTRINGS = {
    "token",
    "authorization",
    "apikey",
    "bearer",
    "cookie",
    "credential",
}

_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")
_BEARER_RE = re.compile(r"\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)

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

LOG_RECORD_STANDARD_KEYS = frozenset(
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


def _is_phi_key(key: str) -> bool:
    if key.lower() in _ALWAYS_REDACT_KEYS:
        return True
    k = _normalize_key(key)
    return any(s in k for s in _SENSITIVE_SUBSTRINGS)


def _is_secret_key(key: str) -> bool:
    k = _normalize_key(key)
    return any(s in k for s in _SECRET_SUBSTRINGS)


def is_sensitive_key(key: str) -> bool:
    """Return True when an attribute/key should be fully redacted."""
    return _is_phi_key(key) or _is_secret_key(key)


def redact_credentials(text: str) -> str:
    """Drop URL query strings, fragments and userinfo, and ``Bearer <token>`` values, from
    free text such as ``str(exc)`` (httpx puts the full request URL in its messages)."""
    text = _URL_RE.sub(lambda m: sanitize_url_for_log(m.group(0)), text)
    return _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)


def sanitize_value(key: str, value: Any) -> Any:
    """Recursively redact sensitive values."""
    if _is_phi_key(key):
        return REDACTED
    if _is_secret_key(key) and not (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    ):
        return REDACTED
    if isinstance(value, dict):
        return {k: sanitize_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_value(key, item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_value(key, item) for item in value)
    if isinstance(value, str):
        return redact_credentials(value)
    return value


def sanitize_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    return {k: sanitize_value(str(k), v) for k, v in mapping.items()}


def _redact_sensitive_string_arg(value: str) -> str:
    if len(value) > 100:
        return REDACTED
    lowered = value.lower()
    if "phi_marker" in lowered:
        return REDACTED
    return redact_credentials(value)


def sanitize_log_record(record: logging.LogRecord) -> None:
    """Sanitize message args and dynamic attributes on a log record in place."""
    # With args, msg is a format string; scrubbing it could drop a ``%s`` from a URL.
    if isinstance(record.msg, str) and not record.args:
        record.msg = redact_credentials(record.msg)
    if record.args:
        if isinstance(record.args, dict):
            record.args = sanitize_mapping(record.args)  # type: ignore[assignment]
        elif isinstance(record.args, tuple):
            record.args = tuple(
                _redact_sensitive_string_arg(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )

    for key in list(record.__dict__.keys()):
        if key in LOG_RECORD_STANDARD_KEYS:
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


def add_filter_once(target: logging.Filterer, flt: logging.Filter) -> None:
    """Add ``flt`` to ``target`` unless a filter of the same type is already there."""
    if not any(type(existing) is type(flt) for existing in target.filters):
        target.addFilter(flt)


def install_sanitizing_log_filter() -> None:
    """Attach connector-id + sanitizing filters to the root logger and its handlers.

    Logger filters only run for records logged on that logger, not for records propagated
    from children (``runtime.base_connector`` etc.), so the handlers carry them too. Call
    again after adding root handlers (e.g. after ``logging.config.dictConfig``); it is
    idempotent.
    """
    root = logging.getLogger()
    for target in (root, *root.handlers):
        add_filter_once(target, ConnectorIdLogFilter())
        add_filter_once(target, SanitizingLogFilter())


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
