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


def _tls_configured(cert_path: str | None, key_path: str | None) -> bool:
    return bool(cert_path and key_path)


def configure_grpc_server_port(
    server: grpc.Server,
    *,
    port: int,
    cert_path: str | None = None,
    key_path: str | None = None,
    require_tls: bool | None = None,
) -> str:
    """Bind gRPC server port with optional TLS. Returns ``secure`` or ``insecure``."""
    if require_tls is None:
        require_tls = _truthy(os.environ.get("NW_GRPC_REQUIRE_TLS"))

    if _tls_configured(cert_path, key_path):
        assert key_path is not None
        assert cert_path is not None
        with open(key_path, "rb") as f:
            private_key = f.read()
        with open(cert_path, "rb") as f:
            certificate_chain = f.read()

        server_credentials = grpc.ssl_server_credentials(((private_key, certificate_chain),))
        server.add_secure_port(f"[::]:{port}", server_credentials)
        logger.info("Starting secure gRPC server (TLS enabled)", extra={"port": port})
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

    server.add_insecure_port(f"[::]:{port}")
    logger.warning(
        "Starting insecure gRPC server (no TLS credentials found). "
        "Traffic will be unencrypted. Set NW_GRPC_TLS_CERT_PATH and NW_GRPC_TLS_KEY_PATH, "
        "or NW_GRPC_REQUIRE_TLS=true to fail startup in production.",
        extra={"port": port},
    )
    return "insecure"
