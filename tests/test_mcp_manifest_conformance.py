"""MCP manifest contract: unified vs per-connector entrypoints expose consistent tool shapes."""

from __future__ import annotations

from node_wire_runtime.manifest import MCP_MANIFEST_CONTRACT_VERSION


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


def _tools_from_server(connector_ids: list[str] | None = None) -> list[dict]:
    from bindings.mcp_server.server import McpServer

    if connector_ids is None:
        server = McpServer()
    else:
        server = McpServer(connector_ids=connector_ids)
    return server.list_tools()
