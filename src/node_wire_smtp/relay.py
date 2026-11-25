#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Server-side SMTP relay configuration (credentials must never go to caller-chosen hosts)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


class SmtpRelayNotAllowedError(ValueError):
    """Raised when the configured SMTP relay is not permitted by policy."""


@dataclass(frozen=True)
class SmtpRelayConfig:
    host: str
    port: int
    use_tls: bool


def _strip_env(s: str) -> str:
    return s.strip(" '\"")


def _normalize_hostname(host: str) -> str:
    return host.strip().lower().rstrip(".")


def load_smtp_allowed_hosts() -> frozenset[str] | None:
    raw = os.environ.get("NW_SMTP_ALLOWED_HOSTS")
    if raw is None or not raw.strip():
        return None
    hosts = {_normalize_hostname(part) for part in raw.split(",") if part.strip()}
    return frozenset(hosts) if hosts else None


def resolve_smtp_relay(config: Mapping[str, Any] | None = None) -> SmtpRelayConfig:
    """Load the pinned relay: the environment first, then connectors.yaml (``host``, ``port``,
    ``use_tls``), then the defaults. Callers can never supply any of it.

    Simplified: the environment wins over connectors.yaml, so a deployment that already sets
    SMTP_HOST keeps working when the file still holds its placeholder host.
    """
    config = config or {}
    host_raw = os.environ.get("SMTP_HOST")
    if host_raw is None:
        host_raw = str(config.get("host") or "smtp.gmail.com")
    host = _strip_env(host_raw)
    if not host:
        raise SmtpRelayNotAllowedError("SMTP relay is not configured (SMTP_HOST is empty)")

    port_raw = os.environ.get("SMTP_PORT")
    if port_raw is None:
        port_raw = str(config.get("port") or "587")
    port_raw = _strip_env(port_raw)
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise SmtpRelayNotAllowedError("SMTP relay port is invalid") from exc

    tls_raw = os.environ.get("SMTP_USE_TLS")
    if tls_raw is None:
        tls_raw = str(config.get("use_tls", "true"))
    use_tls = tls_raw.strip().lower() in ("1", "true", "yes", "on")
    relay = SmtpRelayConfig(host=host, port=port, use_tls=use_tls)

    allowlist = load_smtp_allowed_hosts()
    if allowlist is not None and _normalize_hostname(relay.host) not in allowlist:
        raise SmtpRelayNotAllowedError("SMTP relay host is not on the allowed hosts list")

    return relay
