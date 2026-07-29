#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for gRPC health service registration in serve()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from grpc_health.v1 import health_pb2


def test_grpc_health_servicer_is_registered():
    """serve() must register a HealthServicer and set SERVING for both service names."""
    added_servicers = {}

    def fake_add_health(servicer, server):
        added_servicers["health"] = servicer

    def fake_add_connector(servicer, server):
        added_servicers["connector"] = servicer

    with (
        patch("bindings.grpc_server.server.grpc.server") as mock_grpc_server,
        patch(
            "bindings.grpc_server.server.connector_pb2_grpc.add_ConnectorServiceServicer_to_server",
            fake_add_connector,
        ),
        patch(
            "bindings.grpc_server.server.health_pb2_grpc.add_HealthServicer_to_server",
            fake_add_health,
        ),
        patch("bindings.grpc_server.server.grpc_health.HealthServicer") as mock_health_cls,
        patch("bindings.grpc_server.server.configure_grpc_server_port"),
        patch("bindings.grpc_server.server._async_runner"),
        patch("bindings.grpc_server.server.ConnectorServiceServicer"),
    ):
        fake_server = MagicMock()
        fake_server.wait_for_termination.side_effect = KeyboardInterrupt
        mock_grpc_server.return_value = fake_server

        mock_health_instance = MagicMock()
        mock_health_cls.return_value = mock_health_instance

        from bindings.grpc_server.server import serve

        try:
            serve(port=0)
        except KeyboardInterrupt:
            pass  # serve() raises KeyboardInterrupt on shutdown; suppress to let assertions run

        assert "health" in added_servicers, "HealthServicer was not added to the gRPC server"

        set_calls = {call.args[0]: call.args[1] for call in mock_health_instance.set.call_args_list}
        assert "" in set_calls, "Overall health (empty string) not set"
        assert "aot.connectors.ConnectorService" in set_calls, "ConnectorService health not set"
        assert set_calls[""] == health_pb2.HealthCheckResponse.SERVING
        assert (
            set_calls["aot.connectors.ConnectorService"] == health_pb2.HealthCheckResponse.SERVING
        )
