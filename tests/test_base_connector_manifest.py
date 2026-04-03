from __future__ import annotations

from importlib import import_module
from typing import Any, Dict

import pytest

from bindings.factory import ConnectorFactory
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime.manifest import build_manifest
from node_wire_stripe.schema import ChargeInput
from node_wire_runtime import BaseConnector
from node_wire_runtime.base_connector import _CONNECTOR_REGISTRY


def _normalize_for_mcp(connector_id: str, action: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Test harness: resolve MCP connector and run metadata-driven normalizers."""
    norm = import_module("bindings.mcp_server.server").normalize_mcp_tool_arguments
    auto_register()
    factory = ConnectorFactory()
    factory.load()
    connector = factory.get_for_protocol(connector_id, "mcp")
    assert connector is not None
    return norm(connector, action, arguments)


def test_registry_contains_base_connectors():
    auto_register()
    assert "google_drive" in _CONNECTOR_REGISTRY
    assert "stripe" in _CONNECTOR_REGISTRY
    assert "fhir_epic" in _CONNECTOR_REGISTRY


def test_manifest_emits_per_action():
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
    # Per-action input schema should expose that action's fields (not only a buried union)
    for entry in mcp_manifest:
        if entry["connector_id"] == "stripe":
            props = entry["input_schema"].get("properties", {})
            assert "amount" in props


def test_stripe_connector_accepts_charge_payload():
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
    from node_wire_fhir_cerner.schema import FhirCernerPatientReadInput
    from node_wire_fhir_epic.schema import FhirPatientReadInput as FhirEpicPatientReadInput

    for cid in ("fhir_cerner", "fhir_epic"):
        out = _normalize_for_mcp(
            cid,
            "read_patient",
            {"patientId": "12724066"},
        )
        assert out["resource_id"] == "12724066"
        assert "patientId" not in out
        model = FhirCernerPatientReadInput if cid == "fhir_cerner" else FhirEpicPatientReadInput
        model.model_validate({**out, "action": "read_patient"})

    # Canonical resource_id wins over alias
    out2 = _normalize_for_mcp(
        "fhir_cerner",
        "read_patient",
        {"resource_id": "111", "patient_id": "222"},
    )
    assert out2["resource_id"] == "111"

    out3 = _normalize_for_mcp(
        "fhir_cerner",
        "read_patient",
        {"familyName": "Smith", "givenName": "John"},
    )
    assert out3["family_name"] == "Smith"
    assert out3["given_name"] == "John"


def test_normalize_mcp_tool_arguments_search_patients_maps_legacy():
    from node_wire_fhir_cerner.schema import FhirCernerPatientSearchInput

    out = _normalize_for_mcp(
        "fhir_cerner",
        "search_patients",
        {"patient_ids": "12724066,12724067"},
    )
    assert out["resource_ids"] == ["12724066", "12724067"]

    out2 = _normalize_for_mcp(
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
    from node_wire_google_drive.schema import FilesUploadOperation

    out = _normalize_for_mcp(
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
    from node_wire_google_drive.schema import FilesUploadOperation

    out = _normalize_for_mcp(
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
    from node_wire_google_drive.schema import FilesUploadOperation

    out = _normalize_for_mcp(
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
    from node_wire_google_drive.schema import FilesUploadOperation

    out = _normalize_for_mcp(
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
    from node_wire_google_drive.schema import FilesUploadOperation

    out = _normalize_for_mcp(
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
    from node_wire_google_drive.schema import FilesUploadOperation

    out = _normalize_for_mcp(
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
    from node_wire_google_drive.schema import FilesUploadOperation

    out = _normalize_for_mcp(
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
    from node_wire_google_drive.schema import FilesUploadOperation

    out = _normalize_for_mcp(
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
    out = _normalize_for_mcp(
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
    """invoke_tool should apply normalization before BaseConnector.run."""
    from bindings.mcp_server.server import McpServer
    from node_wire_runtime.models import ConnectorResponse

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
    from node_wire_runtime.models import ConnectorResponse

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


def test_build_manifest_mcp_input_schema_omits_action_property() -> None:
    """MCP/REST manifest must not expose `action` in inputSchema (injected by binding)."""
    auto_register()
    factory = ConnectorFactory()
    factory.load()
    for entry in build_manifest(factory.list_for_protocol("mcp")):
        props = entry["input_schema"].get("properties") or {}
        assert "action" not in props, entry


@pytest.mark.asyncio
async def test_mcp_server_invoke_rejects_legacy_upload_when_env_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD", "reject")
    from bindings.mcp_server.server import McpServer

    server = McpServer(connector_ids=["google_drive"])
    with pytest.raises(ValueError, match="does not match"):
        await server.invoke_tool(
            "google_drive.files.upload",
            {
                "name": "x.txt",
                "mime_type": "text/plain",
                "content": "a",
                "action": "upload",
            },
        )


@pytest.mark.asyncio
async def test_mcp_server_invoke_rejects_conflicting_action() -> None:
    """Tool name action must match payload after normalization (no action spoofing)."""
    from bindings.mcp_server.server import McpServer

    server = McpServer(connector_ids=["google_drive"])

    with pytest.raises(ValueError, match="does not match"):
        await server.invoke_tool(
            "google_drive.files.upload",
            {"name": "x.txt", "mime_type": "text/plain", "content": "a", "action": "files.list"},
        )


def test_normalize_fhir_search_encounter_maps_llm_aliases():
    out = _normalize_for_mcp(
        "fhir_cerner",
        "search_encounter",
        {
            "patient": "12748336",
            "sort": "-date",
            "status": "finished",
        },
    )
    assert out["patient_id"] == "12748336"
    assert out["search_params"]["_sort"] == "-date"
    assert out.get("patient") is None


def test_normalize_mcp_tool_arguments_smtp_send_email_from_alias():
    from node_wire_smtp.schema import SmtpSendInput

    out = _normalize_for_mcp(
        "smtp",
        "send_email",
        {"from": "sender@example.com", "to": ["recipient@example.com"], "subject": "Hi", "body": "Hello"},
    )
    assert out["from_email"] == "sender@example.com"
    assert "from" not in out
    SmtpSendInput.model_validate({**out, "action": "send_email"})


def test_normalize_mcp_tool_arguments_smtp_send_email_sender_alias():
    out = _normalize_for_mcp(
        "smtp",
        "send_email",
        {"sender": "s@example.com", "to": ["r@example.com"], "subject": "x", "body": "y"},
    )
    assert out["from_email"] == "s@example.com"
    assert "sender" not in out


def test_normalize_mcp_tool_arguments_smtp_send_email_canonical_wins():
    out = _normalize_for_mcp(
        "smtp",
        "send_email",
        {
            "from_email": "canonical@example.com",
            "from": "alias@example.com",
            "to": ["r@example.com"],
            "subject": "x",
            "body": "y",
        },
    )
    assert out["from_email"] == "canonical@example.com"
    assert "from" not in out


def test_normalize_mcp_tool_arguments_smtp_send_email_to_string_to_list():
    from node_wire_smtp.schema import SmtpSendInput

    out = _normalize_for_mcp(
        "smtp",
        "send_email",
        {"from_email": "s@example.com", "to": "r@example.com", "subject": "x", "body": "y"},
    )
    assert out["to"] == ["r@example.com"]
    SmtpSendInput.model_validate({**out, "action": "send_email"})


@pytest.mark.asyncio
async def test_mcp_server_invoke_smtp_send_email_normalizes_payload() -> None:
    """invoke_tool should normalize SMTP aliases before connector.run."""
    from bindings.mcp_server.server import McpServer
    from node_wire_runtime.models import ConnectorResponse

    server = McpServer(connector_ids=["smtp"])
    smtp = server._factory.get_for_protocol("smtp", "mcp")
    assert smtp is not None

    captured: dict = {}

    async def fake_run(raw_input):
        captured["payload"] = dict(raw_input)
        return ConnectorResponse(success=True, data={"sent": True}, trace_id="t")

    orig_run = smtp.run
    try:
        smtp.run = fake_run
        await server.invoke_tool(
            "smtp.send_email",
            {"from": "sender@example.com", "to": "recipient@example.com", "subject": "Test", "body": "Body"},
        )
    finally:
        smtp.run = orig_run

    assert captured["payload"]["from_email"] == "sender@example.com"
    assert captured["payload"]["to"] == ["recipient@example.com"]
    assert "from" not in captured["payload"]
    assert captured["payload"].get("action") == "send_email"


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


# ---------------------------------------------------------------------------
# Enterprise-quality schema contract tests
# ---------------------------------------------------------------------------


def test_mcp_server_list_tools_output_schema_is_connector_response_envelope():
    """output_schema must be the ConnectorResponse envelope with correct structure."""
    from bindings.mcp_server.server import McpServer

    server = McpServer()
    tools = server.list_tools()
    assert tools
    for t in tools:
        assert "output_schema" in t
        schema = t["output_schema"]
        assert schema.get("title") == "ConnectorResponse"
        assert schema.get("type") == "object"
        props = schema.get("properties", {})
        assert "success" in props
        assert "data" in props
        assert "trace_id" in props
        assert "error_code" in props
        assert "error_category" in props
        assert set(schema.get("required", [])) == {"success", "trace_id"}


def test_connector_response_schema_embeds_output_model_in_data():
    """_connector_response_schema must inline the output model schema as data, no $ref/$defs."""
    from node_wire_runtime.manifest import _connector_response_schema
    from node_wire_smtp.schema import SmtpSendOutput

    schema = _connector_response_schema(SmtpSendOutput)
    assert schema["title"] == "ConnectorResponse"
    assert schema["type"] == "object"
    props = schema["properties"]
    assert props["success"] == {"type": "boolean"}
    assert props["trace_id"] == {"type": "string"}
    # data must contain the SmtpSendOutput properties (nullable union branch)
    data_any = props["data"]["anyOf"]
    output_branch = next(b for b in data_any if b.get("type") != "null")
    assert "sent" in output_branch.get("properties", {})
    # error_category must inline the enum from runtime (no $ref to avoid $defs leakage)
    ec = props["error_category"]
    enum_values = ec["anyOf"][0]["enum"]
    from node_wire_runtime.models import ErrorCategory

    assert set(enum_values) == {e.value for e in ErrorCategory}
    assert "$ref" not in str(ec)
    assert "$defs" not in schema


def test_manifest_strict_action_retains_additional_properties():
    """Actions not marked alias_tolerant must preserve additionalProperties:false."""
    auto_register()
    factory = ConnectorFactory()
    factory.load()
    mcp_manifest = build_manifest(factory.list_for_protocol("mcp"))

    # files.list uses BaseDriveOperation(extra="forbid") and is not alias_tolerant
    files_list = next(
        e for e in mcp_manifest
        if e["connector_id"] == "google_drive" and e["action"] == "files.list"
    )
    assert files_list["input_schema"].get("additionalProperties") is False


def test_manifest_alias_tolerant_actions_strip_additional_properties():
    """Actions marked alias_tolerant=True must have additionalProperties removed."""
    auto_register()
    factory = ConnectorFactory()
    factory.load()
    mcp_manifest = build_manifest(factory.list_for_protocol("mcp"))
    by_key = {(e["connector_id"], e["action"]): e for e in mcp_manifest}

    # files.upload is alias_tolerant via SdkActionSpec
    assert "additionalProperties" not in by_key[("google_drive", "files.upload")]["input_schema"]
    # smtp send_email is alias_tolerant via @sdk_action kwarg
    assert "additionalProperties" not in by_key[("smtp", "send_email")]["input_schema"]
    # fhir read_patient / search_patients / search_encounter are alias_tolerant
    assert "additionalProperties" not in by_key[("fhir_cerner", "read_patient")]["input_schema"]
    assert "additionalProperties" not in by_key[("fhir_cerner", "search_patients")]["input_schema"]
    assert "additionalProperties" not in by_key[("fhir_cerner", "search_encounter")]["input_schema"]
    assert "additionalProperties" not in by_key[("fhir_epic", "read_patient")]["input_schema"]
    assert "additionalProperties" not in by_key[("fhir_epic", "search_patients")]["input_schema"]
    assert "additionalProperties" not in by_key[("fhir_epic", "search_encounter")]["input_schema"]


def test_sdk_action_meta_alias_tolerant_propagates():
    """alias_tolerant must be correctly stored in _action_registry for all paths."""
    auto_register()

    # google_drive files.upload: alias_tolerant via SdkActionSpec → _make_spec_handler
    gd_cls = _CONNECTOR_REGISTRY["google_drive"]
    assert gd_cls._action_registry["files.upload"].alias_tolerant is True
    assert gd_cls._action_registry["files.list"].alias_tolerant is False

    # smtp send_email: alias_tolerant via @sdk_action kwarg
    smtp_cls = _CONNECTOR_REGISTRY["smtp"]
    assert smtp_cls._action_registry["send_email"].alias_tolerant is True

    # fhir connectors
    cerner_cls = _CONNECTOR_REGISTRY["fhir_cerner"]
    assert cerner_cls._action_registry["read_patient"].alias_tolerant is True
    assert cerner_cls._action_registry["search_patients"].alias_tolerant is True
    assert cerner_cls._action_registry["search_encounter"].alias_tolerant is True

    epic_cls = _CONNECTOR_REGISTRY["fhir_epic"]
    assert epic_cls._action_registry["search_encounter"].alias_tolerant is True


def test_manifest_error_category_enum_matches_runtime_error_category():
    """Emitted JSON Schema enum must stay in sync with ErrorCategory."""
    from node_wire_runtime.manifest import _error_category_json_schema
    from node_wire_runtime.models import ErrorCategory

    schema = _error_category_json_schema()
    assert set(schema["enum"]) == {e.value for e in ErrorCategory}


def test_sdk_action_meta_mcp_normalize_propagates():
    """mcp_normalize registered on actions must appear in _action_registry."""
    auto_register()

    gd_cls = _CONNECTOR_REGISTRY["google_drive"]
    assert gd_cls._action_registry["files.upload"].mcp_normalize is not None
    assert gd_cls._action_registry["files.list"].mcp_normalize is None

    smtp_cls = _CONNECTOR_REGISTRY["smtp"]
    assert smtp_cls._action_registry["send_email"].mcp_normalize is not None

    cerner_cls = _CONNECTOR_REGISTRY["fhir_cerner"]
    assert cerner_cls._action_registry["read_patient"].mcp_normalize is not None
    assert cerner_cls._action_registry["search_patients"].mcp_normalize is not None
    assert cerner_cls._action_registry["search_encounter"].mcp_normalize is not None

    epic_cls = _CONNECTOR_REGISTRY["fhir_epic"]
    assert epic_cls._action_registry["search_encounter"].mcp_normalize is not None


@pytest.mark.asyncio
async def test_mcp_server_invoke_tool_failure_payload_matches_output_schema_shape() -> None:
    """Error ConnectorResponse (data=None) matches manifest output_schema (nullable data)."""
    from bindings.mcp_server.server import McpServer
    from node_wire_runtime.models import ConnectorResponse, ErrorCategory

    server = McpServer(connector_ids=["smtp"])
    smtp = server._factory.get_for_protocol("smtp", "mcp")
    assert smtp is not None

    entry = next(e for e in server.list_tools() if e["name"] == "smtp.send_email")
    schema = entry["output_schema"]
    data_prop = schema["properties"]["data"]
    assert {"type": "null"} in data_prop["anyOf"]

    async def fake_run(_raw_input):
        return ConnectorResponse(
            success=False,
            data=None,
            error_code="VALIDATION_ERROR",
            error_category=ErrorCategory.BUSINESS,
            message="bad",
            trace_id="trace-1",
            details=[{"loc": ["x"], "msg": "y", "type": "value_error"}],
        )

    orig_run = smtp.run
    try:
        smtp.run = fake_run
        out = await server.invoke_tool(
            "smtp.send_email",
            {"from_email": "a@b.com", "to": ["x@y.com"], "subject": "s", "body": "b"},
        )
    finally:
        smtp.run = orig_run

    assert out["success"] is False
    assert out["data"] is None
    assert out["error_code"] == "VALIDATION_ERROR"
    assert out["trace_id"] == "trace-1"


def test_normalize_mcp_tool_arguments_noop_when_action_has_no_normalizer():
    """Strict actions without mcp_normalize should pass args through unchanged."""
    from bindings.mcp_server.server import normalize_mcp_tool_arguments

    auto_register()
    factory = ConnectorFactory()
    factory.load()
    connector = factory.get_for_protocol("google_drive", "mcp")
    assert connector is not None
    raw = {"action": "files.list", "page_size": 10}
    out = normalize_mcp_tool_arguments(connector, "files.list", raw)
    assert out == raw
