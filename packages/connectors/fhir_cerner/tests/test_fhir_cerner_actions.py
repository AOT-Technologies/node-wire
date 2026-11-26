# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
"""Run every action against a mock HTTP transport, so no Cerner server is needed (CI has none)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from node_wire_fhir_cerner import logic
from node_wire_fhir_cerner.logic import FhirCernerConnector
from node_wire_runtime.auth import StaticTokenAuthProvider

BASE = "https://cerner.test/r4/tenant-1"
PHI = "PHI-MARKER-DO-NOT-LOG"

# The reads, searches and writes are named <verb>_<snake_case type>.
READS = (
    "account allergy_intolerance appointment binary care_plan care_team charge_item communication condition "
    "consent coverage device diagnostic_report document_reference encounter family_member_history goal "
    "immunization insurance_plan location media medication_administration medication_dispense "
    "medication_request nutrition_order observation operation_definition organization patient person "
    "practitioner procedure provenance questionnaire questionnaire_response related_person schedule "
    "service_request slot specimen structure_definition"
).split()
NO_SEARCH = {"binary", "media", "operation_definition", "structure_definition"}
CREATES = (
    "allergy_intolerance appointment communication condition coverage diagnostic_report document_reference "
    "encounter family_member_history immunization medication_request observation organization patient "
    "practitioner procedure provenance related_person"
).split()
UPDATES = (
    "allergy_intolerance condition document_reference family_member_history immunization observation "
    "questionnaire_response"
).split()
PATCHES = "appointment communication coverage encounter medication_request patient related_person slot".split()
DELETES = ["coverage"]
OPERATIONS = {
    "fetch_next_page",
    "get_capability_statement",
    "get_smart_configuration",
    "get_ccd",
    "docref",
    "get_caregiver_organizations",
    "health_cards_issue",
    "charge_item_create",
    "charge_item_credit",
    "charge_item_modify",
    "create_financial_transaction",
    "execute_batch",
    "bulk_export_group",
    "bulk_export_patients",
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
    def __init__(
        self,
        token_url: str = "https://auth.test/tenants/t/protocols/oauth2/profiles/smart-v1/token",
    ):
        self.values = {
            "cerner_fhir_base_url": BASE + "/",
            "cerner_token_url": token_url,
            "CERNER_TEST_TOKEN": "tok-123",
        }

    def get_secret(self, key: str) -> str:
        return self.values[key]


class Wire:
    """What the mock Cerner saw, and what it answers next."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.json: Any = {"resourceType": "Bundle", "entry": []}
        self.content: bytes | None = None
        self.headers: dict[str, str] = {}
        self.error: Exception | None = None
        self.queue: list[tuple[int, dict[str, str], bytes]] = []  # answers for the next requests

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.queue:
            status, headers, content = self.queue.pop(0)
            return httpx.Response(status, content=content, headers=headers)
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


def make(secrets: Secrets | None = None) -> FhirCernerConnector:
    secrets = secrets or Secrets()
    return FhirCernerConnector(
        secret_provider=secrets,  # type: ignore[arg-type]
        auth_provider=StaticTokenAuthProvider(
            secret_provider=secrets,  # type: ignore[arg-type]
            secret_key="CERNER_TEST_TOKEN",
        ),
    )


def test_every_cerner_action_is_registered() -> None:
    # read_patient, search_encounter and the document_reference pair are among the original five, and
    # search_patients replaces the generated search_patient
    expected = (
        {f"read_{t}" for t in READS}
        | {f"search_{t}" for t in READS if t not in NO_SEARCH and t != "patient"}
        | {f"create_{t}" for t in CREATES}
        | {f"update_{t}" for t in UPDATES}
        | {f"patch_{t}" for t in PATCHES}
        | {f"delete_{t}" for t in DELETES}
        | OPERATIONS
        | ORIGINAL
    )
    assert set(FhirCernerConnector.sdk_action_metas()) == expected
    assert len(expected) == 129


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
    if verb == "update":
        return {"resource_id": "r1", "if_match": "3", "resource": {"resourceType": rtype}}
    if verb == "patch":
        return {
            "resource_id": "r1",
            "if_match": "3",
            "operations": [{"op": "replace", "path": "/x", "value": 1}],
        }
    return {"resource_id": "r1", "if_match": "3"}


GENERATED = sorted(
    {f"read_{t}" for t in READS if t != "patient"}
    | {
        f"search_{t}"
        for t in READS
        if t not in NO_SEARCH and t not in {"patient", "encounter", "document_reference"}
    }
    | {f"create_{t}" for t in CREATES if t != "document_reference"}
    | {f"update_{t}" for t in UPDATES}
    | {f"patch_{t}" for t in PATCHES}
    | {f"delete_{t}" for t in DELETES}
)


@pytest.mark.parametrize("action", GENERATED)
async def test_action_sends_the_declared_request(wire: Wire, action: str) -> None:
    wire.json = {"resourceType": "Bundle", "entry": []}
    wire.headers = {"Location": f"{BASE}/X/new-1/_history/1"}
    meta = FhirCernerConnector.sdk_action_metas()[action]
    result = await make().run({"action": action, **_minimal(action)})
    assert result.success, result.message
    request = wire.last
    assert request.method == meta.http_method
    assert request.url.path == "/r4/tenant-1" + (meta.http_path or "").replace("{id}", "r1")
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert request.headers["Accept"] == "application/fhir+json"
    if action.startswith("search_"):
        assert dict(request.url.params) == {"_id": "r1"}
    if action.split("_", 1)[0] in ("update", "patch", "delete"):
        assert request.headers["If-Match"] == 'W/"3"'


# ---- search inputs --------------------------------------------------------------------------------
async def test_cerner_no_results_notice_is_not_a_result(wire: Wire) -> None:
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
            "action": "search_location",
            "physical_type": "bu",
            "address_city": "Kansas City",
        }
    )
    assert result.success, result.message
    params = wire.last.url.params
    assert params["-physicalType"] == "bu" and params["address-city"] == "Kansas City"
    assert (await make().run({"action": "search_observation", "patient": "p1", "count": 5})).success
    assert wire.last.url.params["_count"] == "5"
    # Location has no _count in Cerner's capability statement, so it has no count field
    assert not (await make().run({"action": "search_location", "name": "x", "count": 5})).success
    result = await make().run(
        {"action": "search_appointment", "patient": "p1", "date_or_req_period": "ge2024-01-01"}
    )
    assert result.success, result.message
    assert wire.last.url.params["-date-or-req-period"] == "ge2024-01-01"


async def test_include_goes_out_as_a_repeated_parameter(wire: Wire) -> None:
    result = await make().run(
        {
            "action": "search_consent",
            "patient": "p1",
            "include": ["Consent:actor", "Consent:patient"],
        }
    )
    assert result.success, result.message
    assert wire.last.url.params.get_list("_include") == ["Consent:actor", "Consent:patient"]


@pytest.mark.parametrize(
    ("payload", "text"),
    [
        ({"action": "search_condition"}, "needs one of: id, patient, subject"),
        ({"action": "search_condition", "code": "x"}, "not permitted"),
        (
            {"action": "search_condition", "clinical_status": "active"},
            "needs one of: id, patient, subject",
        ),
        (
            {"action": "search_observation", "category": "laboratory"},
            "needs one of: id, patient, subject",
        ),
        ({"action": "search_slot", "start": "ge2024-01-01"}, "needs one of: id, service_type"),
        ({"action": "search_location"}, "give at least one"),
        ({"action": "search_condition", "patient": "e1", "unknown": "x"}, "not permitted"),
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


async def test_a_read_returns_the_etag_to_use_as_if_match(wire: Wire) -> None:
    wire.json = {"resourceType": "Condition", "id": "c1"}
    wire.headers = {"ETag": 'W/"4"'}
    result = await make().run({"action": "read_condition", "resource_id": "c1"})
    assert result.data["etag"] == 'W/"4"'


def test_a_numeric_id_is_an_id_not_a_crash() -> None:
    # the playground sends 12765407, not "12765407"; the normalizer used to fail on it with a server error
    from node_wire_fhir_cerner import normalizers

    read = {"resource_id": 12765407}
    normalizers.normalize_fhir_read_patient(read)
    assert read["resource_id"] == "12765407"
    encounter = {"patient": 12765407}
    normalizers.normalize_fhir_search_encounter(encounter)
    assert encounter["patient_id"] == "12765407"
    patients = {"resource_ids": [12765407, "2"]}
    normalizers.normalize_fhir_search_patients(patients)
    assert patients["resource_ids"] == ["12765407", "2"]


async def test_search_patients_by_id_runs_at_most_five_requests_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return httpx.Response(
            200, json={"resourceType": "Patient", "id": request.url.path.split("/")[-1]}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(logic, "_shared_client", lambda trust_env: client)
    ids = [str(i) for i in range(50)]
    result = await make().run({"action": "search_patients", "resource_ids": ids})
    assert result.success and result.data["total"] == 50 and result.data["errors"] == []
    assert 1 < peak <= 5


async def test_search_patients_refuses_more_than_fifty_ids(wire: Wire) -> None:
    result = await make().run(
        {"action": "search_patients", "resource_ids": [str(i) for i in range(51)]}
    )
    assert not result.success and not wire.requests


# ---- writes ---------------------------------------------------------------------------------------
async def test_create_sends_the_resource_and_returns_the_new_id(wire: Wire) -> None:
    wire.status = 201
    wire.headers = {"Location": f"{BASE}/Condition/new-9/_history/1", "ETag": 'W/"1"'}
    wire.json = None
    result = await make().run({"action": "create_condition", "resource": {"code": {"text": "x"}}})
    assert result.success, result.message
    assert (wire.last.method, wire.last.url.path) == ("POST", "/r4/tenant-1/Condition")
    assert wire.body == {"code": {"text": "x"}, "resourceType": "Condition"}
    assert wire.last.headers["Content-Type"] == "application/fhir+json"
    assert result.data["resource_id"] == "new-9" and result.data["etag"] == 'W/"1"'


async def test_update_puts_to_the_id_with_the_version(wire: Wire) -> None:
    wire.json = {"resourceType": "Observation", "id": "o1"}
    wire.headers = {"ETag": 'W/"8"'}
    result = await make().run(
        {
            "action": "update_observation",
            "resource_id": "o1",
            "if_match": 'W/"7"',
            "resource": {"status": "final"},
        }
    )
    assert result.success, result.message
    assert (wire.last.method, wire.last.url.path) == ("PUT", "/r4/tenant-1/Observation/o1")
    assert wire.last.headers["If-Match"] == 'W/"7"'
    assert wire.body == {"status": "final", "resourceType": "Observation", "id": "o1"}
    assert result.data["resource_id"] == "o1" and result.data["etag"] == 'W/"8"'


async def test_patch_sends_json_patch_with_the_version(wire: Wire) -> None:
    wire.content = b""
    wire.headers = {"ETag": 'W/"11"'}
    ops = [{"op": "replace", "path": "/period/end", "value": "2024-01-01T00:00:00.000Z"}]
    result = await make().run(
        {"action": "patch_encounter", "resource_id": "e1", "if_match": "10", "operations": ops}
    )
    assert result.success, result.message
    assert (wire.last.method, wire.last.url.path) == ("PATCH", "/r4/tenant-1/Encounter/e1")
    assert wire.last.headers["Content-Type"] == "application/json-patch+json"
    assert wire.last.headers["If-Match"] == 'W/"10"'
    assert wire.body == ops
    assert result.data["etag"] == 'W/"11"'


async def test_delete_coverage_needs_the_version(wire: Wire) -> None:
    wire.content = b""
    result = await make().run({"action": "delete_coverage", "resource_id": "c1", "if_match": "2"})
    assert result.success, result.message
    assert (wire.last.method, wire.last.url.path) == ("DELETE", "/r4/tenant-1/Coverage/c1")
    assert wire.last.headers["If-Match"] == 'W/"2"'
    assert (await make().run({"action": "delete_coverage", "resource_id": "c1"})).success is False


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "create_condition", "resource": {"resourceType": "Observation"}},
        {
            "action": "update_observation",
            "resource_id": "o1",
            "if_match": "1",
            "resource": {"id": "o2"},
        },
        {"action": "update_observation", "resource_id": "o1", "resource": {}},
        {"action": "patch_encounter", "resource_id": "e1", "if_match": "1", "operations": []},
        {
            "action": "patch_encounter",
            "resource_id": "e1",
            "if_match": "1",
            "operations": [{"op": "oops"}],
        },
    ],
)
async def test_a_write_must_be_well_formed(wire: Wire, payload: dict) -> None:
    result = await make().run(payload)
    assert not result.success and not wire.requests


async def test_a_create_reads_the_id_from_a_chunked_answer(wire: Wire) -> None:
    # No Location header and no content-length: the body is the only place the id is.
    async def body() -> AsyncIterator[bytes]:
        yield b'{"resourceType": "Condition", "id": "c9"}'

    wire.status = 201
    wire.content = body()  # type: ignore[assignment]
    result = await make().run({"action": "create_condition", "resource": {}})
    assert result.success and result.data["resource_id"] == "c9"


async def test_a_create_without_an_id_in_the_answer_is_an_error(wire: Wire) -> None:
    wire.status = 201
    wire.json = None
    result = await make().run({"action": "create_condition", "resource": {}})
    assert not result.success and "resource ID" in (result.message or "")


# ---- the original create_document_reference keeps Cerner's rules -----------------------------------
def _note(**over: Any) -> dict[str, Any]:
    note = {
        "action": "create_document_reference",
        "status": "current",
        "subject": "Patient/p1",
        "attachment_title": "Follow-up",
        "text": "hello",
        "type": {
            "coding": [
                {
                    "system": "https://fhir.cerner.com/t/codeSet/72",
                    "code": "2820557",
                    "display": "Consult",
                }
            ]
        },
        "author": [{"reference": "Practitioner/9"}],
    }
    note.update(over)
    return note


async def test_a_clinical_note_gets_the_fields_cerner_requires(wire: Wire) -> None:
    wire.status = 201
    wire.headers = {"Location": f"{BASE}/DocumentReference/d1"}
    wire.json = None
    result = await make().run(_note(context={"encounter": [{"reference": "Encounter/e1"}]}))
    assert result.success, result.message
    body = wire.body
    attachment = body["content"][0]["attachment"]
    assert (
        attachment["contentType"] == "text/plain;charset=utf-8" and attachment["data"] == "aGVsbG8="
    )
    assert body["docStatus"] == "final" and body["authenticator"] == {"reference": "Practitioner/9"}
    assert body["type"]["text"] == "Consult" and body["type"]["coding"][0]["userSelected"] is True
    assert body["context"]["period"]["start"].endswith(".000Z")
    assert "identifier" not in body and "text" not in body


@pytest.mark.parametrize(
    ("change", "text"),
    [
        (
            {"type": {"coding": [{"system": "http://loinc.org", "code": "1", "display": "x"}]}},
            "CodeSet 72",
        ),
        ({"attachment_title": None}, "attachment_title"),
        ({"author": None}, "author"),
        ({"text": None}, "'text' or 'data'"),
    ],
)
async def test_a_clinical_note_that_breaks_a_cerner_rule_is_refused(
    wire: Wire, change: dict[str, Any], text: str
) -> None:
    result = await make().run(_note(**change))
    assert not result.success and text in (result.message or "")
    assert not wire.requests


# ---- errors ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "code", "category"),
    [
        (429, "CERNER_RATE_LIMITED", "RETRYABLE"),
        (401, "CERNER_AUTH_ERROR", "AUTH"),
        (403, "CERNER_AUTH_ERROR", "AUTH"),
        (404, "CERNER_HTTP_ERROR", "BUSINESS"),
        (422, "CERNER_HTTP_ERROR", "BUSINESS"),
        (500, "CERNER_SERVER_ERROR", "FATAL"),
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
        ({"action": "bulk_export_delete", "status_url": f"{BASE}/bulk-export/jobs/b1"}, True),
        ({"action": "create_condition", "resource": {}}, False),
    ],
)
async def test_gateway_errors_retry_only_safe_methods(
    wire: Wire, status: int, payload: dict[str, Any], retryable: bool
) -> None:
    wire.status = status
    result = await make().run(payload)
    expected = (
        ("CERNER_UNAVAILABLE", "RETRYABLE") if retryable else ("CERNER_SERVER_ERROR", "FATAL")
    )
    assert (result.error_code, result.error_category) == expected


async def test_the_error_carries_cerners_operation_outcome(wire: Wire) -> None:
    wire.status = 422
    wire.json = {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "processing",
                "details": {"text": "A character set must be specified"},
            },
            {"severity": "error", "code": "invalid", "diagnostics": "second"},
        ],
    }
    result = await make().run({"action": "create_condition", "resource": {}})
    assert "error processing A character set must be specified | error invalid second" in (
        result.message or ""
    )


async def test_an_error_body_is_never_logged(wire: Wire, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    wire.status = 400
    wire.json = {"resourceType": "OperationOutcome", "issue": [{"diagnostics": PHI}]}
    await make().run({"action": "search_condition", "patient": PHI})
    assert PHI not in caplog.text
    assert any(getattr(r, "connector_id", None) == "fhir_cerner" for r in caplog.records)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ReadTimeout("slow"), "CERNER_TIMEOUT"),
        (httpx.ConnectError("refused"), "CERNER_CONNECT_ERROR"),
    ],
)
async def test_transport_errors_are_retryable(wire: Wire, error: Exception, code: str) -> None:
    wire.error = error
    result = await make().run({"action": "read_observation", "resource_id": "o1"})
    assert (result.error_code, result.error_category) == (code, "RETRYABLE")


@pytest.mark.parametrize(
    ("payload", "error", "code", "category"),
    [
        # a write that timed out after it was sent may have stored the record: never repeat it
        (
            _minimal("create_condition") | {"action": "create_condition"},
            httpx.ReadTimeout("s"),
            "CERNER_TIMEOUT_OUTCOME_UNKNOWN",
            "FATAL",
        ),
        (
            _minimal("patch_encounter") | {"action": "patch_encounter"},
            httpx.ReadTimeout("s"),
            "CERNER_TIMEOUT_OUTCOME_UNKNOWN",
            "FATAL",
        ),
        # but not when nothing was sent
        (
            _minimal("create_condition") | {"action": "create_condition"},
            httpx.ConnectTimeout("s"),
            "CERNER_TIMEOUT",
            "RETRYABLE",
        ),
        # an update writes the same state again, a delete is the same delete, and a read changes nothing
        (
            _minimal("update_observation") | {"action": "update_observation"},
            httpx.ReadTimeout("s"),
            "CERNER_TIMEOUT",
            "RETRYABLE",
        ),
        (
            _minimal("delete_coverage") | {"action": "delete_coverage"},
            httpx.ReadTimeout("s"),
            "CERNER_TIMEOUT",
            "RETRYABLE",
        ),
        (
            {"action": "search_condition", "patient": "e"},
            httpx.ReadTimeout("s"),
            "CERNER_TIMEOUT",
            "RETRYABLE",
        ),
        # a kick-off counts against Cerner's one export at a time
        (
            {"action": "bulk_export_group", "resource_id": "g"},
            httpx.ReadTimeout("s"),
            "CERNER_TIMEOUT_OUTCOME_UNKNOWN",
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


async def test_a_token_url_with_hosts_is_refused(wire: Wire) -> None:
    secrets = Secrets(token_url="https://authorization.cerner.com/hosts/x/token")
    result = await make(secrets).run({"action": "read_observation", "resource_id": "o1"})
    assert not result.success and "/hosts/" in (result.message or "")
    assert not wire.requests


# ---- operations -----------------------------------------------------------------------------------
async def test_fetch_next_page_follows_a_link_on_the_cerner_server(wire: Wire) -> None:
    wire.json = {"resourceType": "Bundle", "entry": [{"resource": {"id": "c2"}}]}
    result = await make().run({"action": "fetch_next_page", "url": f"{BASE}/Condition?page=2"})
    assert result.success, result.message
    assert wire.last.url.params["page"] == "2"
    assert result.data["resources"] == [{"id": "c2"}]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/r4/tenant-1/Condition?page=2",
        "http://cerner.test/r4/tenant-1/Condition",
        "https://cerner.test:8443/r4/tenant-1/Condition",
        "https://cerner.test.evil.test/x",
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
    assert "configured Cerner server" in (result.message or "")


async def test_conformance_reads(wire: Wire) -> None:
    wire.json = {"resourceType": "CapabilityStatement"}
    result = await make().run({"action": "get_capability_statement"})
    assert result.success and wire.last.url.path == "/r4/tenant-1/metadata"
    wire.json = {"token_endpoint": "https://auth.test/token"}
    result = await make().run({"action": "get_smart_configuration"})
    assert wire.last.url.path == "/r4/tenant-1/.well-known/smart-configuration"
    assert result.data["resource"]["token_endpoint"] == "https://auth.test/token"


async def test_get_ccd_as_json_or_xml_and_docref(wire: Wire) -> None:
    wire.json = {"resourceType": "Binary", "contentType": "application/xml", "data": "PGNjZC8+"}
    result = await make().run(
        {"action": "get_ccd", "patient": "p1", "start": "2024-01-01T00:00:00.000Z"}
    )
    assert wire.last.url.path == "/r4/tenant-1/Binary/$autogen-ccd-if"
    assert dict(wire.last.url.params) == {"patient": "p1", "start": "2024-01-01T00:00:00.000Z"}
    assert result.data["resource"]["data"] == "PGNjZC8+"
    wire.content = b"<ClinicalDocument/>"
    result = await make().run({"action": "get_ccd", "patient": "p1", "xml": True})
    assert wire.last.headers["Accept"] == "application/xml"
    assert result.data["content"] == "<ClinicalDocument/>"
    wire.content = None
    wire.json = {"resourceType": "Bundle", "entry": [{"resource": {"id": "d1"}}]}
    result = await make().run({"action": "docref", "patient": "p1"})
    assert wire.last.url.path == "/r4/tenant-1/DocumentReference/$docref"
    assert result.data["resources"] == [{"id": "d1"}]


async def test_caregiver_organizations_send_the_id_as_underscore_id(wire: Wire) -> None:
    assert (
        await make().run({"action": "get_caregiver_organizations", "resource_id": "3304067"})
    ).success
    assert wire.last.url.path == "/r4/tenant-1/Organization/$get-cg-for-mrcu"
    assert dict(wire.last.url.params) == {"_id": "3304067"}


async def test_operations_with_a_parameters_body(wire: Wire) -> None:
    wire.json = {"resourceType": "Parameters"}
    parameters = {"parameter": [{"name": "_since", "valueDateTime": "2024-01-01"}]}
    await make().run(
        {"action": "health_cards_issue", "resource_id": "p1", "parameters": parameters}
    )
    assert wire.last.url.path == "/r4/tenant-1/Patient/p1/$health-cards-issue"
    assert wire.body == {"parameter": parameters["parameter"], "resourceType": "Parameters"}
    await make().run(
        {"action": "charge_item_credit", "resource_id": "ci1", "parameters": parameters}
    )
    assert wire.last.url.path == "/r4/tenant-1/ChargeItem/ci1/$credit"
    for action, path in (("charge_item_create", "$create"), ("charge_item_modify", "$modify")):
        await make().run({"action": action, "parameters": parameters})
        assert (wire.last.method, wire.last.url.path) == ("POST", f"/r4/tenant-1/ChargeItem/{path}")
    result = await make().run(
        {"action": "charge_item_create", "parameters": {"resourceType": "Bundle"}}
    )
    assert not result.success


async def test_a_financial_transaction_is_a_basic_create(wire: Wire) -> None:
    wire.status = 201
    wire.headers = {"Location": f"{BASE}/Basic/b1"}
    wire.json = None
    result = await make().run({"action": "create_financial_transaction", "resource": {"code": {}}})
    assert result.success and result.data["resource_id"] == "b1"
    assert (wire.last.method, wire.last.url.path) == ("POST", "/r4/tenant-1/Basic")
    assert wire.body["resourceType"] == "Basic"


async def test_execute_batch_posts_the_bundle_to_the_root(wire: Wire) -> None:
    bundle = {"type": "batch", "entry": [{"request": {"method": "POST", "url": "Provenance"}}]}
    wire.json = {"resourceType": "Bundle", "type": "batch-response"}
    result = await make().run({"action": "execute_batch", "bundle": bundle})
    assert result.success, result.message
    assert (wire.last.method, str(wire.last.url)) == ("POST", BASE)
    assert wire.body["resourceType"] == "Bundle"
    assert not (
        await make().run({"action": "execute_batch", "bundle": {"type": "searchset"}})
    ).success


async def test_bulk_export_group_kicks_off_and_returns_the_status_url(wire: Wire) -> None:
    wire.status = 202
    wire.json = None
    wire.headers = {"Content-Location": f"{BASE}/bulk-export/jobs/J1"}
    result = await make().run(
        {
            "action": "bulk_export_group",
            "resource_id": "g1",
            "type": "Patient,Condition",
            "since": "2024-01-01T00:00:00.000Z",
            "include_associated_data": "LatestProvenanceResources",
            "type_filter": "Condition?category=problem-list-item",
            "lenient": True,
        }
    )
    assert result.success, result.message
    assert wire.last.url.path == "/r4/tenant-1/Group/g1/$export"
    assert wire.last.headers["Prefer"] == "respond-async, handling=lenient"
    assert dict(wire.last.url.params) == {
        "_type": "Patient,Condition",
        "_since": "2024-01-01T00:00:00.000Z",
        "includeAssociatedData": "LatestProvenanceResources",
        "_typeFilter": "Condition?category=problem-list-item",
    }
    assert result.data["status_url"] == f"{BASE}/bulk-export/jobs/J1"


async def test_bulk_export_patients_posts_the_patient_list(wire: Wire) -> None:
    wire.status = 202
    wire.json = None
    wire.headers = {"Content-Location": f"{BASE}/bulk-export/jobs/J2"}
    result = await make().run(
        {
            "action": "bulk_export_patients",
            "patients": ["1", "2"],
            "type": "Condition",
            "since": "2024-01-01T00:00:00.000Z",
        }
    )
    assert result.success, result.message
    assert (wire.last.method, wire.last.url.path) == ("POST", "/r4/tenant-1/Patient/$export")
    assert wire.last.headers["Prefer"] == "respond-async"
    assert wire.body["parameter"] == [
        {"name": "patient", "valueReference": {"reference": "Patient/1"}},
        {"name": "patient", "valueReference": {"reference": "Patient/2"}},
        {"name": "_type", "valueString": "Condition"},
        {"name": "_since", "valueInstant": "2024-01-01T00:00:00.000Z"},
    ]
    assert result.data["status_url"] == f"{BASE}/bulk-export/jobs/J2"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "bulk_export_group", "resource_id": "g1", "type_filter": "Condition?x=y"},
        {"action": "bulk_export_patients", "patients": []},
    ],
)
async def test_a_bulk_export_must_be_well_formed(wire: Wire, payload: dict) -> None:
    assert not (await make().run(payload)).success and not wire.requests


async def test_bulk_status_reports_progress_then_the_manifest(wire: Wire) -> None:
    url = f"{BASE}/bulk-export/jobs/J1"
    wire.status = 202
    wire.content = b""
    wire.headers = {"X-Progress": "in-progress"}
    running = await make().run({"action": "bulk_export_status", "status_url": url})
    assert running.data["status_code"] == 202 and running.data["progress"] == "in-progress"
    assert running.data["resource"] is None
    wire.status = 200
    wire.content = None
    wire.headers = {}
    wire.json = {"output": [{"type": "Patient", "url": f"{BASE}/bulk-export/files/F1"}]}
    done = await make().run({"action": "bulk_export_status", "status_url": url})
    assert done.data["resource"]["output"][0]["url"].endswith("/files/F1")


async def _resolves(url: str) -> None:
    """Stand-in for the runtime's DNS check: the test hosts do not exist."""


async def test_bulk_file_follows_the_redirect_without_the_token(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logic, "assert_safe_destination", _resolves)
    wire.queue = [
        (307, {"Location": "https://storage.test/f/1?sig=abc"}, b""),
        (200, {}, b'{"id":"1"}\n{"id":"2"}\n'),
    ]
    result = await make().run(
        {"action": "bulk_export_file", "file_url": f"{BASE}/bulk-export/files/F1"}
    )
    assert result.success, result.message
    first, second = wire.requests
    assert first.headers["Authorization"] == "Bearer tok-123"
    assert first.headers["Accept"] == "application/fhir+ndjson"
    assert str(second.url) == "https://storage.test/f/1?sig=abc"
    assert "Authorization" not in second.headers
    assert result.data["content"] == '{"id":"1"}\n{"id":"2"}\n'


@pytest.mark.parametrize(
    "link",
    [
        "https://127.0.0.1/f/1",
        "https://localhost/f/1",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/f/1",
        "https://[::1]/f/1",
    ],
)
async def test_bulk_file_refuses_a_download_link_to_an_internal_host(wire: Wire, link: str) -> None:
    wire.queue = [(307, {"Location": link}, b"")]
    result = await make().run(
        {"action": "bulk_export_file", "file_url": f"{BASE}/bulk-export/files/F1"}
    )
    assert not result.success and len(wire.requests) == 1


async def test_bulk_file_refuses_a_download_link_that_is_not_https(wire: Wire) -> None:
    wire.queue = [(307, {"Location": "http://storage.test/f/1"}, b"")]
    result = await make().run(
        {"action": "bulk_export_file", "file_url": f"{BASE}/bulk-export/files/F1"}
    )
    assert not result.success and "not https" in (result.message or "")
    assert len(wire.requests) == 1


async def test_bulk_file_has_a_size_cap(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logic, "_MAX_BULK_FILE_BYTES", 5)
    wire.content = b'{"id":"1"}\n{"id":"2"}\n'
    result = await make().run(
        {"action": "bulk_export_file", "file_url": f"{BASE}/bulk-export/files/F1"}
    )
    assert not result.success and "larger than" in (result.message or "")


async def test_bulk_file_over_the_cap_is_refused_before_it_is_all_read(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logic, "_MAX_BULK_FILE_BYTES", 10)
    read: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        for i in range(100):
            read.append(i)
            yield b"x" * 4

    wire.content = chunks()  # type: ignore[assignment]
    result = await make().run(
        {"action": "bulk_export_file", "file_url": f"{BASE}/bulk-export/files/F1"}
    )
    assert not result.success and "larger than" in (result.message or "")
    assert len(read) < 100


async def test_bulk_delete(wire: Wire) -> None:
    wire.status = 202
    wire.content = b""
    result = await make().run(
        {"action": "bulk_export_delete", "status_url": f"{BASE}/bulk-export/jobs/J1"}
    )
    assert wire.last.method == "DELETE" and result.data["status_code"] == 202


# ---- configuration --------------------------------------------------------------------------------
async def test_the_base_url_comes_from_the_secret_and_loses_its_trailing_slash(wire: Wire) -> None:
    await make().run({"action": "read_observation", "resource_id": "o1"})
    assert str(wire.last.url) == f"{BASE}/Observation/o1"


def test_calls_share_the_runtimes_pooled_client() -> None:
    from node_wire_runtime import rest

    assert logic._shared_client is rest._shared_client
