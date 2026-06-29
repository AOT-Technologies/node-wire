#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
import os

import grpc

from .auth import _truthy

logger = logging.getLogger("bindings.grpc_server")

# Mirror the MCP binding's host model (bindings/mcp_server/server.py): default to
# loopback and require an explicit opt-in to expose the server on all interfaces.
_DEFAULT_GRPC_HOST = "127.0.0.1"
_PUBLIC_BIND_HOSTS = frozenset({"0.0.0.0", "::"})


def resolve_grpc_host(env_value: str | None = None) -> str:
    """Resolve the gRPC bind host from ``NW_GRPC_HOST`` (default ``127.0.0.1``)."""
    if env_value is not None:
        return env_value.strip()
    return os.getenv("NW_GRPC_HOST", _DEFAULT_GRPC_HOST).strip()


def is_public_bind_host(host: str) -> bool:
    return host in _PUBLIC_BIND_HOSTS


def _format_bind_target(host: str, port: int) -> str:
    """Build a gRPC bind string, bracketing IPv6 literals (``[::1]:50051``)."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _tls_configured(cert_path: str | None, key_path: str | None) -> bool:
    return bool(cert_path and key_path)


def configure_grpc_server_port(
    server: grpc.Server,
    *,
    port: int,
    host: str | None = None,
    cert_path: str | None = None,
    key_path: str | None = None,
    require_tls: bool | None = None,
) -> str:
    """Bind gRPC server port with optional TLS. Returns ``secure`` or ``insecure``."""
    if require_tls is None:
        require_tls = _truthy(os.environ.get("NW_GRPC_REQUIRE_TLS"))

    if host is None:
        host = resolve_grpc_host()
    bind_target = _format_bind_target(host, port)

    if is_public_bind_host(host):
        logger.warning(
            "gRPC server binding to all interfaces; set NW_GRPC_HOST=127.0.0.1 "
            "for local-only access",
            extra={"host": host, "port": port},
        )

    if _tls_configured(cert_path, key_path):
        assert key_path is not None
        assert cert_path is not None
        with open(key_path, "rb") as f:
            private_key = f.read()
        with open(cert_path, "rb") as f:
            certificate_chain = f.read()

        server_credentials = grpc.ssl_server_credentials(((private_key, certificate_chain),))
        server.add_secure_port(bind_target, server_credentials)
        logger.info(
            "Starting secure gRPC server (TLS enabled)",
            extra={"host": host, "port": port},
        )
        return "secure"

    if require_tls:
        raise ValueError(
            "gRPC TLS is required (NW_GRPC_REQUIRE_TLS=true) but NW_GRPC_TLS_CERT_PATH "
            "and NW_GRPC_TLS_KEY_PATH are not both set."
        )

    if cert_path or key_path:
        logger.warning(
            "Incomplete gRPC TLS configuration; both NW_GRPC_TLS_CERT_PATH and "
            "NW_GRPC_TLS_KEY_PATH must be set.",
            extra={"port": port},
        )

    server.add_insecure_port(bind_target)
    logger.warning(
        "Starting insecure gRPC server (no TLS credentials found). "
        "Traffic will be unencrypted. Set NW_GRPC_TLS_CERT_PATH and NW_GRPC_TLS_KEY_PATH, "
        "or NW_GRPC_REQUIRE_TLS=true to fail startup in production.",
        extra={"host": host, "port": port},
    )
    return "insecure"
