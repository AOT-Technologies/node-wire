#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""MCP manifest contract: unified vs per-connector entrypoints expose consistent tool shapes."""

from __future__ import annotations

from node_wire_runtime.manifest import MCP_MANIFEST_CONTRACT_VERSION

ALL_CONNECTOR_IDS = (
    "http_generic",
    "smtp",
    "stripe",
    "google_drive",
    "fhir_epic",
    "fhir_cerner",
    "salesforce",
    "slack",
)


def test_manifest_contract_version_is_exported() -> None:
    assert MCP_MANIFEST_CONTRACT_VERSION
    assert int(MCP_MANIFEST_CONTRACT_VERSION) >= 2


def test_per_connector_tool_names_are_subsets_of_unified() -> None:
    from bindings.factory import ConnectorFactory
    from node_wire_runtime.connector_registry import auto_register

    auto_register()
    factory = ConnectorFactory()
    factory.load()
    full = {t["name"] for t in _tools_from_server()}
    cerner = {t["name"] for t in _tools_from_server(connector_ids=["fhir_cerner"])}
    assert cerner == {n for n in full if n.startswith("fhir_cerner.")}
    drive = {t["name"] for t in _tools_from_server(connector_ids=["google_drive"])}
    assert drive == {n for n in full if n.startswith("google_drive.")}
    assert "google_drive.files.upload" in drive


def test_all_eight_connectors_tools_are_subsets_of_unified() -> None:
    full = {t["name"] for t in _tools_from_server()}
    for connector_id in ALL_CONNECTOR_IDS:
        subset = {t["name"] for t in _tools_from_server(connector_ids=[connector_id])}
        expected = {name for name in full if name.startswith(f"{connector_id}.")}
        assert subset == expected, connector_id


def test_unified_manifest_includes_representative_tools_for_each_connector() -> None:
    full = {t["name"] for t in _tools_from_server()}
    expected_tools = {
        "http_generic.request",
        "smtp.send_email",
        "stripe.charge",
        "google_drive.files.list",
        "fhir_epic.read_patient",
        "fhir_cerner.read_patient",
        "salesforce.create_contact",
        "slack.post_message",
    }
    assert expected_tools.issubset(full)


def _tools_from_server(connector_ids: list[str] | None = None) -> list[dict]:
    from bindings.mcp_server.server import McpServer

    if connector_ids is None:
        server = McpServer()
    else:
        server = McpServer(connector_ids=connector_ids)
    return server.list_tools()
