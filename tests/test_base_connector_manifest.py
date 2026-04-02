from __future__ import annotations

import pytest

from bindings.factory import ConnectorFactory
from connectors import auto_register
from connectors.manifest import build_manifest
from connectors.stripe.schema import ChargeInput
from runtime import BaseConnector
from runtime.base_connector import _CONNECTOR_REGISTRY


def test_registry_contains_base_connectors():
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
    assert isinstance(connector, BaseConnector)
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


def test_mcp_server_list_tools_includes_output_schema():
    from bindings.mcp_server.server import McpServer

    server = McpServer()
    tools = server.list_tools()
    assert tools
    assert all("output_schema" in t for t in tools)


def test_mcp_server_connector_ids_filters_list_tools():
    from bindings.mcp_server.server import McpServer

    server = McpServer(connector_ids=["fhir_cerner"])
    names = {t["name"] for t in server.list_tools()}
    assert names
    assert all(n.startswith("fhir_cerner.") for n in names)
    assert "fhir_epic.read_patient" not in names


@pytest.mark.asyncio
async def test_mcp_server_invoke_rejects_disallowed_connector() -> None:
    from bindings.mcp_server.server import McpServer

    server = McpServer(connector_ids=["google_drive"])
    with pytest.raises(ValueError, match="not allowed"):
        await server.invoke_tool(
            "smtp.send_email",
            {"to": ["doc@example.com"], "subject": "x", "body": "y"},
        )


def test_mcp_server_run_stdio_smoke():
    from bindings.mcp_server.server import McpServer

    server = McpServer()
    assert callable(server.run_stdio)
    assert callable(server._run_stdio_async)


def test_normalize_mcp_tool_arguments_read_patient_maps_legacy_ids():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.fhir_cerner.schema import FhirCernerPatientReadInput
    from connectors.fhir_epic.schema import FhirPatientReadInput as FhirEpicPatientReadInput

    for cid in ("fhir_cerner", "fhir_epic"):
        out = normalize_mcp_tool_arguments(
            cid,
            "read_patient",
            {"patientId": "12724066"},
        )
        assert out["resource_id"] == "12724066"
        assert "patientId" not in out
        model = FhirCernerPatientReadInput if cid == "fhir_cerner" else FhirEpicPatientReadInput
        model.model_validate({**out, "action": "read_patient"})

    # Canonical resource_id wins over alias
    out2 = normalize_mcp_tool_arguments(
        "fhir_cerner",
        "read_patient",
        {"resource_id": "111", "patient_id": "222"},
    )
    assert out2["resource_id"] == "111"

    out3 = normalize_mcp_tool_arguments(
        "fhir_cerner",
        "read_patient",
        {"familyName": "Smith", "givenName": "John"},
    )
    assert out3["family_name"] == "Smith"
    assert out3["given_name"] == "John"


def test_normalize_mcp_tool_arguments_search_patients_maps_legacy():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.fhir_cerner.schema import FhirCernerPatientSearchInput

    out = normalize_mcp_tool_arguments(
        "fhir_cerner",
        "search_patients",
        {"patient_ids": "12724066,12724067"},
    )
    assert out["resource_ids"] == ["12724066", "12724067"]

    out2 = normalize_mcp_tool_arguments(
        "fhir_cerner",
        "search_patients",
        {"search_params": {"patientId": "12724066"}},
    )
    assert out2["search_params"]["identifier"] == "12724066"
    assert "patientId" not in out2["search_params"]

    FhirCernerPatientSearchInput.model_validate(
        {**out2, "action": "search_patients"}
    )


def test_normalize_mcp_tool_arguments_google_drive_files_upload_mime_type_alias():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.google_drive.schema import FilesUploadOperation

    out = normalize_mcp_tool_arguments(
        "google_drive",
        "files.upload",
        {
            "name": "a.txt",
            "mimeType": "text/plain",
            "parents": ["folder1"],
            "content": "hello",
        },
    )
    assert out["mime_type"] == "text/plain"
    assert "mimeType" not in out
    FilesUploadOperation.model_validate({**out, "action": "files.upload"})


def test_normalize_mcp_tool_arguments_google_drive_files_upload_action_upload():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.google_drive.schema import FilesUploadOperation

    out = normalize_mcp_tool_arguments(
        "google_drive",
        "files.upload",
        {
            "action": "upload",
            "name": "a.txt",
            "mime_type": "text/plain",
            "content": "x",
        },
    )
    assert out["action"] == "files.upload"
    FilesUploadOperation.model_validate(out)


def test_normalize_mcp_tool_arguments_google_drive_files_upload_nested_file():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.google_drive.schema import FilesUploadOperation

    out = normalize_mcp_tool_arguments(
        "google_drive",
        "files.upload",
        {
            "content": "body",
            "file": {
                "mime_type": "text/plain",
                "name": "nested.txt",
                "parents": ["p1"],
            },
        },
    )
    assert out["name"] == "nested.txt"
    assert out["mime_type"] == "text/plain"
    assert out["parents"] == ["p1"]
    assert "file" not in out
    FilesUploadOperation.model_validate({**out, "action": "files.upload"})


def test_normalize_mcp_tool_arguments_google_drive_files_upload_media_string_maps_to_content():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.google_drive.schema import FilesUploadOperation

    out = normalize_mcp_tool_arguments(
        "google_drive",
        "files.upload",
        {
            "name": "a.txt",
            "mime_type": "text/plain",
            "media": "hello",
        },
    )
    assert out["content"] == "hello"
    assert "media" not in out
    FilesUploadOperation.model_validate({**out, "action": "files.upload"})


def test_normalize_mcp_tool_arguments_google_drive_files_upload_media_object_text_alias_maps_to_content():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.google_drive.schema import FilesUploadOperation

    out = normalize_mcp_tool_arguments(
        "google_drive",
        "files.upload",
        {
            "name": "a.txt",
            "mime_type": "text/plain",
            "media": {"text": "hello"},
        },
    )
    assert out["content"] == "hello"
    assert "media" not in out
    FilesUploadOperation.model_validate({**out, "action": "files.upload"})


def test_normalize_mcp_tool_arguments_google_drive_files_upload_media_object_base64_maps_to_content_base64():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.google_drive.schema import FilesUploadOperation

    out = normalize_mcp_tool_arguments(
        "google_drive",
        "files.upload",
        {
            "name": "a.pdf",
            "mime_type": "application/pdf",
            "media": {"base64": "Zg=="},
        },
    )
    assert out["content_base64"] == "Zg=="
    assert "media" not in out
    FilesUploadOperation.model_validate({**out, "action": "files.upload"})


def test_normalize_mcp_tool_arguments_google_drive_files_upload_media_metadata_aliases_are_used_when_missing():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.google_drive.schema import FilesUploadOperation

    out = normalize_mcp_tool_arguments(
        "google_drive",
        "files.upload",
        {
            "media": {
                "name": "nested.txt",
                "mimeType": "text/plain",
                "parents": "p1,p2",
                "content": "hi",
            }
        },
    )
    assert out["name"] == "nested.txt"
    assert out["mime_type"] == "text/plain"
    assert out["parents"] == ["p1", "p2"]
    assert out["content"] == "hi"
    assert "media" not in out
    FilesUploadOperation.model_validate({**out, "action": "files.upload"})


def test_normalize_mcp_tool_arguments_google_drive_files_upload_canonical_content_wins_over_media_alias():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments
    from connectors.google_drive.schema import FilesUploadOperation

    out = normalize_mcp_tool_arguments(
        "google_drive",
        "files.upload",
        {
            "name": "root.txt",
            "mime_type": "text/plain",
            "content": "root",
            "media": {"content": "ignored"},
        },
    )
    assert out["content"] == "root"
    assert "media" not in out
    FilesUploadOperation.model_validate({**out, "action": "files.upload"})


def test_normalize_mcp_tool_arguments_google_drive_canonical_mime_type_wins_over_nested():
    from bindings.mcp_server.server import normalize_mcp_tool_arguments

    out = normalize_mcp_tool_arguments(
        "google_drive",
        "files.upload",
        {
            "mime_type": "text/plain",
            "name": "root.txt",
            "content": "c",
            "file": {"mime_type": "application/json", "name": "ignored.txt"},
        },
    )
    assert out["mime_type"] == "text/plain"
    assert out["name"] == "root.txt"


@pytest.mark.asyncio
async def test_mcp_server_invoke_tool_passes_normalized_payload_to_connector_run() -> None:
    """invoke_tool should apply normalization before BaseConnector.run (SDK action)."""
    from bindings.mcp_server.server import McpServer
    from runtime.models import ConnectorResponse

    server = McpServer(connector_ids=["fhir_cerner"])
    cerner = server._factory.get_for_protocol("fhir_cerner", "mcp")
    assert cerner is not None

    captured: dict = {}

    async def fake_run(raw_input):
        captured["payload"] = dict(raw_input)
        return ConnectorResponse(success=True, data={"resource": {"id": "12724066"}}, trace_id="t")

    orig_run = cerner.run
    try:
        cerner.run = fake_run
        await server.invoke_tool("fhir_cerner.read_patient", {"patientId": "12724066"})
    finally:
        cerner.run = orig_run

    assert captured["payload"]["resource_id"] == "12724066"
    assert captured["payload"].get("action") == "read_patient"


@pytest.mark.asyncio
async def test_mcp_server_invoke_google_drive_files_upload_normalizes_payload() -> None:
    """invoke_tool should normalize Drive upload aliases before connector.run."""
    from bindings.mcp_server.server import McpServer
    from runtime.models import ConnectorResponse

    server = McpServer(connector_ids=["google_drive"])
    gdrive = server._factory.get_for_protocol("google_drive", "mcp")
    assert gdrive is not None

    captured: dict = {}

    async def fake_run(raw_input):
        captured["payload"] = dict(raw_input)
        return ConnectorResponse(success=True, data={"raw": {}}, trace_id="t")

    orig_run = gdrive.run
    try:
        gdrive.run = fake_run
        await server.invoke_tool(
            "google_drive.files.upload",
            {
                "mimeType": "text/plain",
                "name": "patient_summary.txt",
                "parents": ["folder-id"],
                "content": "summary",
                "media": {"content": "ignored"},
                "action": "upload",
            },
        )
    finally:
        gdrive.run = orig_run

    assert captured["payload"]["mime_type"] == "text/plain"
    assert captured["payload"]["action"] == "files.upload"
    assert "mimeType" not in captured["payload"]
    assert "media" not in captured["payload"]


def test_mcp_server_invoke_tool_malformed_name() -> None:
    import asyncio

    from bindings.mcp_server.server import McpServer

    async def _run() -> None:
        server = McpServer()
        with pytest.raises(ValueError, match="Tool name must be in the form"):
            await server.invoke_tool("no_dot_separator", {})

    asyncio.run(_run())


def test_mcp_server_invoke_tool_connector_not_in_filter() -> None:
    import asyncio

    from bindings.mcp_server.server import McpServer

    async def _run() -> None:
        server = McpServer(connector_ids=["fhir_cerner"])
        with pytest.raises(ValueError, match="not allowed on this MCP server"):
            await server.invoke_tool("fhir_epic.read_patient", {"resource_id": "x"})

    asyncio.run(_run())


def test_mcp_server_invoke_tool_unknown_connector_id() -> None:
    import asyncio

    from bindings.mcp_server.server import McpServer

    async def _run() -> None:
        server = McpServer()
        with pytest.raises(ValueError, match="not available via MCP"):
            await server.invoke_tool("unknown_connector_xyz.read_patient", {})

    asyncio.run(_run())
