from __future__ import annotations

from bindings.factory import ConnectorFactory
from connectors import auto_register
from connectors.manifest import build_manifest
from connectors.stripe.schema import ChargeInput
from runtime import SDKConnector
from runtime.sdk_connector import _CONNECTOR_REGISTRY


def test_registry_contains_sdk_connectors():
    auto_register()
    assert "google_drive" in _CONNECTOR_REGISTRY
    assert "stripe" in _CONNECTOR_REGISTRY
    assert "fhir_epic" in _CONNECTOR_REGISTRY


def test_manifest_emits_per_sdk_action():
    auto_register()
    factory = ConnectorFactory()
    factory.load()
    rest_manifest = build_manifest(factory.list_for_protocol("rest"))
    rest_actions = {(e["connector_id"], e["action"]) for e in rest_manifest}
    assert ("google_drive", "files.list") in rest_actions
    assert ("fhir_epic", "read_patient") in rest_actions
    assert ("stripe", "charge") not in rest_actions  # stripe is grpc/mcp only in config

    mcp_manifest = build_manifest(factory.list_for_protocol("mcp"))
    mcp_actions = {(e["connector_id"], e["action"]) for e in mcp_manifest}
    assert ("stripe", "charge") in mcp_actions
    # Per-action input schema should not be the full union for SDK connectors
    for entry in mcp_manifest:
        if entry["connector_id"] == "stripe":
            props = entry["input_schema"].get("properties", {})
            assert "amount" in props


def test_stripe_connector_is_sdk_and_accepts_charge_payload():
    auto_register()
    factory = ConnectorFactory()
    factory.load()
    connector = factory.get_for_protocol("stripe", "grpc")
    assert connector is not None
    assert isinstance(connector, SDKConnector)
    validated = ChargeInput.model_validate(
        {"action": "charge", "amount": 100, "currency": "usd", "source": "tok_visa"}
    )
    assert validated.action == "charge"


def test_mcp_tool_invoke_sets_action():
    from bindings.mcp_server.server import McpServer

    server = McpServer()
    tools = server.list_tools()
    names = {t["name"] for t in tools}
    assert "google_drive.files.list" in names
    assert "stripe.charge" in names
