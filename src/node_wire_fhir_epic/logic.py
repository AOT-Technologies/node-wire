#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
# Version details
#   Epic on FHIR: R4 (FHIR 4.0.1), written against the Epic November 2026 capability statement.
#   Auth: SMART backend services, set in connectors.yaml (oauth2, private_key_jwt, RS384). The connector only
#     asks the runtime for the Authorization header.
#   epic_fhir_base_url: a tenant secret (EPIC_FHIR_BASE_URL). It is never an action input.
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type
from urllib.parse import quote, urlsplit

import httpx

from node_wire_runtime import (
    BaseConnector,
    ErrorCategory,
    RestAuthError,
    RestRateLimited,
    RestUnavailable,
    nw_action,
    sdk_action,
)
from node_wire_runtime.log_sanitization import fhir_log_extra, log_http_status_error
from node_wire_runtime.rest import _shared_client

from .normalizers import (
    assert_encounter_query_has_patient,
    normalize_fhir_read_patient,
    normalize_fhir_search_encounter,
    normalize_fhir_search_patients,
)
from .schema import (
    CreateAllergyIntoleranceInput,
    CreateBodyStructureInput,
    CreateCommunicationInput,
    CreateConditionInput,
    CreateObservationInput,
    CreatePatientInput,
    CreateProcedureInput,
    CreateQuestionnaireResponseInput,
    CreateServiceRequestInput,
    ReadAccountInput,
    ReadAdverseEventInput,
    ReadAllergyIntoleranceInput,
    ReadAppointmentInput,
    ReadBinaryInput,
    ReadBodyStructureInput,
    ReadCarePlanInput,
    ReadCareTeamInput,
    ReadClaimInput,
    ReadCommunicationInput,
    ReadConditionInput,
    ReadConsentInput,
    ReadContractInput,
    ReadCoverageInput,
    ReadDeviceInput,
    ReadDeviceRequestInput,
    ReadDeviceUseStatementInput,
    ReadDiagnosticReportInput,
    ReadDocumentReferenceInput,
    ReadEncounterInput,
    ReadEndpointInput,
    ReadEpisodeOfCareInput,
    ReadExplanationOfBenefitInput,
    ReadFamilyMemberHistoryInput,
    ReadFlagInput,
    ReadGoalInput,
    ReadGroupInput,
    ReadImagingStudyInput,
    ReadImmunizationInput,
    ReadImmunizationRecommendationInput,
    ReadListInput,
    ReadLocationInput,
    ReadMediaInput,
    ReadMedicationAdministrationInput,
    ReadMedicationDispenseInput,
    ReadMedicationInput,
    ReadMedicationRequestInput,
    ReadMedicationStatementInput,
    ReadNutritionOrderInput,
    ReadObservationInput,
    ReadOrganizationInput,
    ReadPractitionerInput,
    ReadPractitionerRoleInput,
    ReadProcedureInput,
    ReadProvenanceInput,
    ReadQuestionnaireInput,
    ReadQuestionnaireResponseInput,
    ReadRelatedPersonInput,
    ReadRequestGroupInput,
    ReadResearchStudyInput,
    ReadResearchSubjectInput,
    ReadServiceRequestInput,
    ReadSpecimenInput,
    ReadSubstanceInput,
    ReadTaskInput,
    ReadValueSetInput,
    SearchAccountInput,
    SearchAdverseEventInput,
    SearchAllergyIntoleranceInput,
    SearchAppointmentInput,
    SearchBinaryInput,
    SearchBodyStructureInput,
    SearchCarePlanInput,
    SearchCareTeamInput,
    SearchClaimInput,
    SearchCommunicationInput,
    SearchConditionInput,
    SearchConsentInput,
    SearchContractInput,
    SearchCoverageInput,
    SearchDeviceInput,
    SearchDeviceRequestInput,
    SearchDeviceUseStatementInput,
    SearchDiagnosticReportInput,
    SearchEndpointInput,
    SearchEpisodeOfCareInput,
    SearchExplanationOfBenefitInput,
    SearchFamilyMemberHistoryInput,
    SearchFlagInput,
    SearchGoalInput,
    SearchGroupInput,
    SearchImagingStudyInput,
    SearchImmunizationInput,
    SearchImmunizationRecommendationInput,
    SearchListInput,
    SearchLocationInput,
    SearchMediaInput,
    SearchMedicationAdministrationInput,
    SearchMedicationDispenseInput,
    SearchMedicationInput,
    SearchMedicationRequestInput,
    SearchMedicationStatementInput,
    SearchNutritionOrderInput,
    SearchObservationInput,
    SearchOrganizationInput,
    SearchPractitionerInput,
    SearchPractitionerRoleInput,
    SearchProcedureInput,
    SearchQuestionnaireInput,
    SearchQuestionnaireResponseInput,
    SearchRelatedPersonInput,
    SearchRequestGroupInput,
    SearchResearchStudyInput,
    SearchResearchSubjectInput,
    SearchServiceRequestInput,
    SearchSpecimenInput,
    SearchSubstanceInput,
    SearchTaskInput,
    UpdateBodyStructureInput,
    UpdateDiagnosticReportInput,
    UpdateDocumentReferenceInput,
    UpdateObservationInput,
    UpdateProcedureInput,
    UpdateServiceRequestInput,
    UpdateTaskInput,
    BulkExportDeleteInput,
    BulkExportFileInput,
    BulkExportGroupInput,
    BulkExportStatusInput,
    FetchNextPageInput,
    FhirDocumentReferenceCreateInput,
    FhirDocumentReferenceSearchInput,
    FhirEncounterSearchInput,
    FhirEpicOperationOutput,
    FhirPatientReadInput,
    FhirPatientSearchInput,
    PatientMatchInput,
    PatientSummaryInput,
    ValueSetExpandInput,
)

logger = logging.getLogger("connectors.fhir_epic")
# httpx logs every request URL at INFO, query string included, and here that is a patient id or a name.
# Simplified: quiet the whole httpx logger rather than filter it; its WARNING and ERROR records stay.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Simplified: a bulk file is returned whole in the answer, so it is capped. It is read in chunks and refused as
# soon as it passes the cap. Narrow the export with type and type_filter (Epic's own advice) to keep files small.
_MAX_BULK_FILE_BYTES = 20_000_000


class _WriteTimedOut(httpx.TimeoutException):
    """A write timed out after it was sent: Epic may have stored it, and it has no conditional create."""


class _AuthRejected(httpx.HTTPStatusError):
    """403: the Epic app is not authorised for this API."""


class _ServerError(httpx.HTTPStatusError):
    """A 5xx that is not retried: any 5xx on a write, and a 500 on a read."""


class _Rejected(httpx.HTTPStatusError, ValueError):
    """Any other error status. A ValueError too, as the original connector's create error was."""


def _diagnostics(response: httpx.Response) -> str:
    """The OperationOutcome diagnostics of an error answer, when it has them."""
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict) or data.get("resourceType") != "OperationOutcome":
        return ""
    issues = data.get("issue")
    texts = [i["diagnostics"] for i in issues or [] if isinstance(i, dict) and i.get("diagnostics")]
    return " | ".join(texts)


def _status_error(method: str, exc: httpx.HTTPStatusError) -> httpx.HTTPStatusError:
    """The HTTPStatusError as the subclass that carries the right category."""
    response = exc.response
    status = response.status_code
    message = f"Epic Error: {_diagnostics(response) or f'HTTP {status} from Epic FHIR endpoint'}"
    cls: type[httpx.HTTPStatusError] = _Rejected
    if status == 401:
        cls = RestAuthError
    elif status == 403:
        cls = _AuthRejected
    elif status == 429:
        cls = RestRateLimited
    elif status in (502, 503, 504) and method in ("GET", "DELETE"):
        cls = RestUnavailable
    elif status >= 500:
        cls = _ServerError
    return cls(message, request=exc.request, response=response)


def _doc_summary(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Keys and sizes of a DocumentReference body, for a log line (no content)."""
    content = doc.get("content")
    attachment: Dict[str, Any] = {}
    if isinstance(content, list) and content and isinstance(content[0], dict):
        found = content[0].get("attachment")
        attachment = found if isinstance(found, dict) else {}
    data = attachment.get("data")
    return {
        "keys": sorted(doc),
        "content_items": len(content) if isinstance(content, list) else 0,
        "attachment_content_type": attachment.get("contentType"),
        "attachment_data_length": len(data) if isinstance(data, str) else 0,
    }


def _bundle(data: Any) -> tuple[list[Dict[str, Any]], Optional[int], Optional[str]]:
    """Resources, total and next link of a search Bundle."""
    if not isinstance(data, dict):
        return [], None, None
    # An entry with search.mode "outcome" is Epic's notice (for example "returns no results"), not data.
    resources = [
        e["resource"]
        for e in data.get("entry") or []
        if "resource" in e and (e.get("search") or {}).get("mode") != "outcome"
    ]
    nxt = next((x.get("url") for x in data.get("link") or [] if x.get("relation") == "next"), None)
    return resources, data.get("total"), nxt


def _name_params(
    given: Optional[str],
    family: Optional[str],
    name: Optional[str],
    birthdate: Optional[str],
    extra: Optional[Dict[str, str]],
) -> Dict[str, str]:
    params: Dict[str, str] = dict(extra or {})
    if given and given.strip():
        params["given"] = given.strip()
    if family and family.strip():
        params["family"] = family.strip()
    if name and name.strip() and "given" not in params and "family" not in params:
        params["name"] = name.strip()
    if birthdate and birthdate.strip():
        params["birthdate"] = birthdate.strip()
    return params


class FhirEpicConnector(BaseConnector):
    """Epic FHIR R4: one @nw_action per read, search, create and update, plus the bulk and operation calls."""

    connector_id = "fhir_epic"
    action = "execute"
    output_model = FhirEpicOperationOutput

    # Simplified: other 5xx (and 502/503/504 on writes) are not retried, nor is a timeout of a write after
    # it was sent.
    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {
        httpx.TimeoutException: (ErrorCategory.RETRYABLE, "FHIR_TIMEOUT"),
        httpx.ConnectError: (ErrorCategory.RETRYABLE, "FHIR_CONNECT_ERROR"),
        _WriteTimedOut: (ErrorCategory.FATAL, "FHIR_TIMEOUT_OUTCOME_UNKNOWN"),
        httpx.RequestError: (ErrorCategory.FATAL, "FHIR_REQUEST_ERROR"),
        httpx.HTTPStatusError: (ErrorCategory.BUSINESS, "FHIR_HTTP_ERROR"),
        _ServerError: (ErrorCategory.FATAL, "FHIR_SERVER_ERROR"),
        RestAuthError: (ErrorCategory.AUTH, "FHIR_AUTH_ERROR"),
        _AuthRejected: (ErrorCategory.AUTH, "FHIR_AUTH_ERROR"),
        RestRateLimited: (ErrorCategory.RETRYABLE, "FHIR_RATE_LIMITED"),
        RestUnavailable: (ErrorCategory.RETRYABLE, "FHIR_UNAVAILABLE"),
        ValueError: (ErrorCategory.BUSINESS, "FHIR_INVALID_REQUEST"),
    }

    # ---- Shared helpers ----------------------------------------------------
    def _base_url(self) -> str:
        return self.secret_provider.get_secret("epic_fhir_base_url").rstrip("/")

    def _same_server(self, url: str) -> str:
        """The url, if it is on the configured Epic server; otherwise the auth header would leak."""
        got, want = urlsplit(url), urlsplit(self._base_url())
        if (got.scheme, got.hostname, got.port) != (want.scheme, want.hostname, want.port):
            raise ValueError("the url must be on the configured Epic server (epic_fhir_base_url)")
        return url

    async def _send(
        self,
        method: str,
        url: str,
        trace_id: str,
        *,
        query: Any = None,
        body: Any = None,
        headers: Optional[Dict[str, str]] = None,
        repeatable: Optional[bool] = None,
        max_bytes: Optional[int] = None,
    ) -> httpx.Response:
        """One request. ``repeatable``: a retry of this call is harmless (default: GET and DELETE).

        ``max_bytes``: read the answer in chunks and refuse one that is larger.
        """
        merged: Dict[str, str] = {"Accept": "application/fhir+json"}
        merged.update(await self.get_auth_headers())
        merged.update(headers or {})
        if body is not None:
            merged.setdefault("Content-Type", "application/fhir+json")
        if repeatable is None:
            repeatable = method in ("GET", "DELETE")
        # Only the method and the URL path are logged, never query values or bodies (patient data).
        logger.info(
            "Calling Epic FHIR",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "method": method,
                "path": urlsplit(url).path,
            },
        )
        kwargs: Dict[str, Any] = {
            "headers": merged,
            "params": query or None,
            "timeout": float(os.getenv("NW_TIMEOUT", "30.0")),
        }
        if body is not None:
            kwargs["json"] = body
        try:
            client = _shared_client(True)
            if max_bytes is None:
                response = await getattr(client, method.lower())(url, **kwargs)
            else:
                response = await self._read_capped(client, method, url, kwargs, max_bytes)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log_http_status_error(
                logger,
                "Epic FHIR request failed",
                exc,
                trace_id=trace_id,
                connector_id=self.connector_id,
            )
            raise _status_error(method, exc) from exc
        except httpx.TimeoutException as exc:
            # A connect or pool timeout means nothing was sent, so a retry is always safe.
            sent = not isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout))
            logger.error(
                "Epic FHIR request failed | error=%s",
                type(exc).__name__,
                extra={"trace_id": trace_id, "connector_id": self.connector_id},
            )
            if sent and not repeatable:
                raise _WriteTimedOut(str(exc) or "timed out after the request was sent") from exc
            raise
        except Exception as exc:
            logger.error(
                "Epic FHIR request failed | error=%s",
                type(exc).__name__,
                extra={"trace_id": trace_id, "connector_id": self.connector_id},
            )
            raise
        return response

    @staticmethod
    async def _read_capped(
        client: httpx.AsyncClient, method: str, url: str, kwargs: Dict[str, Any], max_bytes: int
    ) -> httpx.Response:
        """The answer, read in chunks. A body over ``max_bytes`` is refused before it is all in memory."""
        async with client.stream(method, url, **kwargs) as streamed:
            body = bytearray()
            async for chunk in streamed.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise ValueError(
                        f"the export file is larger than {max_bytes // 1_000_000} MB; "
                        "narrow the export with type and type_filter"
                    )
            # The chunks are already decoded, so the headers that describe the wire body are dropped.
            dropped = ("content-encoding", "content-length", "transfer-encoding")
            headers = [
                (k, v) for k, v in streamed.headers.multi_items() if k.lower() not in dropped
            ]
            return httpx.Response(
                streamed.status_code,
                headers=headers,
                content=bytes(body),
                request=streamed.request,
            )

    @staticmethod
    def _query(params: Any) -> Dict[str, Any]:
        """The fields that were set, under their wire names. Lists go out as repeated parameters."""
        fields = type(params).model_fields
        values = params.model_dump(mode="json", exclude_unset=True, exclude={"action"})
        return {fields[n].serialization_alias or n: v for n, v in values.items()}

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        return response.json() if response.content else None

    async def _read(self, rtype: str, rid: str, trace_id: str) -> FhirEpicOperationOutput:
        url = f"{self._base_url()}/{rtype}/{quote(rid, safe='')}"
        response = await self._send("GET", url, trace_id)
        return FhirEpicOperationOutput(resource=response.json())

    async def _search(self, rtype: str, params: Any, trace_id: str) -> FhirEpicOperationOutput:
        response = await self._send(
            "GET", f"{self._base_url()}/{rtype}", trace_id, query=self._query(params)
        )
        return self._page(response)

    @staticmethod
    def _page(response: httpx.Response) -> FhirEpicOperationOutput:
        resources, total, nxt = _bundle(response.json())
        return FhirEpicOperationOutput(resources=resources, total=total, next_url=nxt)

    async def _create(self, rtype: str, params: Any, trace_id: str) -> FhirEpicOperationOutput:
        response = await self._send(
            "POST", f"{self._base_url()}/{rtype}", trace_id, body=params.resource
        )
        return self._created(rtype, response)

    async def _update(self, rtype: str, params: Any, trace_id: str) -> FhirEpicOperationOutput:
        url = f"{self._base_url()}/{rtype}/{quote(params.resource_id, safe='')}"
        # A repeated PUT writes the same state, so a timeout after it was sent may be retried.
        response = await self._send("PUT", url, trace_id, body=params.resource, repeatable=True)
        body = self._json(response)
        return FhirEpicOperationOutput(
            resource_id=params.resource_id, resource=body if isinstance(body, dict) else None
        )

    @staticmethod
    def _created(rtype: str, response: httpx.Response) -> FhirEpicOperationOutput:
        """The id and, when Epic returns it, the body of a created resource."""
        resource_id: Optional[str] = None
        body: Dict[str, Any] = {}
        location = response.headers.get("Location", "")
        if location:
            history = location.find("/_history/")
            resource_id = (location[:history] if history != -1 else location).split("/")[-1]
        if not resource_id and response.content:
            try:
                body = response.json()
                resource_id = body.get("id")
            except Exception as exc:
                logger.debug("Could not parse create response body as JSON: %s", type(exc).__name__)
        if not resource_id:
            raise ValueError(
                f"Could not extract resource ID from {rtype} create response. "
                f"Status: {response.status_code}, Location: {location!r}"
            )
        return FhirEpicOperationOutput(resource_id=resource_id, resource=body or None)

    # ---- Wave 0: the original five actions ---------------------------------
    @sdk_action("read_patient", alias_tolerant=True, mcp_normalize=normalize_fhir_read_patient)
    async def read_patient(
        self, params: FhirPatientReadInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        base = self._base_url()
        if params.resource_id:
            mode, url, query = (
                "read_by_id",
                f"{base}/Patient/{quote(params.resource_id, safe='')}",
                None,
            )
        elif params.given_name or params.family_name or params.name:
            mode, url = "read_by_name", f"{base}/Patient"
            query = _name_params(
                params.given_name,
                params.family_name,
                params.name,
                params.birthdate,
                params.search_params,
            )
        elif params.search_params:
            mode, url, query = "read_by_search", f"{base}/Patient", params.search_params
        else:
            raise ValueError(
                "Provide resource_id, or name fields (given_name/family_name/name), or search_params"
            )
        logger.info(
            "FHIR Patient read",
            extra=fhir_log_extra(trace_id, connector_id=self.connector_id, mode=mode),
        )
        data = (await self._send("GET", url, trace_id, query=query)).json()
        if data.get("resourceType") == "Bundle":
            if not data.get("entry"):
                raise ValueError("No patients found in search results")
            data = data["entry"][0].get("resource", {})
        return FhirEpicOperationOutput(resource=data)

    @sdk_action(
        "search_patients", alias_tolerant=True, mcp_normalize=normalize_fhir_search_patients
    )
    async def search_patients(
        self, params: FhirPatientSearchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        base = self._base_url()
        if params.resource_ids:
            ids = [rid.strip() for rid in params.resource_ids if rid.strip()]
            if not ids:
                raise ValueError("resource_ids list is empty")

            async def fetch(rid: str) -> tuple[str, Optional[Dict[str, Any]], Optional[str]]:
                try:
                    url = f"{base}/Patient/{quote(rid, safe='')}"
                    return rid, (await self._send("GET", url, trace_id)).json(), None
                except Exception as exc:
                    return rid, None, str(exc)

            results = await asyncio.gather(*[fetch(rid) for rid in ids])
            found = [r for _, r, _ in results if r is not None]
            errors = [
                {"resource_id": i, "error": e or "Unknown error"}
                for i, r, e in results
                if r is None
            ]
            return FhirEpicOperationOutput(resources=found, total=len(found), errors=errors)

        query = _name_params(
            params.given_name,
            params.family_name,
            params.name,
            params.birthdate,
            params.search_params,
        )
        if not query:
            raise ValueError(
                "Provide resource_ids for multi-ID lookup, or at least one of "
                "given_name / family_name / name / birthdate / search_params for name-based search"
            )
        response = await self._send("GET", f"{base}/Patient", trace_id, query=query)
        page = self._page(response)
        page.errors = []  # as before: a name search reports no per-id errors
        return page

    @sdk_action(
        "search_encounter", alias_tolerant=True, mcp_normalize=normalize_fhir_search_encounter
    )
    async def search_encounter(
        self, params: FhirEncounterSearchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        if not (params.patient_id or params.status or params.date or params.search_params):
            raise ValueError("Provide at least patient_id, status, date OR search_params")
        query: Dict[str, str] = dict(params.search_params or {})
        for key, value in (
            ("patient", params.patient_id),
            ("status", params.status),
            ("date", params.date),
        ):
            if value and value.strip():
                query[key] = value.strip()
        assert_encounter_query_has_patient(query)
        response = await self._send("GET", f"{self._base_url()}/Encounter", trace_id, query=query)
        return self._page(response)

    @nw_action("create_document_reference")
    async def create_document_reference(
        self, params: FhirDocumentReferenceCreateInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        doc: Dict[str, Any] = {
            "resourceType": "DocumentReference",
            "identifier": params.identifier,
            "status": params.status,
            "type": params.type,
            "subject": {"reference": params.subject},
            "date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "content": [
                {
                    "attachment": {
                        "contentType": params.content_type or "text/plain",
                        "data": params.data,
                    }
                }
            ],
        }
        for key in ("category", "author", "description", "context"):
            if getattr(params, key):
                doc[key] = getattr(params, key)
        doc.update(params.additional_fields or {})
        try:
            response = await self._send(
                "POST", f"{self._base_url()}/DocumentReference", trace_id, body=doc
            )
        except httpx.HTTPStatusError:
            # The shape of the body, never its content, helps to see why Epic refused it.
            logger.error(
                "FHIR DocumentReference create failed | payload_summary=%s",
                json.dumps(_doc_summary(doc)),
                extra={"trace_id": trace_id, "connector_id": self.connector_id},
            )
            raise
        return self._created("DocumentReference", response)

    @nw_action("search_document_reference")
    async def search_document_reference(
        self, params: FhirDocumentReferenceSearchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        response = await self._send(
            "GET", f"{self._base_url()}/DocumentReference", trace_id, query=params.search_params
        )
        return self._page(response)

    # ---- Operations --------------------------------------------------------
    @nw_action("fetch_next_page", http_method="GET", http_path="/{next link}")
    async def fetch_next_page(
        self, params: FetchNextPageInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        response = await self._send("GET", self._same_server(params.url), trace_id)
        return self._page(response)

    @nw_action("patient_match", http_method="POST", http_path="/Patient/$match")
    async def patient_match(
        self, params: PatientMatchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        parts: List[Dict[str, Any]] = [{"name": "resource", "resource": params.patient}]
        if params.only_certain_matches is not None:
            parts.append(
                {"name": "onlyCertainMatches", "valueBoolean": params.only_certain_matches}
            )
        if params.count is not None:
            parts.append({"name": "count", "valueInteger": params.count})
        body = {"resourceType": "Parameters", "parameter": parts}
        # A match only reads, so a timeout may be retried.
        response = await self._send(
            "POST", f"{self._base_url()}/Patient/$match", trace_id, body=body, repeatable=True
        )
        resources, total, nxt = _bundle(response.json())
        return FhirEpicOperationOutput(resources=resources, total=total, next_url=nxt)

    @nw_action("patient_summary", http_method="GET", http_path="/Patient/{id}/$summary")
    async def patient_summary(
        self, params: PatientSummaryInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        url = f"{self._base_url()}/Patient/{quote(params.resource_id, safe='')}/$summary"
        return FhirEpicOperationOutput(resource=(await self._send("GET", url, trace_id)).json())

    @nw_action("value_set_expand", http_method="GET", http_path="/ValueSet/$expand")
    async def value_set_expand(
        self, params: ValueSetExpandInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        response = await self._send(
            "GET", f"{self._base_url()}/ValueSet/$expand", trace_id, query=self._query(params)
        )
        return FhirEpicOperationOutput(resource=response.json())

    @nw_action("bulk_export_group", http_method="GET", http_path="/Group/{id}/$export")
    async def bulk_export_group(
        self, params: BulkExportGroupInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        query = {
            k: v
            for k, v in (
                ("_type", params.type),
                ("includeAssociatedData", params.include_associated_data),
                ("_typeFilter", params.type_filter),
            )
            if v
        }
        url = f"{self._base_url()}/Group/{quote(params.resource_id, safe='')}/$export"
        # Epic allows one kick-off per group per 24 hours, so a lost answer is not retried blindly.
        response = await self._send(
            "GET", url, trace_id, query=query, headers={"Prefer": "respond-async"}, repeatable=False
        )
        return FhirEpicOperationOutput(
            status_code=response.status_code,
            status_url=response.headers.get("Content-Location"),
        )

    @nw_action("bulk_export_status", http_method="GET", http_path="/BulkRequest/{id}")
    async def bulk_export_status(
        self, params: BulkExportStatusInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        response = await self._send("GET", self._same_server(params.status_url), trace_id)
        body = self._json(response)
        return FhirEpicOperationOutput(
            status_code=response.status_code,
            progress=response.headers.get("X-Progress"),
            resource=body if isinstance(body, dict) else None,
        )

    @nw_action("bulk_export_file", http_method="GET", http_path="/{file url}")
    async def bulk_export_file(
        self, params: BulkExportFileInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        response = await self._send(
            "GET",
            self._same_server(params.file_url),
            trace_id,
            headers={"Accept": "application/fhir+ndjson"},
            max_bytes=_MAX_BULK_FILE_BYTES,
        )
        return FhirEpicOperationOutput(status_code=response.status_code, content=response.text)

    @nw_action("bulk_export_delete", http_method="DELETE", http_path="/BulkRequest/{id}")
    async def bulk_export_delete(
        self, params: BulkExportDeleteInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        response = await self._send("DELETE", self._same_server(params.status_url), trace_id)
        return FhirEpicOperationOutput(status_code=response.status_code)

    # ---- Wave 1: US Core -----------------------------------------
    @nw_action("read_allergy_intolerance", http_method="GET", http_path="/AllergyIntolerance/{id}")
    async def read_allergy_intolerance(
        self, params: ReadAllergyIntoleranceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("AllergyIntolerance", params.resource_id, trace_id)

    @nw_action("search_allergy_intolerance", http_method="GET", http_path="/AllergyIntolerance")
    async def search_allergy_intolerance(
        self, params: SearchAllergyIntoleranceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("AllergyIntolerance", params, trace_id)

    @nw_action("read_care_plan", http_method="GET", http_path="/CarePlan/{id}")
    async def read_care_plan(
        self, params: ReadCarePlanInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("CarePlan", params.resource_id, trace_id)

    @nw_action("search_care_plan", http_method="GET", http_path="/CarePlan")
    async def search_care_plan(
        self, params: SearchCarePlanInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("CarePlan", params, trace_id)

    @nw_action("read_care_team", http_method="GET", http_path="/CareTeam/{id}")
    async def read_care_team(
        self, params: ReadCareTeamInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("CareTeam", params.resource_id, trace_id)

    @nw_action("search_care_team", http_method="GET", http_path="/CareTeam")
    async def search_care_team(
        self, params: SearchCareTeamInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("CareTeam", params, trace_id)

    @nw_action("read_condition", http_method="GET", http_path="/Condition/{id}")
    async def read_condition(
        self, params: ReadConditionInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Condition", params.resource_id, trace_id)

    @nw_action("search_condition", http_method="GET", http_path="/Condition")
    async def search_condition(
        self, params: SearchConditionInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Condition", params, trace_id)

    @nw_action("read_coverage", http_method="GET", http_path="/Coverage/{id}")
    async def read_coverage(
        self, params: ReadCoverageInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Coverage", params.resource_id, trace_id)

    @nw_action("search_coverage", http_method="GET", http_path="/Coverage")
    async def search_coverage(
        self, params: SearchCoverageInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Coverage", params, trace_id)

    @nw_action("read_device", http_method="GET", http_path="/Device/{id}")
    async def read_device(
        self, params: ReadDeviceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Device", params.resource_id, trace_id)

    @nw_action("search_device", http_method="GET", http_path="/Device")
    async def search_device(
        self, params: SearchDeviceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Device", params, trace_id)

    @nw_action("read_diagnostic_report", http_method="GET", http_path="/DiagnosticReport/{id}")
    async def read_diagnostic_report(
        self, params: ReadDiagnosticReportInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("DiagnosticReport", params.resource_id, trace_id)

    @nw_action("search_diagnostic_report", http_method="GET", http_path="/DiagnosticReport")
    async def search_diagnostic_report(
        self, params: SearchDiagnosticReportInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("DiagnosticReport", params, trace_id)

    @nw_action("read_document_reference", http_method="GET", http_path="/DocumentReference/{id}")
    async def read_document_reference(
        self, params: ReadDocumentReferenceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("DocumentReference", params.resource_id, trace_id)

    @nw_action("read_encounter", http_method="GET", http_path="/Encounter/{id}")
    async def read_encounter(
        self, params: ReadEncounterInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Encounter", params.resource_id, trace_id)

    @nw_action("read_goal", http_method="GET", http_path="/Goal/{id}")
    async def read_goal(self, params: ReadGoalInput, *, trace_id: str) -> FhirEpicOperationOutput:
        return await self._read("Goal", params.resource_id, trace_id)

    @nw_action("search_goal", http_method="GET", http_path="/Goal")
    async def search_goal(
        self, params: SearchGoalInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Goal", params, trace_id)

    @nw_action("read_immunization", http_method="GET", http_path="/Immunization/{id}")
    async def read_immunization(
        self, params: ReadImmunizationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Immunization", params.resource_id, trace_id)

    @nw_action("search_immunization", http_method="GET", http_path="/Immunization")
    async def search_immunization(
        self, params: SearchImmunizationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Immunization", params, trace_id)

    @nw_action("read_location", http_method="GET", http_path="/Location/{id}")
    async def read_location(
        self, params: ReadLocationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Location", params.resource_id, trace_id)

    @nw_action("search_location", http_method="GET", http_path="/Location")
    async def search_location(
        self, params: SearchLocationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Location", params, trace_id)

    @nw_action("read_medication", http_method="GET", http_path="/Medication/{id}")
    async def read_medication(
        self, params: ReadMedicationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Medication", params.resource_id, trace_id)

    @nw_action("search_medication", http_method="GET", http_path="/Medication")
    async def search_medication(
        self, params: SearchMedicationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Medication", params, trace_id)

    @nw_action("read_medication_dispense", http_method="GET", http_path="/MedicationDispense/{id}")
    async def read_medication_dispense(
        self, params: ReadMedicationDispenseInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("MedicationDispense", params.resource_id, trace_id)

    @nw_action("search_medication_dispense", http_method="GET", http_path="/MedicationDispense")
    async def search_medication_dispense(
        self, params: SearchMedicationDispenseInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("MedicationDispense", params, trace_id)

    @nw_action("read_medication_request", http_method="GET", http_path="/MedicationRequest/{id}")
    async def read_medication_request(
        self, params: ReadMedicationRequestInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("MedicationRequest", params.resource_id, trace_id)

    @nw_action("search_medication_request", http_method="GET", http_path="/MedicationRequest")
    async def search_medication_request(
        self, params: SearchMedicationRequestInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("MedicationRequest", params, trace_id)

    @nw_action("read_observation", http_method="GET", http_path="/Observation/{id}")
    async def read_observation(
        self, params: ReadObservationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Observation", params.resource_id, trace_id)

    @nw_action("search_observation", http_method="GET", http_path="/Observation")
    async def search_observation(
        self, params: SearchObservationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Observation", params, trace_id)

    @nw_action("read_organization", http_method="GET", http_path="/Organization/{id}")
    async def read_organization(
        self, params: ReadOrganizationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Organization", params.resource_id, trace_id)

    @nw_action("search_organization", http_method="GET", http_path="/Organization")
    async def search_organization(
        self, params: SearchOrganizationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Organization", params, trace_id)

    @nw_action("read_practitioner", http_method="GET", http_path="/Practitioner/{id}")
    async def read_practitioner(
        self, params: ReadPractitionerInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Practitioner", params.resource_id, trace_id)

    @nw_action("search_practitioner", http_method="GET", http_path="/Practitioner")
    async def search_practitioner(
        self, params: SearchPractitionerInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Practitioner", params, trace_id)

    @nw_action("read_practitioner_role", http_method="GET", http_path="/PractitionerRole/{id}")
    async def read_practitioner_role(
        self, params: ReadPractitionerRoleInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("PractitionerRole", params.resource_id, trace_id)

    @nw_action("search_practitioner_role", http_method="GET", http_path="/PractitionerRole")
    async def search_practitioner_role(
        self, params: SearchPractitionerRoleInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("PractitionerRole", params, trace_id)

    @nw_action("read_procedure", http_method="GET", http_path="/Procedure/{id}")
    async def read_procedure(
        self, params: ReadProcedureInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Procedure", params.resource_id, trace_id)

    @nw_action("search_procedure", http_method="GET", http_path="/Procedure")
    async def search_procedure(
        self, params: SearchProcedureInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Procedure", params, trace_id)

    @nw_action("read_service_request", http_method="GET", http_path="/ServiceRequest/{id}")
    async def read_service_request(
        self, params: ReadServiceRequestInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("ServiceRequest", params.resource_id, trace_id)

    @nw_action("search_service_request", http_method="GET", http_path="/ServiceRequest")
    async def search_service_request(
        self, params: SearchServiceRequestInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("ServiceRequest", params, trace_id)

    @nw_action("read_specimen", http_method="GET", http_path="/Specimen/{id}")
    async def read_specimen(
        self, params: ReadSpecimenInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Specimen", params.resource_id, trace_id)

    @nw_action("search_specimen", http_method="GET", http_path="/Specimen")
    async def search_specimen(
        self, params: SearchSpecimenInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Specimen", params, trace_id)

    @nw_action("read_related_person", http_method="GET", http_path="/RelatedPerson/{id}")
    async def read_related_person(
        self, params: ReadRelatedPersonInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("RelatedPerson", params.resource_id, trace_id)

    @nw_action("search_related_person", http_method="GET", http_path="/RelatedPerson")
    async def search_related_person(
        self, params: SearchRelatedPersonInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("RelatedPerson", params, trace_id)

    @nw_action("read_binary", http_method="GET", http_path="/Binary/{id}")
    async def read_binary(
        self, params: ReadBinaryInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Binary", params.resource_id, trace_id)

    @nw_action("search_binary", http_method="GET", http_path="/Binary")
    async def search_binary(
        self, params: SearchBinaryInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Binary", params, trace_id)

    @nw_action("read_provenance", http_method="GET", http_path="/Provenance/{id}")
    async def read_provenance(
        self, params: ReadProvenanceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Provenance", params.resource_id, trace_id)

    # ---- Wave 2: other resources ---------------------------------
    @nw_action("read_account", http_method="GET", http_path="/Account/{id}")
    async def read_account(
        self, params: ReadAccountInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Account", params.resource_id, trace_id)

    @nw_action("search_account", http_method="GET", http_path="/Account")
    async def search_account(
        self, params: SearchAccountInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Account", params, trace_id)

    @nw_action("read_adverse_event", http_method="GET", http_path="/AdverseEvent/{id}")
    async def read_adverse_event(
        self, params: ReadAdverseEventInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("AdverseEvent", params.resource_id, trace_id)

    @nw_action("search_adverse_event", http_method="GET", http_path="/AdverseEvent")
    async def search_adverse_event(
        self, params: SearchAdverseEventInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("AdverseEvent", params, trace_id)

    @nw_action("read_appointment", http_method="GET", http_path="/Appointment/{id}")
    async def read_appointment(
        self, params: ReadAppointmentInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Appointment", params.resource_id, trace_id)

    @nw_action("search_appointment", http_method="GET", http_path="/Appointment")
    async def search_appointment(
        self, params: SearchAppointmentInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Appointment", params, trace_id)

    @nw_action("read_body_structure", http_method="GET", http_path="/BodyStructure/{id}")
    async def read_body_structure(
        self, params: ReadBodyStructureInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("BodyStructure", params.resource_id, trace_id)

    @nw_action("search_body_structure", http_method="GET", http_path="/BodyStructure")
    async def search_body_structure(
        self, params: SearchBodyStructureInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("BodyStructure", params, trace_id)

    @nw_action("read_claim", http_method="GET", http_path="/Claim/{id}")
    async def read_claim(self, params: ReadClaimInput, *, trace_id: str) -> FhirEpicOperationOutput:
        return await self._read("Claim", params.resource_id, trace_id)

    @nw_action("search_claim", http_method="GET", http_path="/Claim")
    async def search_claim(
        self, params: SearchClaimInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Claim", params, trace_id)

    @nw_action("read_communication", http_method="GET", http_path="/Communication/{id}")
    async def read_communication(
        self, params: ReadCommunicationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Communication", params.resource_id, trace_id)

    @nw_action("search_communication", http_method="GET", http_path="/Communication")
    async def search_communication(
        self, params: SearchCommunicationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Communication", params, trace_id)

    @nw_action("read_consent", http_method="GET", http_path="/Consent/{id}")
    async def read_consent(
        self, params: ReadConsentInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Consent", params.resource_id, trace_id)

    @nw_action("search_consent", http_method="GET", http_path="/Consent")
    async def search_consent(
        self, params: SearchConsentInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Consent", params, trace_id)

    @nw_action("read_contract", http_method="GET", http_path="/Contract/{id}")
    async def read_contract(
        self, params: ReadContractInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Contract", params.resource_id, trace_id)

    @nw_action("search_contract", http_method="GET", http_path="/Contract")
    async def search_contract(
        self, params: SearchContractInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Contract", params, trace_id)

    @nw_action("read_device_request", http_method="GET", http_path="/DeviceRequest/{id}")
    async def read_device_request(
        self, params: ReadDeviceRequestInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("DeviceRequest", params.resource_id, trace_id)

    @nw_action("search_device_request", http_method="GET", http_path="/DeviceRequest")
    async def search_device_request(
        self, params: SearchDeviceRequestInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("DeviceRequest", params, trace_id)

    @nw_action("read_device_use_statement", http_method="GET", http_path="/DeviceUseStatement/{id}")
    async def read_device_use_statement(
        self, params: ReadDeviceUseStatementInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("DeviceUseStatement", params.resource_id, trace_id)

    @nw_action("search_device_use_statement", http_method="GET", http_path="/DeviceUseStatement")
    async def search_device_use_statement(
        self, params: SearchDeviceUseStatementInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("DeviceUseStatement", params, trace_id)

    @nw_action("read_endpoint", http_method="GET", http_path="/Endpoint/{id}")
    async def read_endpoint(
        self, params: ReadEndpointInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Endpoint", params.resource_id, trace_id)

    @nw_action("search_endpoint", http_method="GET", http_path="/Endpoint")
    async def search_endpoint(
        self, params: SearchEndpointInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Endpoint", params, trace_id)

    @nw_action("read_episode_of_care", http_method="GET", http_path="/EpisodeOfCare/{id}")
    async def read_episode_of_care(
        self, params: ReadEpisodeOfCareInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("EpisodeOfCare", params.resource_id, trace_id)

    @nw_action("search_episode_of_care", http_method="GET", http_path="/EpisodeOfCare")
    async def search_episode_of_care(
        self, params: SearchEpisodeOfCareInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("EpisodeOfCare", params, trace_id)

    @nw_action(
        "read_explanation_of_benefit", http_method="GET", http_path="/ExplanationOfBenefit/{id}"
    )
    async def read_explanation_of_benefit(
        self, params: ReadExplanationOfBenefitInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("ExplanationOfBenefit", params.resource_id, trace_id)

    @nw_action(
        "search_explanation_of_benefit", http_method="GET", http_path="/ExplanationOfBenefit"
    )
    async def search_explanation_of_benefit(
        self, params: SearchExplanationOfBenefitInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("ExplanationOfBenefit", params, trace_id)

    @nw_action(
        "read_family_member_history", http_method="GET", http_path="/FamilyMemberHistory/{id}"
    )
    async def read_family_member_history(
        self, params: ReadFamilyMemberHistoryInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("FamilyMemberHistory", params.resource_id, trace_id)

    @nw_action("search_family_member_history", http_method="GET", http_path="/FamilyMemberHistory")
    async def search_family_member_history(
        self, params: SearchFamilyMemberHistoryInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("FamilyMemberHistory", params, trace_id)

    @nw_action("read_flag", http_method="GET", http_path="/Flag/{id}")
    async def read_flag(self, params: ReadFlagInput, *, trace_id: str) -> FhirEpicOperationOutput:
        return await self._read("Flag", params.resource_id, trace_id)

    @nw_action("search_flag", http_method="GET", http_path="/Flag")
    async def search_flag(
        self, params: SearchFlagInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Flag", params, trace_id)

    @nw_action("read_group", http_method="GET", http_path="/Group/{id}")
    async def read_group(self, params: ReadGroupInput, *, trace_id: str) -> FhirEpicOperationOutput:
        return await self._read("Group", params.resource_id, trace_id)

    @nw_action("search_group", http_method="GET", http_path="/Group")
    async def search_group(
        self, params: SearchGroupInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Group", params, trace_id)

    @nw_action("read_imaging_study", http_method="GET", http_path="/ImagingStudy/{id}")
    async def read_imaging_study(
        self, params: ReadImagingStudyInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("ImagingStudy", params.resource_id, trace_id)

    @nw_action("search_imaging_study", http_method="GET", http_path="/ImagingStudy")
    async def search_imaging_study(
        self, params: SearchImagingStudyInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("ImagingStudy", params, trace_id)

    @nw_action(
        "read_immunization_recommendation",
        http_method="GET",
        http_path="/ImmunizationRecommendation/{id}",
    )
    async def read_immunization_recommendation(
        self, params: ReadImmunizationRecommendationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("ImmunizationRecommendation", params.resource_id, trace_id)

    @nw_action(
        "search_immunization_recommendation",
        http_method="GET",
        http_path="/ImmunizationRecommendation",
    )
    async def search_immunization_recommendation(
        self, params: SearchImmunizationRecommendationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("ImmunizationRecommendation", params, trace_id)

    @nw_action("read_list", http_method="GET", http_path="/List/{id}")
    async def read_list(self, params: ReadListInput, *, trace_id: str) -> FhirEpicOperationOutput:
        return await self._read("List", params.resource_id, trace_id)

    @nw_action("search_list", http_method="GET", http_path="/List")
    async def search_list(
        self, params: SearchListInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("List", params, trace_id)

    @nw_action("read_media", http_method="GET", http_path="/Media/{id}")
    async def read_media(self, params: ReadMediaInput, *, trace_id: str) -> FhirEpicOperationOutput:
        return await self._read("Media", params.resource_id, trace_id)

    @nw_action("search_media", http_method="GET", http_path="/Media")
    async def search_media(
        self, params: SearchMediaInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Media", params, trace_id)

    @nw_action(
        "read_medication_administration",
        http_method="GET",
        http_path="/MedicationAdministration/{id}",
    )
    async def read_medication_administration(
        self, params: ReadMedicationAdministrationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("MedicationAdministration", params.resource_id, trace_id)

    @nw_action(
        "search_medication_administration", http_method="GET", http_path="/MedicationAdministration"
    )
    async def search_medication_administration(
        self, params: SearchMedicationAdministrationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("MedicationAdministration", params, trace_id)

    @nw_action(
        "read_medication_statement", http_method="GET", http_path="/MedicationStatement/{id}"
    )
    async def read_medication_statement(
        self, params: ReadMedicationStatementInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("MedicationStatement", params.resource_id, trace_id)

    @nw_action("search_medication_statement", http_method="GET", http_path="/MedicationStatement")
    async def search_medication_statement(
        self, params: SearchMedicationStatementInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("MedicationStatement", params, trace_id)

    @nw_action("read_nutrition_order", http_method="GET", http_path="/NutritionOrder/{id}")
    async def read_nutrition_order(
        self, params: ReadNutritionOrderInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("NutritionOrder", params.resource_id, trace_id)

    @nw_action("search_nutrition_order", http_method="GET", http_path="/NutritionOrder")
    async def search_nutrition_order(
        self, params: SearchNutritionOrderInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("NutritionOrder", params, trace_id)

    @nw_action("read_questionnaire", http_method="GET", http_path="/Questionnaire/{id}")
    async def read_questionnaire(
        self, params: ReadQuestionnaireInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Questionnaire", params.resource_id, trace_id)

    @nw_action("search_questionnaire", http_method="GET", http_path="/Questionnaire")
    async def search_questionnaire(
        self, params: SearchQuestionnaireInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Questionnaire", params, trace_id)

    @nw_action(
        "read_questionnaire_response", http_method="GET", http_path="/QuestionnaireResponse/{id}"
    )
    async def read_questionnaire_response(
        self, params: ReadQuestionnaireResponseInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("QuestionnaireResponse", params.resource_id, trace_id)

    @nw_action(
        "search_questionnaire_response", http_method="GET", http_path="/QuestionnaireResponse"
    )
    async def search_questionnaire_response(
        self, params: SearchQuestionnaireResponseInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("QuestionnaireResponse", params, trace_id)

    @nw_action("read_request_group", http_method="GET", http_path="/RequestGroup/{id}")
    async def read_request_group(
        self, params: ReadRequestGroupInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("RequestGroup", params.resource_id, trace_id)

    @nw_action("search_request_group", http_method="GET", http_path="/RequestGroup")
    async def search_request_group(
        self, params: SearchRequestGroupInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("RequestGroup", params, trace_id)

    @nw_action("read_research_study", http_method="GET", http_path="/ResearchStudy/{id}")
    async def read_research_study(
        self, params: ReadResearchStudyInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("ResearchStudy", params.resource_id, trace_id)

    @nw_action("search_research_study", http_method="GET", http_path="/ResearchStudy")
    async def search_research_study(
        self, params: SearchResearchStudyInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("ResearchStudy", params, trace_id)

    @nw_action("read_research_subject", http_method="GET", http_path="/ResearchSubject/{id}")
    async def read_research_subject(
        self, params: ReadResearchSubjectInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("ResearchSubject", params.resource_id, trace_id)

    @nw_action("search_research_subject", http_method="GET", http_path="/ResearchSubject")
    async def search_research_subject(
        self, params: SearchResearchSubjectInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("ResearchSubject", params, trace_id)

    @nw_action("read_substance", http_method="GET", http_path="/Substance/{id}")
    async def read_substance(
        self, params: ReadSubstanceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("Substance", params.resource_id, trace_id)

    @nw_action("search_substance", http_method="GET", http_path="/Substance")
    async def search_substance(
        self, params: SearchSubstanceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Substance", params, trace_id)

    @nw_action("read_task", http_method="GET", http_path="/Task/{id}")
    async def read_task(self, params: ReadTaskInput, *, trace_id: str) -> FhirEpicOperationOutput:
        return await self._read("Task", params.resource_id, trace_id)

    @nw_action("search_task", http_method="GET", http_path="/Task")
    async def search_task(
        self, params: SearchTaskInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._search("Task", params, trace_id)

    @nw_action("read_value_set", http_method="GET", http_path="/ValueSet/{id}")
    async def read_value_set(
        self, params: ReadValueSetInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._read("ValueSet", params.resource_id, trace_id)

    # ---- Wave 3: writes ------------------------------------------
    @nw_action("create_allergy_intolerance", http_method="POST", http_path="/AllergyIntolerance")
    async def create_allergy_intolerance(
        self, params: CreateAllergyIntoleranceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._create("AllergyIntolerance", params, trace_id)

    @nw_action("create_body_structure", http_method="POST", http_path="/BodyStructure")
    async def create_body_structure(
        self, params: CreateBodyStructureInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._create("BodyStructure", params, trace_id)

    @nw_action("create_communication", http_method="POST", http_path="/Communication")
    async def create_communication(
        self, params: CreateCommunicationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._create("Communication", params, trace_id)

    @nw_action("create_condition", http_method="POST", http_path="/Condition")
    async def create_condition(
        self, params: CreateConditionInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._create("Condition", params, trace_id)

    @nw_action("create_observation", http_method="POST", http_path="/Observation")
    async def create_observation(
        self, params: CreateObservationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._create("Observation", params, trace_id)

    @nw_action("create_patient", http_method="POST", http_path="/Patient")
    async def create_patient(
        self, params: CreatePatientInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._create("Patient", params, trace_id)

    @nw_action("create_procedure", http_method="POST", http_path="/Procedure")
    async def create_procedure(
        self, params: CreateProcedureInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._create("Procedure", params, trace_id)

    @nw_action(
        "create_questionnaire_response", http_method="POST", http_path="/QuestionnaireResponse"
    )
    async def create_questionnaire_response(
        self, params: CreateQuestionnaireResponseInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._create("QuestionnaireResponse", params, trace_id)

    @nw_action("create_service_request", http_method="POST", http_path="/ServiceRequest")
    async def create_service_request(
        self, params: CreateServiceRequestInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._create("ServiceRequest", params, trace_id)

    @nw_action("update_body_structure", http_method="PUT", http_path="/BodyStructure/{id}")
    async def update_body_structure(
        self, params: UpdateBodyStructureInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._update("BodyStructure", params, trace_id)

    @nw_action("update_diagnostic_report", http_method="PUT", http_path="/DiagnosticReport/{id}")
    async def update_diagnostic_report(
        self, params: UpdateDiagnosticReportInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._update("DiagnosticReport", params, trace_id)

    @nw_action("update_document_reference", http_method="PUT", http_path="/DocumentReference/{id}")
    async def update_document_reference(
        self, params: UpdateDocumentReferenceInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._update("DocumentReference", params, trace_id)

    @nw_action("update_observation", http_method="PUT", http_path="/Observation/{id}")
    async def update_observation(
        self, params: UpdateObservationInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._update("Observation", params, trace_id)

    @nw_action("update_procedure", http_method="PUT", http_path="/Procedure/{id}")
    async def update_procedure(
        self, params: UpdateProcedureInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._update("Procedure", params, trace_id)

    @nw_action("update_service_request", http_method="PUT", http_path="/ServiceRequest/{id}")
    async def update_service_request(
        self, params: UpdateServiceRequestInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._update("ServiceRequest", params, trace_id)

    @nw_action("update_task", http_method="PUT", http_path="/Task/{id}")
    async def update_task(
        self, params: UpdateTaskInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        return await self._update("Task", params, trace_id)
