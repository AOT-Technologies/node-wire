# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
"""Run every action against a mock HTTP transport, so no Epic server is needed (CI has none)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from node_wire_fhir_epic import logic
from node_wire_fhir_epic.logic import FhirEpicConnector
from node_wire_runtime.auth import StaticTokenAuthProvider

BASE = "https://epic.test/api/FHIR/R4"
PHI = "PHI-MARKER-DO-NOT-LOG"

# The 124 reads, searches, creates and updates are named <verb>_<snake_case type>.
READS = (
    "account adverse_event allergy_intolerance appointment binary body_structure care_plan care_team claim "
    "communication condition consent contract coverage device device_request device_use_statement "
    "diagnostic_report document_reference encounter endpoint episode_of_care explanation_of_benefit "
    "family_member_history flag goal group imaging_study immunization immunization_recommendation list "
    "location media medication medication_administration medication_dispense medication_request "
    "medication_statement nutrition_order observation organization practitioner practitioner_role "
    "procedure provenance questionnaire questionnaire_response related_person request_group "
    "research_study research_subject service_request specimen substance task value_set"
).split()
READS.append("patient")  # read_patient is one of the original five
NO_SEARCH = {"provenance", "value_set"}
CREATES = (
    "allergy_intolerance body_structure communication condition observation patient procedure "
    "questionnaire_response service_request"
).split()
UPDATES = "body_structure diagnostic_report document_reference observation procedure service_request task".split()
OPERATIONS = {
    "fetch_next_page",
    "patient_match",
    "patient_summary",
    "value_set_expand",
    "bulk_export_group",
    "bulk_export_status",
    "bulk_export_file",
    "bulk_export_delete",
}
ORIGINAL = {
    "read_patient",
    "search_patients",
    "search_encounter",
    "create_document_reference",
    "search_document_reference",
}


def _type_of(snake: str) -> str:
    return "".join(p.capitalize() for p in snake.split("_"))


class Secrets:
    def get_secret(self, key: str) -> str:
        return {"epic_fhir_base_url": BASE + "/", "EPIC_TEST_TOKEN": "tok-123"}[key]


class Wire:
    """What the mock Epic saw, and what it answers next."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.json: Any = {"resourceType": "Bundle", "entry": []}
        self.content: bytes | None = None
        self.headers: dict[str, str] = {}
        self.error: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.content is not None:
            return httpx.Response(self.status, content=self.content, headers=self.headers)
        return httpx.Response(self.status, json=self.json, headers=self.headers)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    @property
    def body(self) -> Any:
        return json.loads(self.last.content) if self.last.content else None


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    w = Wire()
    client = httpx.AsyncClient(transport=httpx.MockTransport(w.handler))
    monkeypatch.setattr(logic, "_shared_client", lambda trust_env: client)
    return w


def make() -> FhirEpicConnector:
    secrets = Secrets()
    return FhirEpicConnector(
        secret_provider=secrets,
        auth_provider=StaticTokenAuthProvider(
            secret_provider=secrets, secret_key="EPIC_TEST_TOKEN"
        ),
    )


def test_every_epic_action_is_registered() -> None:
    # search_patient, search_encounter and the document_reference pair are among the original five
    expected = (
        {f"read_{t}" for t in READS}
        | {f"search_{t}" for t in READS if t not in NO_SEARCH and t != "patient"}
        | {f"create_{t}" for t in CREATES}
        | {f"update_{t}" for t in UPDATES}
        | OPERATIONS
        | ORIGINAL
    )
    assert set(FhirEpicConnector.sdk_action_metas()) == expected
    assert len(expected) == 137


# ---- every generated action sends the request it declares ---------------------------------------
def _minimal(action: str) -> dict[str, Any]:
    verb = action.split("_", 1)[0]
    rtype = _type_of(action.split("_", 1)[1])
    if verb == "read":
        return {"resource_id": "r1"}
    if verb == "search":
        return {"id": "r1"}
    if verb == "create":
        return {"resource": {"resourceType": rtype}}
    return {"resource_id": "r1", "resource": {"resourceType": rtype}}


GENERATED = sorted(
    {f"read_{t}" for t in READS if t != "patient"}
    | {
        f"search_{t}"
        for t in READS
        if t not in NO_SEARCH and t not in {"patient", "encounter", "document_reference"}
    }
    | {f"create_{t}" for t in CREATES}
    | {f"update_{t}" for t in UPDATES}
)


@pytest.mark.parametrize("action", GENERATED)
async def test_action_sends_the_declared_request(wire: Wire, action: str) -> None:
    wire.json = {"resourceType": "Bundle", "entry": []}
    wire.headers = {"Location": f"{BASE}/X/new-1/_history/1"}
    meta = FhirEpicConnector.sdk_action_metas()[action]
    result = await make().run({"action": action, **_minimal(action)})
    assert result.success, result.message
    request = wire.last
    assert request.method == meta.http_method
    assert request.url.path == "/api/FHIR/R4" + (meta.http_path or "").replace("{id}", "r1")
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert request.headers["Accept"] == "application/fhir+json"
    if action.startswith("search_"):
        assert dict(request.url.params) == {"_id": "r1"}


# ---- search inputs --------------------------------------------------------------------------------
async def test_epic_no_results_notice_is_not_a_result(wire: Wire) -> None:
    wire.json = {
        "resourceType": "Bundle",
        "entry": [
            {
                "search": {"mode": "outcome"},
                "resource": {"resourceType": "OperationOutcome", "issue": []},
            }
        ],
    }
    result = await make().run(
        {"action": "search_patients", "given_name": "No", "family_name": "One"}
    )
    assert result.success and result.data["resources"] == []


async def test_search_fields_go_out_under_their_fhir_names(wire: Wire) -> None:
    result = await make().run(
        {
            "action": "search_observation",
            "patient": "e1",
            "category": "vital-signs",
            "combo_code": "8480-6",
            "count": 5,
            "include": ["Observation:encounter", "Observation:performer"],
            "revinclude": "Provenance:target".split(),
        }
    )
    assert result.success, result.message
    params = wire.last.url.params
    assert (
        params["patient"] == "e1" and params["_count"] == "5" and params["combo-code"] == "8480-6"
    )
    assert params.get_list("_include") == ["Observation:encounter", "Observation:performer"]
    assert params["_revinclude"] == "Provenance:target"


async def test_a_search_param_named_action_does_not_clash_with_the_action(wire: Wire) -> None:
    result = await make().run({"action": "search_consent", "patient": "e1", "action_": "disclose"})
    assert result.success, result.message
    assert wire.last.url.params["action"] == "disclose"


@pytest.mark.parametrize(
    ("payload", "text"),
    [
        ({"action": "search_condition"}, "needs one of: patient, subject"),
        ({"action": "search_condition", "code": "x"}, "needs one of: patient, subject"),
        ({"action": "search_observation", "patient": "e1"}, "needs one of: category, code"),
        ({"action": "search_location"}, "give at least one"),
        ({"action": "search_condition", "patient": "e1", "unknown": "x"}, "unknown"),
        ({"action": "read_observation", "resource_id": "a/b"}, "resource_id"),
        (
            {"action": "read_observation", "resource_id": "a", "base_url": "https://evil.test"},
            "base_url",
        ),
    ],
)
async def test_bad_search_input_is_refused_before_any_call(
    wire: Wire, payload: dict, text: str
) -> None:
    result = await make().run(payload)
    assert not result.success
    assert text in (result.message or "") + json.dumps(result.details, default=str)
    assert not wire.requests


async def test_an_id_search_needs_no_patient(wire: Wire) -> None:
    assert (await make().run({"action": "search_condition", "id": "c1"})).success
    assert (await make().run({"action": "search_location", "name": "Clinic"})).success


async def test_a_bundle_gives_resources_total_and_the_next_link(wire: Wire) -> None:
    wire.json = {
        "resourceType": "Bundle",
        "total": 7,
        "link": [
            {"relation": "self", "url": "x"},
            {"relation": "next", "url": f"{BASE}/Condition?page=2"},
        ],
        "entry": [{"resource": {"resourceType": "Condition", "id": "c1"}}],
    }
    result = await make().run({"action": "search_condition", "patient": "e1"})
    assert result.data["resources"] == [{"resourceType": "Condition", "id": "c1"}]
    assert (result.data["total"], result.data["next_url"]) == (7, f"{BASE}/Condition?page=2")


# ---- writes ---------------------------------------------------------------------------------------
async def test_create_sends_the_resource_and_returns_the_new_id(wire: Wire) -> None:
    wire.status = 201
    wire.headers = {"Location": f"{BASE}/Condition/new-9/_history/1"}
    wire.json = None
    result = await make().run({"action": "create_condition", "resource": {"code": {"text": "x"}}})
    assert result.success, result.message
    assert (wire.last.method, wire.last.url.path) == ("POST", "/api/FHIR/R4/Condition")
    assert wire.body == {"code": {"text": "x"}, "resourceType": "Condition"}
    assert wire.last.headers["Content-Type"] == "application/fhir+json"
    assert result.data["resource_id"] == "new-9"


async def test_update_puts_to_the_id_and_sets_the_body_id(wire: Wire) -> None:
    wire.json = {"resourceType": "Task", "id": "t1"}
    result = await make().run(
        {"action": "update_task", "resource_id": "t1", "resource": {"status": "completed"}}
    )
    assert result.success, result.message
    assert (wire.last.method, wire.last.url.path) == ("PUT", "/api/FHIR/R4/Task/t1")
    assert wire.body == {"status": "completed", "resourceType": "Task", "id": "t1"}
    assert result.data["resource_id"] == "t1"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "create_condition", "resource": {"resourceType": "Observation"}},
        {"action": "update_task", "resource_id": "t1", "resource": {"id": "t2"}},
    ],
)
async def test_a_write_body_must_match_its_type_and_id(wire: Wire, payload: dict) -> None:
    result = await make().run(payload)
    assert not result.success and not wire.requests


async def test_a_create_without_an_id_in_the_answer_is_an_error(wire: Wire) -> None:
    wire.status = 201
    wire.json = None
    result = await make().run({"action": "create_condition", "resource": {}})
    assert not result.success and "resource ID" in (result.message or "")


# ---- errors ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "code", "category"),
    [
        (429, "FHIR_RATE_LIMITED", "RETRYABLE"),
        (401, "FHIR_AUTH_ERROR", "AUTH"),
        (403, "FHIR_AUTH_ERROR", "AUTH"),
        (404, "FHIR_HTTP_ERROR", "BUSINESS"),
        (422, "FHIR_HTTP_ERROR", "BUSINESS"),
        (500, "FHIR_HTTP_ERROR", "BUSINESS"),
    ],
)
async def test_status_decides_the_category(
    wire: Wire, status: int, code: str, category: str
) -> None:
    wire.status = status
    result = await make().run({"action": "read_observation", "resource_id": "o1"})
    assert (result.error_code, result.error_category) == (code, category)


@pytest.mark.parametrize("status", [502, 503, 504])
@pytest.mark.parametrize(
    ("payload", "retryable"),
    [
        ({"action": "read_observation", "resource_id": "o1"}, True),
        ({"action": "bulk_export_delete", "status_url": f"{BASE}/../BulkRequest/b1"}, True),
        ({"action": "create_condition", "resource": {}}, False),
    ],
)
async def test_gateway_errors_retry_only_safe_methods(
    wire: Wire, status: int, payload: dict[str, Any], retryable: bool
) -> None:
    wire.status = status
    result = await make().run(payload)
    expected = ("FHIR_UNAVAILABLE", "RETRYABLE") if retryable else ("FHIR_HTTP_ERROR", "BUSINESS")
    assert (result.error_code, result.error_category) == expected


async def test_the_error_carries_epics_operation_outcome(wire: Wire) -> None:
    wire.status = 400
    wire.json = {
        "resourceType": "OperationOutcome",
        "issue": [{"diagnostics": "Valid encounter required"}, {"diagnostics": "second"}],
    }
    result = await make().run({"action": "create_condition", "resource": {}})
    assert "Valid encounter required | second" in (result.message or "")


async def test_an_error_body_is_never_logged(wire: Wire, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    wire.status = 400
    wire.json = {"resourceType": "OperationOutcome", "issue": [{"diagnostics": PHI}]}
    await make().run({"action": "search_condition", "patient": PHI})
    assert PHI not in caplog.text
    assert any(getattr(r, "connector_id", None) == "fhir_epic" for r in caplog.records)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ReadTimeout("slow"), "FHIR_TIMEOUT"),
        (httpx.ConnectError("refused"), "FHIR_CONNECT_ERROR"),
    ],
)
async def test_transport_errors_are_retryable(wire: Wire, error: Exception, code: str) -> None:
    wire.error = error
    result = await make().run({"action": "read_observation", "resource_id": "o1"})
    assert (result.error_code, result.error_category) == (code, "RETRYABLE")


@pytest.mark.parametrize(
    ("payload", "error", "code", "category"),
    [
        # a create that timed out after it was sent may have stored the record: never repeat it
        (
            {"action": "create_condition", "resource": {}},
            httpx.ReadTimeout("s"),
            "FHIR_TIMEOUT_OUTCOME_UNKNOWN",
            "FATAL",
        ),
        (
            {"action": "create_condition", "resource": {}},
            httpx.WriteTimeout("s"),
            "FHIR_TIMEOUT_OUTCOME_UNKNOWN",
            "FATAL",
        ),
        # but not when nothing was sent
        (
            {"action": "create_condition", "resource": {}},
            httpx.ConnectTimeout("s"),
            "FHIR_TIMEOUT",
            "RETRYABLE",
        ),
        (
            {"action": "create_condition", "resource": {}},
            httpx.PoolTimeout("s"),
            "FHIR_TIMEOUT",
            "RETRYABLE",
        ),
        # an update writes the same state again, and a read changes nothing
        (
            {"action": "update_task", "resource_id": "t", "resource": {}},
            httpx.ReadTimeout("s"),
            "FHIR_TIMEOUT",
            "RETRYABLE",
        ),
        (
            {"action": "search_condition", "patient": "e"},
            httpx.ReadTimeout("s"),
            "FHIR_TIMEOUT",
            "RETRYABLE",
        ),
        # a kick-off counts against Epic's one request per group per day
        (
            {"action": "bulk_export_group", "resource_id": "g"},
            httpx.ReadTimeout("s"),
            "FHIR_TIMEOUT_OUTCOME_UNKNOWN",
            "FATAL",
        ),
    ],
)
async def test_only_a_safe_timeout_is_retryable(
    wire: Wire, payload: dict[str, Any], error: Exception, code: str, category: str
) -> None:
    wire.error = error
    result = await make().run(payload)
    assert (result.error_code, result.error_category) == (code, category)


# ---- operations -----------------------------------------------------------------------------------
async def test_fetch_next_page_follows_a_link_on_the_epic_server(wire: Wire) -> None:
    wire.json = {"resourceType": "Bundle", "entry": [{"resource": {"id": "c2"}}]}
    result = await make().run({"action": "fetch_next_page", "url": f"{BASE}/Condition?page=2"})
    assert result.success, result.message
    assert wire.last.url.params["page"] == "2"
    assert result.data["resources"] == [{"id": "c2"}]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/api/FHIR/R4/Condition?page=2",
        "http://epic.test/api/FHIR/R4/Condition",
        "https://epic.test:8443/api/FHIR/R4/Condition",
        "https://epic.test.evil.test/x",
    ],
)
@pytest.mark.parametrize(
    "action", ["fetch_next_page", "bulk_export_file", "bulk_export_status", "bulk_export_delete"]
)
async def test_a_link_on_another_server_is_refused_so_the_token_never_leaves(
    wire: Wire, action: str, url: str
) -> None:
    key = {"fetch_next_page": "url", "bulk_export_file": "file_url"}.get(action, "status_url")
    result = await make().run({"action": action, key: url})
    assert not result.success and not wire.requests
    assert "configured Epic server" in (result.message or "")


async def test_patient_match_posts_a_parameters_resource(wire: Wire) -> None:
    result = await make().run(
        {
            "action": "patient_match",
            "patient": {"resourceType": "Patient", "birthDate": "1980-01-01"},
            "only_certain_matches": True,
            "count": 3,
        }
    )
    assert result.success, result.message
    assert (wire.last.method, wire.last.url.path) == ("POST", "/api/FHIR/R4/Patient/$match")
    assert wire.body == {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "resource",
                "resource": {"resourceType": "Patient", "birthDate": "1980-01-01"},
            },
            {"name": "onlyCertainMatches", "valueBoolean": True},
            {"name": "count", "valueInteger": 3},
        ],
    }


async def test_patient_match_timeout_is_retryable_because_it_only_reads(wire: Wire) -> None:
    wire.error = httpx.ReadTimeout("slow")
    result = await make().run({"action": "patient_match", "patient": {"resourceType": "Patient"}})
    assert (result.error_code, result.error_category) == ("FHIR_TIMEOUT", "RETRYABLE")


async def test_patient_summary_and_value_set_expand(wire: Wire) -> None:
    wire.json = {"resourceType": "Bundle", "type": "document"}
    assert (await make().run({"action": "patient_summary", "resource_id": "e1"})).success
    assert wire.last.url.path == "/api/FHIR/R4/Patient/e1/$summary"
    wire.json = {"resourceType": "ValueSet"}
    assert (
        await make().run({"action": "value_set_expand", "url": "http://x/vs", "filter": "ab"})
    ).success
    assert wire.last.url.path == "/api/FHIR/R4/ValueSet/$expand"
    assert dict(wire.last.url.params) == {"url": "http://x/vs", "filter": "ab"}


async def test_bulk_export_group_kicks_off_and_returns_the_status_url(wire: Wire) -> None:
    wire.status = 202
    wire.json = None
    wire.headers = {"Content-Location": "https://epic.test/api/FHIR/BulkRequest/B1"}
    result = await make().run(
        {
            "action": "bulk_export_group",
            "resource_id": "g1",
            "type": "Patient,Condition",
            "include_associated_data": "LatestProvenanceResources",
            "type_filter": "Condition?category=problem-list-item",
        }
    )
    assert result.success, result.message
    assert wire.last.url.path == "/api/FHIR/R4/Group/g1/$export"
    assert wire.last.headers["Prefer"] == "respond-async"
    assert dict(wire.last.url.params) == {
        "_type": "Patient,Condition",
        "includeAssociatedData": "LatestProvenanceResources",
        "_typeFilter": "Condition?category=problem-list-item",
    }
    assert result.data["status_url"] == "https://epic.test/api/FHIR/BulkRequest/B1"


async def test_bulk_type_filter_needs_a_type(wire: Wire) -> None:
    result = await make().run(
        {"action": "bulk_export_group", "resource_id": "g1", "type_filter": "Condition?x=y"}
    )
    assert not result.success and not wire.requests


async def test_bulk_status_reports_progress_then_the_manifest(wire: Wire) -> None:
    url = "https://epic.test/api/FHIR/BulkRequest/B1"
    wire.status = 202
    wire.content = b""
    wire.headers = {"X-Progress": "Processing - patient data, 15% complete."}
    running = await make().run({"action": "bulk_export_status", "status_url": url})
    assert running.data["status_code"] == 202
    assert running.data["progress"] == "Processing - patient data, 15% complete."
    assert running.data["resource"] is None
    wire.status = 200
    wire.content = None
    wire.headers = {}
    wire.json = {"output": [{"type": "Patient", "url": "https://epic.test/f/1"}]}
    done = await make().run({"action": "bulk_export_status", "status_url": url})
    assert done.data["resource"]["output"][0]["url"] == "https://epic.test/f/1"


async def test_bulk_file_is_ndjson_text_with_a_size_cap(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire.content = b'{"id":"1"}\n{"id":"2"}\n'
    result = await make().run({"action": "bulk_export_file", "file_url": "https://epic.test/f/1"})
    assert result.data["content"] == '{"id":"1"}\n{"id":"2"}\n'
    assert wire.last.headers["Accept"] == "application/fhir+ndjson"
    monkeypatch.setattr(logic, "_MAX_BULK_FILE_BYTES", 5)
    result = await make().run({"action": "bulk_export_file", "file_url": "https://epic.test/f/1"})
    assert not result.success and "larger than" in (result.message or "")


async def test_bulk_delete(wire: Wire) -> None:
    wire.status = 202
    wire.content = b""
    result = await make().run(
        {"action": "bulk_export_delete", "status_url": "https://epic.test/api/FHIR/BulkRequest/B1"}
    )
    assert wire.last.method == "DELETE" and result.data["status_code"] == 202


# ---- configuration --------------------------------------------------------------------------------
async def test_the_base_url_comes_from_the_secret_and_loses_its_trailing_slash(wire: Wire) -> None:
    await make().run({"action": "read_observation", "resource_id": "o1"})
    assert str(wire.last.url) == f"{BASE}/Observation/o1"


def test_calls_share_the_runtimes_pooled_client() -> None:
    from node_wire_runtime import rest

    assert logic._shared_client is rest._shared_client
