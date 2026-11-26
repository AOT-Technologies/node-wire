#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
# Version details
#   Oracle Health (Cerner) Millennium: FHIR R4 (4.0.1), written against the capability statement of
#     November 2026 and Oracle's FHIR R4 API documentation. DSTU2 is no longer supported by Oracle.
#   Auth: SMART backend services, set in connectors.yaml (oauth2, private_key_jwt, RS384). The token request
#     carries the scopes (system/<Resource>.read and .write), so an action only works for a resource whose
#     scope is configured (CERNER_SCOPES or the list in connectors.yaml). The connector only asks the runtime
#     for the Authorization header.
#   cerner_fhir_base_url: a tenant secret (CERNER_FHIR_BASE_URL). It is never an action input.
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type
from urllib.parse import quote, urlparse, urlsplit

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
from node_wire_runtime.rest import _shared_client, assert_safe_destination

from .normalizers import (
    assert_encounter_query_has_patient,
    normalize_fhir_read_patient,
    normalize_fhir_search_encounter,
    normalize_fhir_search_patients,
)
from .schema import (
    BulkExportDeleteInput,
    BulkExportFileInput,
    BulkExportGroupInput,
    BulkExportPatientsInput,
    BulkExportStatusInput,
    ChargeItemCreateInput,
    ChargeItemCreditInput,
    ChargeItemModifyInput,
    CreateAllergyIntoleranceInput,
    CreateAppointmentInput,
    CreateCommunicationInput,
    CreateConditionInput,
    CreateCoverageInput,
    CreateDiagnosticReportInput,
    CreateEncounterInput,
    CreateFamilyMemberHistoryInput,
    CreateFinancialTransactionInput,
    CreateImmunizationInput,
    CreateMedicationRequestInput,
    CreateObservationInput,
    CreateOrganizationInput,
    CreatePatientInput,
    CreatePractitionerInput,
    CreateProcedureInput,
    CreateProvenanceInput,
    CreateRelatedPersonInput,
    DeleteCoverageInput,
    DocrefInput,
    ExecuteBatchInput,
    FetchNextPageInput,
    FhirCernerDocumentReferenceCreateInput,
    FhirCernerDocumentReferenceSearchInput,
    FhirCernerEncounterSearchInput,
    FhirCernerOperationOutput,
    FhirCernerPatientReadInput,
    FhirCernerPatientSearchInput,
    GetCapabilityStatementInput,
    GetCaregiverOrganizationsInput,
    GetCcdInput,
    GetSmartConfigurationInput,
    HealthCardsIssueInput,
    PatchAppointmentInput,
    PatchCommunicationInput,
    PatchCoverageInput,
    PatchEncounterInput,
    PatchMedicationRequestInput,
    PatchPatientInput,
    PatchRelatedPersonInput,
    PatchSlotInput,
    ReadAccountInput,
    ReadAllergyIntoleranceInput,
    ReadAppointmentInput,
    ReadBinaryInput,
    ReadCarePlanInput,
    ReadCareTeamInput,
    ReadChargeItemInput,
    ReadCommunicationInput,
    ReadConditionInput,
    ReadConsentInput,
    ReadCoverageInput,
    ReadDeviceInput,
    ReadDiagnosticReportInput,
    ReadDocumentReferenceInput,
    ReadEncounterInput,
    ReadFamilyMemberHistoryInput,
    ReadGoalInput,
    ReadImmunizationInput,
    ReadInsurancePlanInput,
    ReadLocationInput,
    ReadMediaInput,
    ReadMedicationAdministrationInput,
    ReadMedicationDispenseInput,
    ReadMedicationRequestInput,
    ReadNutritionOrderInput,
    ReadObservationInput,
    ReadOperationDefinitionInput,
    ReadOrganizationInput,
    ReadPersonInput,
    ReadPractitionerInput,
    ReadProcedureInput,
    ReadProvenanceInput,
    ReadQuestionnaireInput,
    ReadQuestionnaireResponseInput,
    ReadRelatedPersonInput,
    ReadScheduleInput,
    ReadServiceRequestInput,
    ReadSlotInput,
    ReadSpecimenInput,
    ReadStructureDefinitionInput,
    SearchAccountInput,
    SearchAllergyIntoleranceInput,
    SearchAppointmentInput,
    SearchCarePlanInput,
    SearchCareTeamInput,
    SearchChargeItemInput,
    SearchCommunicationInput,
    SearchConditionInput,
    SearchConsentInput,
    SearchCoverageInput,
    SearchDeviceInput,
    SearchDiagnosticReportInput,
    SearchFamilyMemberHistoryInput,
    SearchGoalInput,
    SearchImmunizationInput,
    SearchInsurancePlanInput,
    SearchLocationInput,
    SearchMedicationAdministrationInput,
    SearchMedicationDispenseInput,
    SearchMedicationRequestInput,
    SearchNutritionOrderInput,
    SearchObservationInput,
    SearchOrganizationInput,
    SearchPersonInput,
    SearchPractitionerInput,
    SearchProcedureInput,
    SearchProvenanceInput,
    SearchQuestionnaireInput,
    SearchQuestionnaireResponseInput,
    SearchRelatedPersonInput,
    SearchScheduleInput,
    SearchServiceRequestInput,
    SearchSlotInput,
    SearchSpecimenInput,
    UpdateAllergyIntoleranceInput,
    UpdateConditionInput,
    UpdateDocumentReferenceInput,
    UpdateFamilyMemberHistoryInput,
    UpdateImmunizationInput,
    UpdateObservationInput,
    UpdateQuestionnaireResponseInput,
)

logger = logging.getLogger("connectors.fhir_cerner")
# httpx logs every request URL at INFO, query string included, and here that is a patient id or a name.
# Simplified: quiet the whole httpx logger rather than filter it; its WARNING and ERROR records stay.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Simplified: a bulk file is returned whole in the answer, so it is capped. It is read in chunks and refused as
# soon as it passes the cap. Narrow the export with type, since and type_filter to keep files small.
_MAX_BULK_FILE_BYTES = 20_000_000

_PATCH_JSON = "application/json-patch+json"


class _WriteTimedOut(httpx.TimeoutException):
    """A write timed out after it was sent: Cerner may have stored it, and it has no conditional create."""


class _AuthRejected(httpx.HTTPStatusError):
    """403: the Cerner app is not authorised for this API, or the token has no scope for it."""


class _ServerError(httpx.HTTPStatusError):
    """A 5xx that is not retried: any 5xx on a write, and a 500 on a read."""


class _Rejected(httpx.HTTPStatusError, ValueError):
    """Any other error status. A ValueError too, as the original connector's create error was."""


def _diagnostics(response: httpx.Response) -> str:
    """The OperationOutcome text of an error answer, when it has some. Cerner puts it in details.text."""
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict) or data.get("resourceType") != "OperationOutcome":
        return ""
    found = []
    for issue in data.get("issue") or []:
        if not isinstance(issue, dict):
            continue
        details = issue.get("details")
        text = issue.get("diagnostics") or (
            details.get("text") if isinstance(details, dict) else ""
        )
        parts = [p for p in (issue.get("severity"), issue.get("code"), text) if p]
        if parts:
            found.append(" ".join(parts))
    return " | ".join(found)


def _status_error(method: str, exc: httpx.HTTPStatusError) -> httpx.HTTPStatusError:
    """The HTTPStatusError as the subclass that carries the right category."""
    response = exc.response
    status = response.status_code
    message = (
        f"Cerner Error: {_diagnostics(response) or f'HTTP {status} from Cerner FHIR endpoint'}"
    )
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
    # An entry with search.mode "outcome" is Cerner's notice (for example "returns no results"), not data.
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


def _etag(response: httpx.Response) -> Optional[str]:
    """The resource version Cerner sends back, to be used as if_match on the next write."""
    value = response.headers.get("ETag")
    return value if isinstance(value, str) else None


def _if_match(value: str) -> str:
    """The version as an If-Match header: a bare number becomes the weak ETag W/"n"."""
    value = value.strip()
    return value if value.startswith(("W/", '"')) else f'W/"{value}"'


class FhirCernerConnector(BaseConnector):
    """Cerner FHIR R4: one @nw_action per read, search, write and operation, plus the bulk export calls."""

    connector_id = "fhir_cerner"
    action = "execute"
    output_model = FhirCernerOperationOutput

    # Simplified: other 5xx (and 502/503/504 on writes) are not retried, nor is a timeout of a write after
    # it was sent.
    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {
        httpx.TimeoutException: (ErrorCategory.RETRYABLE, "CERNER_TIMEOUT"),
        httpx.ConnectError: (ErrorCategory.RETRYABLE, "CERNER_CONNECT_ERROR"),
        _WriteTimedOut: (ErrorCategory.FATAL, "CERNER_TIMEOUT_OUTCOME_UNKNOWN"),
        httpx.RequestError: (ErrorCategory.FATAL, "CERNER_REQUEST_ERROR"),
        httpx.HTTPStatusError: (ErrorCategory.BUSINESS, "CERNER_HTTP_ERROR"),
        _ServerError: (ErrorCategory.FATAL, "CERNER_SERVER_ERROR"),
        RestAuthError: (ErrorCategory.AUTH, "CERNER_AUTH_ERROR"),
        _AuthRejected: (ErrorCategory.AUTH, "CERNER_AUTH_ERROR"),
        RestRateLimited: (ErrorCategory.RETRYABLE, "CERNER_RATE_LIMITED"),
        RestUnavailable: (ErrorCategory.RETRYABLE, "CERNER_UNAVAILABLE"),
        ValueError: (ErrorCategory.BUSINESS, "CERNER_VALIDATION_ERROR"),
    }

    # ---- Shared helpers ----------------------------------------------------
    def _base_url(self) -> str:
        return self.secret_provider.get_secret("cerner_fhir_base_url").rstrip("/")

    def _same_server(self, url: str) -> str:
        """The url, if it is on the configured Cerner server; otherwise the auth header would leak."""
        got, want = urlsplit(url), urlsplit(self._base_url())
        if (got.scheme, got.hostname, got.port) != (want.scheme, want.hostname, want.port):
            raise ValueError(
                "the url must be on the configured Cerner server (cerner_fhir_base_url)"
            )
        return url

    async def _auth_headers(self) -> Dict[str, str]:
        # A token URL with '/hosts/' is a malformed sandbox URL that only ever answers 401.
        try:
            token_url = self.secret_provider.get_secret("cerner_token_url")
        except Exception:
            token_url = None
        if token_url and "/hosts/" in token_url:
            raise ValueError(
                "Cerner token_url must not contain '/hosts/' (found in secret). Use the "
                "'smart-v1/token' endpoint, e.g. "
                "https://authorization.cerner.com/tenants/{tenant}/protocols/oauth2/profiles/smart-v1/token"
            )
        return await self.get_auth_headers()

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
        auth: bool = True,
        redirect: bool = False,
    ) -> httpx.Response:
        """One request. ``repeatable``: a retry of this call is harmless (default: GET and DELETE).

        ``max_bytes``: read the answer in chunks and refuse one that is larger. ``auth=False``: send no
        Authorization header (a download link). ``redirect``: a 3xx answer is returned, not an error.
        """
        merged: Dict[str, str] = {"Accept": "application/fhir+json"}
        if auth:
            merged.update(await self._auth_headers())
        merged.update(headers or {})
        if body is not None:
            merged.setdefault("Content-Type", "application/fhir+json")
        if repeatable is None:
            repeatable = method in ("GET", "DELETE")
        # Only the method and the URL path are logged, never query values or bodies (patient data).
        logger.info(
            "Calling Cerner FHIR",
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
            if not (redirect and response.is_redirect):
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log_http_status_error(
                logger,
                "Cerner FHIR request failed",
                exc,
                trace_id=trace_id,
                connector_id=self.connector_id,
            )
            raise _status_error(method, exc) from exc
        except httpx.TimeoutException as exc:
            # A connect or pool timeout means nothing was sent, so a retry is always safe.
            sent = not isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout))
            logger.error(
                "Cerner FHIR request failed | error=%s",
                type(exc).__name__,
                extra={"trace_id": trace_id, "connector_id": self.connector_id},
            )
            if sent and not repeatable:
                raise _WriteTimedOut(str(exc) or "timed out after the request was sent") from exc
            raise
        except Exception as exc:
            logger.error(
                "Cerner FHIR request failed | error=%s",
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
                        "narrow the export with type, since and type_filter"
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

    async def _read(self, rtype: str, rid: str, trace_id: str) -> FhirCernerOperationOutput:
        url = f"{self._base_url()}/{rtype}/{quote(rid, safe='')}"
        response = await self._send("GET", url, trace_id)
        return FhirCernerOperationOutput(resource=response.json(), etag=_etag(response))

    async def _search(self, rtype: str, params: Any, trace_id: str) -> FhirCernerOperationOutput:
        response = await self._send(
            "GET", f"{self._base_url()}/{rtype}", trace_id, query=self._query(params)
        )
        return self._page(response)

    @staticmethod
    def _page(response: httpx.Response) -> FhirCernerOperationOutput:
        resources, total, nxt = _bundle(response.json())
        return FhirCernerOperationOutput(resources=resources, total=total, next_url=nxt)

    async def _create(self, rtype: str, params: Any, trace_id: str) -> FhirCernerOperationOutput:
        response = await self._send(
            "POST", f"{self._base_url()}/{rtype}", trace_id, body=params.resource
        )
        return self._created(rtype, response)

    async def _update(self, rtype: str, params: Any, trace_id: str) -> FhirCernerOperationOutput:
        url = f"{self._base_url()}/{rtype}/{quote(params.resource_id, safe='')}"
        # A repeated PUT writes the same state, so a timeout after it was sent may be retried.
        response = await self._send(
            "PUT",
            url,
            trace_id,
            body=params.resource,
            headers={"If-Match": _if_match(params.if_match)},
            repeatable=True,
        )
        body = self._json(response)
        return FhirCernerOperationOutput(
            resource_id=params.resource_id,
            resource=body if isinstance(body, dict) else None,
            etag=_etag(response),
        )

    async def _patch(self, rtype: str, params: Any, trace_id: str) -> FhirCernerOperationOutput:
        url = f"{self._base_url()}/{rtype}/{quote(params.resource_id, safe='')}"
        # A patch (an add, say) is not safe to repeat, so a timeout after it was sent is not retried.
        response = await self._send(
            "PATCH",
            url,
            trace_id,
            body=params.operations,
            headers={"If-Match": _if_match(params.if_match), "Content-Type": _PATCH_JSON},
            repeatable=False,
        )
        body = self._json(response)
        return FhirCernerOperationOutput(
            resource_id=params.resource_id,
            resource=body if isinstance(body, dict) else None,
            etag=_etag(response),
        )

    async def _delete(self, rtype: str, params: Any, trace_id: str) -> FhirCernerOperationOutput:
        url = f"{self._base_url()}/{rtype}/{quote(params.resource_id, safe='')}"
        response = await self._send(
            "DELETE", url, trace_id, headers={"If-Match": _if_match(params.if_match)}
        )
        return FhirCernerOperationOutput(
            resource_id=params.resource_id, status_code=response.status_code
        )

    @staticmethod
    def _created(rtype: str, response: httpx.Response) -> FhirCernerOperationOutput:
        """The id and, when Cerner returns it, the body of a created resource."""
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
        return FhirCernerOperationOutput(
            resource_id=resource_id, resource=body or None, etag=_etag(response)
        )

    async def _operation(
        self, method: str, path: str, trace_id: str, **kwargs: Any
    ) -> FhirCernerOperationOutput:
        """An operation whose answer is one resource (a Bundle, a Parameters or a Binary)."""
        response = await self._send(method, f"{self._base_url()}{path}", trace_id, **kwargs)
        body = self._json(response)
        return FhirCernerOperationOutput(
            resource=body if isinstance(body, dict) else None, status_code=response.status_code
        )

    # ---- Wave 0: the original five actions ---------------------------------
    @sdk_action("read_patient", alias_tolerant=True, mcp_normalize=normalize_fhir_read_patient)
    async def read_patient(
        self, params: FhirCernerPatientReadInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
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
        return FhirCernerOperationOutput(resource=data)

    @sdk_action(
        "search_patients", alias_tolerant=True, mcp_normalize=normalize_fhir_search_patients
    )
    async def search_patients(
        self, params: FhirCernerPatientSearchInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        base = self._base_url()
        if params.resource_ids:
            ids = [rid.strip() for rid in params.resource_ids if rid.strip()]
            if not ids:
                raise ValueError("resource_ids list is empty")

            # At most 5 requests at a time, so 50 ids do not open 50 connections or trip Cerner's rate limit.
            limit = asyncio.Semaphore(5)

            async def fetch(rid: str) -> tuple[str, Optional[Dict[str, Any]], Optional[str]]:
                async with limit:
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
            return FhirCernerOperationOutput(resources=found, total=len(found), errors=errors)

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
        self, params: FhirCernerEncounterSearchInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
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
        self, params: FhirCernerDocumentReferenceCreateInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        # Cerner strictly requires a charset (lowercase, no space) for text types; without it the answer is
        # 422 "a character set must be specified".
        content_type = (params.content_type or "text/plain").strip().lower()
        if content_type.startswith("text/"):
            content_type = content_type.replace(" ", "")
            if "charset=" not in content_type:
                content_type = f"{content_type};charset=utf-8"

        attachment: Dict[str, Any] = {"contentType": content_type}
        if params.data:
            attachment["data"] = params.data
        elif params.text:
            attachment["data"] = base64.b64encode(params.text.encode("utf-8")).decode("ascii")
        else:
            raise ValueError("Either 'text' or 'data' must be provided")

        # Cerner requires title and creation on the attachment.
        if not params.attachment_title:
            raise ValueError("Cerner requires 'attachment_title' on DocumentReference create.")
        attachment["title"] = params.attachment_title
        attachment["creation"] = params.attachment_creation or datetime.now(
            tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        doc_ref: Dict[str, Any] = {
            "resourceType": "DocumentReference",
            "status": params.status,
            "docStatus": params.doc_status or "final",  # required by Cerner on create
            "type": params.type,
            "subject": {"reference": params.subject},
            "content": [{"attachment": attachment}],
        }
        # Cerner requires every type.coding to have a 'display' and 'userSelected: true', the type itself to
        # have a top-level 'text', and the proprietary CodeSet 72 system rather than LOINC.
        codings = doc_ref["type"].get("coding", [])
        for coding in codings:
            system_host = urlparse(coding.get("system", "")).hostname or ""
            if system_host == "loinc.org" or system_host.endswith(".loinc.org"):
                raise ValueError(
                    "Cerner requires the proprietary CodeSet 72 system for DocumentReference 'type', "
                    "not a LOINC system URL. "
                    "Use: 'https://fhir.cerner.com/{tenant_id}/codeSet/72' with a valid CodeSet 72 code."
                )
            if not coding.get("display"):
                raise ValueError(
                    "Cerner requires 'type.coding[].display' on DocumentReference create. "
                    f"Add a display value for code '{coding.get('code')}'."
                )
            if coding.get("userSelected") is None:
                coding["userSelected"] = True
        if not doc_ref["type"].get("text") and codings:
            doc_ref["type"]["text"] = codings[0].get("display", "")

        # Only what the caller passed: the Cerner examples have no category, 'description' can be rejected
        # depending on the tenant, and 'identifier' is not supported on create.
        if params.category:
            doc_ref["category"] = params.category
        if params.author:
            doc_ref["author"] = params.author
        # Cerner requires an authenticator on create.
        if params.authenticator:
            doc_ref["authenticator"] = params.authenticator
        elif params.author:
            doc_ref["authenticator"] = params.author[0]
        if params.custodian:
            doc_ref["custodian"] = params.custodian
        if params.context:
            context = dict(params.context)
            # Cerner requires context.period whenever context.encounter is set: a one-hour window from now.
            if context.get("encounter") and not context.get("period"):
                start = datetime.now(tz=timezone.utc)
                context["period"] = {
                    "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                }
            doc_ref["context"] = context
        if params.additional_fields:
            doc_ref.update(params.additional_fields)
        # Cerner answers 422 to an unknown root field, so none of the connector's own fields may stay.
        for field in (
            "text",
            "data",
            "content_type",
            "attachment_title",
            "attachment_creation",
            "doc_status",
        ):
            doc_ref.pop(field, None)
        # Cerner requires at least one author for clinical note document types.
        if not params.author:
            raise ValueError(
                "Cerner requires 'author' for clinical note document types. "
                "Provide at least one author reference, e.g. [{'reference': 'Practitioner/{id}'}]"
            )

        try:
            response = await self._send(
                "POST", f"{self._base_url()}/DocumentReference", trace_id, body=doc_ref
            )
        except httpx.HTTPStatusError:
            # The shape of the body, never its content, helps to see why Cerner refused it.
            logger.error(
                "FHIR DocumentReference create failed | payload_summary=%s",
                json.dumps(_doc_summary(doc_ref)),
                extra={"trace_id": trace_id, "connector_id": self.connector_id},
            )
            raise
        return self._created("DocumentReference", response)

    @nw_action("search_document_reference")
    async def search_document_reference(
        self, params: FhirCernerDocumentReferenceSearchInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        response = await self._send(
            "GET", f"{self._base_url()}/DocumentReference", trace_id, query=params.search_params
        )
        return self._page(response)

    # ---- Operations --------------------------------------------------------
    @nw_action("fetch_next_page", http_method="GET", http_path="/{next link}")
    async def fetch_next_page(
        self, params: FetchNextPageInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        response = await self._send("GET", self._same_server(params.url), trace_id)
        return self._page(response)

    @nw_action("get_capability_statement", http_method="GET", http_path="/metadata")
    async def get_capability_statement(
        self, params: GetCapabilityStatementInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._operation("GET", "/metadata", trace_id)

    @nw_action(
        "get_smart_configuration", http_method="GET", http_path="/.well-known/smart-configuration"
    )
    async def get_smart_configuration(
        self, params: GetSmartConfigurationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._operation(
            "GET",
            "/.well-known/smart-configuration",
            trace_id,
            headers={"Accept": "application/json"},
        )

    @nw_action("get_ccd", http_method="GET", http_path="/Binary/$autogen-ccd-if")
    async def get_ccd(self, params: GetCcdInput, *, trace_id: str) -> FhirCernerOperationOutput:
        query = {
            k: v
            for k, v in (
                ("patient", params.patient),
                ("encounter", params.encounter),
                ("start", params.start),
                ("end", params.end),
            )
            if v
        }
        url = f"{self._base_url()}/Binary/$autogen-ccd-if"
        if params.xml:
            response = await self._send(
                "GET", url, trace_id, query=query, headers={"Accept": "application/xml"}
            )
            return FhirCernerOperationOutput(
                status_code=response.status_code, content=response.text
            )
        response = await self._send("GET", url, trace_id, query=query)
        return FhirCernerOperationOutput(resource=response.json(), status_code=response.status_code)

    @nw_action("docref", http_method="GET", http_path="/DocumentReference/$docref")
    async def docref(self, params: DocrefInput, *, trace_id: str) -> FhirCernerOperationOutput:
        query = {
            k: v
            for k, v in (("patient", params.patient), ("start", params.start), ("end", params.end))
            if v
        }
        response = await self._send(
            "GET", f"{self._base_url()}/DocumentReference/$docref", trace_id, query=query
        )
        return self._page(response)

    @nw_action(
        "get_caregiver_organizations", http_method="GET", http_path="/Organization/$get-cg-for-mrcu"
    )
    async def get_caregiver_organizations(
        self, params: GetCaregiverOrganizationsInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        response = await self._send(
            "GET",
            f"{self._base_url()}/Organization/$get-cg-for-mrcu",
            trace_id,
            query={"_id": params.resource_id},
        )
        return self._page(response)

    @nw_action(
        "health_cards_issue", http_method="POST", http_path="/Patient/{id}/$health-cards-issue"
    )
    async def health_cards_issue(
        self, params: HealthCardsIssueInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        path = f"/Patient/{quote(params.resource_id, safe='')}/$health-cards-issue"
        # Issuing only reads the chart, so a timeout may be retried.
        return await self._operation(
            "POST", path, trace_id, body=params.parameters, repeatable=True
        )

    @nw_action("charge_item_create", http_method="POST", http_path="/ChargeItem/$create")
    async def charge_item_create(
        self, params: ChargeItemCreateInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._operation(
            "POST", "/ChargeItem/$create", trace_id, body=params.parameters
        )

    @nw_action("charge_item_credit", http_method="POST", http_path="/ChargeItem/{id}/$credit")
    async def charge_item_credit(
        self, params: ChargeItemCreditInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        path = f"/ChargeItem/{quote(params.resource_id, safe='')}/$credit"
        return await self._operation("POST", path, trace_id, body=params.parameters)

    @nw_action("charge_item_modify", http_method="POST", http_path="/ChargeItem/$modify")
    async def charge_item_modify(
        self, params: ChargeItemModifyInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._operation(
            "POST", "/ChargeItem/$modify", trace_id, body=params.parameters
        )

    @nw_action("create_financial_transaction", http_method="POST", http_path="/Basic")
    async def create_financial_transaction(
        self, params: CreateFinancialTransactionInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Basic", params, trace_id)

    @nw_action("execute_batch", http_method="POST", http_path="/")
    async def execute_batch(
        self, params: ExecuteBatchInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        # A batch can hold writes, so a timeout after it was sent is not retried.
        return await self._operation("POST", "", trace_id, body=params.bundle, repeatable=False)

    async def _bulk_kickoff(
        self,
        method: str,
        path: str,
        trace_id: str,
        *,
        query: Any = None,
        body: Any = None,
        lenient: bool,
    ) -> FhirCernerOperationOutput:
        headers = {"Prefer": "respond-async, handling=lenient" if lenient else "respond-async"}
        # Cerner allows one export in progress per app and tenant, so a lost answer is not retried blindly.
        response = await self._send(
            method,
            f"{self._base_url()}{path}",
            trace_id,
            query=query,
            body=body,
            headers=headers,
            repeatable=False,
        )
        return FhirCernerOperationOutput(
            status_code=response.status_code,
            status_url=response.headers.get("Content-Location"),
        )

    @nw_action("bulk_export_group", http_method="GET", http_path="/Group/{id}/$export")
    async def bulk_export_group(
        self, params: BulkExportGroupInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        query = {
            k: v
            for k, v in (
                ("_type", params.type),
                ("_since", params.since),
                ("includeAssociatedData", params.include_associated_data),
                ("_typeFilter", params.type_filter),
            )
            if v
        }
        path = f"/Group/{quote(params.resource_id, safe='')}/$export"
        return await self._bulk_kickoff("GET", path, trace_id, query=query, lenient=params.lenient)

    @nw_action("bulk_export_patients", http_method="POST", http_path="/Patient/$export")
    async def bulk_export_patients(
        self, params: BulkExportPatientsInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        parts: List[Dict[str, Any]] = [
            {"name": "patient", "valueReference": {"reference": f"Patient/{pid}"}}
            for pid in params.patients
        ]
        for name, kind, value in (
            ("_type", "valueString", params.type),
            ("_typeFilter", "valueString", params.type_filter),
            ("includeAssociatedData", "valueCode", params.include_associated_data),
            ("_since", "valueInstant", params.since),
        ):
            if value:
                parts.append({"name": name, kind: value})
        body = {"resourceType": "Parameters", "parameter": parts}
        return await self._bulk_kickoff(
            "POST", "/Patient/$export", trace_id, body=body, lenient=params.lenient
        )

    @nw_action("bulk_export_status", http_method="GET", http_path="/bulk-export/jobs/{id}")
    async def bulk_export_status(
        self, params: BulkExportStatusInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        response = await self._send("GET", self._same_server(params.status_url), trace_id)
        body = self._json(response)
        return FhirCernerOperationOutput(
            status_code=response.status_code,
            progress=response.headers.get("X-Progress"),
            resource=body if isinstance(body, dict) else None,
        )

    @nw_action("bulk_export_file", http_method="GET", http_path="/bulk-export/files/{id}")
    async def bulk_export_file(
        self, params: BulkExportFileInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        ndjson = {"Accept": "application/fhir+ndjson"}
        response = await self._send(
            "GET",
            self._same_server(params.file_url),
            trace_id,
            headers=ndjson,
            max_bytes=_MAX_BULK_FILE_BYTES,
            redirect=True,
        )
        if response.is_redirect:
            # Cerner answers 307 with the download link. That link is not Cerner's FHIR server, so the
            # Authorization header must not follow it.
            target = response.headers.get("Location", "")
            if urlsplit(target).scheme != "https":
                raise ValueError("the file download link Cerner returned is not https")
            # The link names a host of Cerner's choosing: refuse a private, loopback or link-local one, as the
            # runtime does for every other outbound call.
            await assert_safe_destination(target)
            response = await self._send(
                "GET", target, trace_id, headers=ndjson, max_bytes=_MAX_BULK_FILE_BYTES, auth=False
            )
        return FhirCernerOperationOutput(status_code=response.status_code, content=response.text)

    @nw_action("bulk_export_delete", http_method="DELETE", http_path="/bulk-export/jobs/{id}")
    async def bulk_export_delete(
        self, params: BulkExportDeleteInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        response = await self._send("DELETE", self._same_server(params.status_url), trace_id)
        return FhirCernerOperationOutput(status_code=response.status_code)

    # ---- Generated, one per Cerner read, search, create, update, patch and delete ----------

    # ---- Wave 1: clinical ----------------------------------------
    @nw_action("read_allergy_intolerance", http_method="GET", http_path="/AllergyIntolerance/{id}")
    async def read_allergy_intolerance(
        self, params: ReadAllergyIntoleranceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("AllergyIntolerance", params.resource_id, trace_id)

    @nw_action("search_allergy_intolerance", http_method="GET", http_path="/AllergyIntolerance")
    async def search_allergy_intolerance(
        self, params: SearchAllergyIntoleranceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("AllergyIntolerance", params, trace_id)

    @nw_action("read_care_plan", http_method="GET", http_path="/CarePlan/{id}")
    async def read_care_plan(
        self, params: ReadCarePlanInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("CarePlan", params.resource_id, trace_id)

    @nw_action("search_care_plan", http_method="GET", http_path="/CarePlan")
    async def search_care_plan(
        self, params: SearchCarePlanInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("CarePlan", params, trace_id)

    @nw_action("read_care_team", http_method="GET", http_path="/CareTeam/{id}")
    async def read_care_team(
        self, params: ReadCareTeamInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("CareTeam", params.resource_id, trace_id)

    @nw_action("search_care_team", http_method="GET", http_path="/CareTeam")
    async def search_care_team(
        self, params: SearchCareTeamInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("CareTeam", params, trace_id)

    @nw_action("read_condition", http_method="GET", http_path="/Condition/{id}")
    async def read_condition(
        self, params: ReadConditionInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Condition", params.resource_id, trace_id)

    @nw_action("search_condition", http_method="GET", http_path="/Condition")
    async def search_condition(
        self, params: SearchConditionInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Condition", params, trace_id)

    @nw_action("read_device", http_method="GET", http_path="/Device/{id}")
    async def read_device(
        self, params: ReadDeviceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Device", params.resource_id, trace_id)

    @nw_action("search_device", http_method="GET", http_path="/Device")
    async def search_device(
        self, params: SearchDeviceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Device", params, trace_id)

    @nw_action("read_diagnostic_report", http_method="GET", http_path="/DiagnosticReport/{id}")
    async def read_diagnostic_report(
        self, params: ReadDiagnosticReportInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("DiagnosticReport", params.resource_id, trace_id)

    @nw_action("search_diagnostic_report", http_method="GET", http_path="/DiagnosticReport")
    async def search_diagnostic_report(
        self, params: SearchDiagnosticReportInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("DiagnosticReport", params, trace_id)

    @nw_action("read_document_reference", http_method="GET", http_path="/DocumentReference/{id}")
    async def read_document_reference(
        self, params: ReadDocumentReferenceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("DocumentReference", params.resource_id, trace_id)

    @nw_action("read_encounter", http_method="GET", http_path="/Encounter/{id}")
    async def read_encounter(
        self, params: ReadEncounterInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Encounter", params.resource_id, trace_id)

    @nw_action("read_goal", http_method="GET", http_path="/Goal/{id}")
    async def read_goal(self, params: ReadGoalInput, *, trace_id: str) -> FhirCernerOperationOutput:
        return await self._read("Goal", params.resource_id, trace_id)

    @nw_action("search_goal", http_method="GET", http_path="/Goal")
    async def search_goal(
        self, params: SearchGoalInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Goal", params, trace_id)

    @nw_action("read_immunization", http_method="GET", http_path="/Immunization/{id}")
    async def read_immunization(
        self, params: ReadImmunizationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Immunization", params.resource_id, trace_id)

    @nw_action("search_immunization", http_method="GET", http_path="/Immunization")
    async def search_immunization(
        self, params: SearchImmunizationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Immunization", params, trace_id)

    @nw_action("read_location", http_method="GET", http_path="/Location/{id}")
    async def read_location(
        self, params: ReadLocationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Location", params.resource_id, trace_id)

    @nw_action("search_location", http_method="GET", http_path="/Location")
    async def search_location(
        self, params: SearchLocationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Location", params, trace_id)

    @nw_action("read_medication_request", http_method="GET", http_path="/MedicationRequest/{id}")
    async def read_medication_request(
        self, params: ReadMedicationRequestInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("MedicationRequest", params.resource_id, trace_id)

    @nw_action("search_medication_request", http_method="GET", http_path="/MedicationRequest")
    async def search_medication_request(
        self, params: SearchMedicationRequestInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("MedicationRequest", params, trace_id)

    @nw_action("read_observation", http_method="GET", http_path="/Observation/{id}")
    async def read_observation(
        self, params: ReadObservationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Observation", params.resource_id, trace_id)

    @nw_action("search_observation", http_method="GET", http_path="/Observation")
    async def search_observation(
        self, params: SearchObservationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Observation", params, trace_id)

    @nw_action("read_organization", http_method="GET", http_path="/Organization/{id}")
    async def read_organization(
        self, params: ReadOrganizationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Organization", params.resource_id, trace_id)

    @nw_action("search_organization", http_method="GET", http_path="/Organization")
    async def search_organization(
        self, params: SearchOrganizationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Organization", params, trace_id)

    @nw_action("read_practitioner", http_method="GET", http_path="/Practitioner/{id}")
    async def read_practitioner(
        self, params: ReadPractitionerInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Practitioner", params.resource_id, trace_id)

    @nw_action("search_practitioner", http_method="GET", http_path="/Practitioner")
    async def search_practitioner(
        self, params: SearchPractitionerInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Practitioner", params, trace_id)

    @nw_action("read_procedure", http_method="GET", http_path="/Procedure/{id}")
    async def read_procedure(
        self, params: ReadProcedureInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Procedure", params.resource_id, trace_id)

    @nw_action("search_procedure", http_method="GET", http_path="/Procedure")
    async def search_procedure(
        self, params: SearchProcedureInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Procedure", params, trace_id)

    @nw_action("read_provenance", http_method="GET", http_path="/Provenance/{id}")
    async def read_provenance(
        self, params: ReadProvenanceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Provenance", params.resource_id, trace_id)

    @nw_action("search_provenance", http_method="GET", http_path="/Provenance")
    async def search_provenance(
        self, params: SearchProvenanceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Provenance", params, trace_id)

    @nw_action("read_service_request", http_method="GET", http_path="/ServiceRequest/{id}")
    async def read_service_request(
        self, params: ReadServiceRequestInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("ServiceRequest", params.resource_id, trace_id)

    @nw_action("search_service_request", http_method="GET", http_path="/ServiceRequest")
    async def search_service_request(
        self, params: SearchServiceRequestInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("ServiceRequest", params, trace_id)

    @nw_action("read_specimen", http_method="GET", http_path="/Specimen/{id}")
    async def read_specimen(
        self, params: ReadSpecimenInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Specimen", params.resource_id, trace_id)

    @nw_action("search_specimen", http_method="GET", http_path="/Specimen")
    async def search_specimen(
        self, params: SearchSpecimenInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Specimen", params, trace_id)

    # ---- Wave 2: other resources ---------------------------------
    @nw_action("read_account", http_method="GET", http_path="/Account/{id}")
    async def read_account(
        self, params: ReadAccountInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Account", params.resource_id, trace_id)

    @nw_action("search_account", http_method="GET", http_path="/Account")
    async def search_account(
        self, params: SearchAccountInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Account", params, trace_id)

    @nw_action("read_appointment", http_method="GET", http_path="/Appointment/{id}")
    async def read_appointment(
        self, params: ReadAppointmentInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Appointment", params.resource_id, trace_id)

    @nw_action("search_appointment", http_method="GET", http_path="/Appointment")
    async def search_appointment(
        self, params: SearchAppointmentInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Appointment", params, trace_id)

    @nw_action("read_charge_item", http_method="GET", http_path="/ChargeItem/{id}")
    async def read_charge_item(
        self, params: ReadChargeItemInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("ChargeItem", params.resource_id, trace_id)

    @nw_action("search_charge_item", http_method="GET", http_path="/ChargeItem")
    async def search_charge_item(
        self, params: SearchChargeItemInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("ChargeItem", params, trace_id)

    @nw_action("read_communication", http_method="GET", http_path="/Communication/{id}")
    async def read_communication(
        self, params: ReadCommunicationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Communication", params.resource_id, trace_id)

    @nw_action("search_communication", http_method="GET", http_path="/Communication")
    async def search_communication(
        self, params: SearchCommunicationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Communication", params, trace_id)

    @nw_action("read_consent", http_method="GET", http_path="/Consent/{id}")
    async def read_consent(
        self, params: ReadConsentInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Consent", params.resource_id, trace_id)

    @nw_action("search_consent", http_method="GET", http_path="/Consent")
    async def search_consent(
        self, params: SearchConsentInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Consent", params, trace_id)

    @nw_action("read_coverage", http_method="GET", http_path="/Coverage/{id}")
    async def read_coverage(
        self, params: ReadCoverageInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Coverage", params.resource_id, trace_id)

    @nw_action("search_coverage", http_method="GET", http_path="/Coverage")
    async def search_coverage(
        self, params: SearchCoverageInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Coverage", params, trace_id)

    @nw_action(
        "read_family_member_history", http_method="GET", http_path="/FamilyMemberHistory/{id}"
    )
    async def read_family_member_history(
        self, params: ReadFamilyMemberHistoryInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("FamilyMemberHistory", params.resource_id, trace_id)

    @nw_action("search_family_member_history", http_method="GET", http_path="/FamilyMemberHistory")
    async def search_family_member_history(
        self, params: SearchFamilyMemberHistoryInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("FamilyMemberHistory", params, trace_id)

    @nw_action("read_insurance_plan", http_method="GET", http_path="/InsurancePlan/{id}")
    async def read_insurance_plan(
        self, params: ReadInsurancePlanInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("InsurancePlan", params.resource_id, trace_id)

    @nw_action("search_insurance_plan", http_method="GET", http_path="/InsurancePlan")
    async def search_insurance_plan(
        self, params: SearchInsurancePlanInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("InsurancePlan", params, trace_id)

    @nw_action(
        "read_medication_administration",
        http_method="GET",
        http_path="/MedicationAdministration/{id}",
    )
    async def read_medication_administration(
        self, params: ReadMedicationAdministrationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("MedicationAdministration", params.resource_id, trace_id)

    @nw_action(
        "search_medication_administration", http_method="GET", http_path="/MedicationAdministration"
    )
    async def search_medication_administration(
        self, params: SearchMedicationAdministrationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("MedicationAdministration", params, trace_id)

    @nw_action("read_medication_dispense", http_method="GET", http_path="/MedicationDispense/{id}")
    async def read_medication_dispense(
        self, params: ReadMedicationDispenseInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("MedicationDispense", params.resource_id, trace_id)

    @nw_action("search_medication_dispense", http_method="GET", http_path="/MedicationDispense")
    async def search_medication_dispense(
        self, params: SearchMedicationDispenseInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("MedicationDispense", params, trace_id)

    @nw_action("read_nutrition_order", http_method="GET", http_path="/NutritionOrder/{id}")
    async def read_nutrition_order(
        self, params: ReadNutritionOrderInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("NutritionOrder", params.resource_id, trace_id)

    @nw_action("search_nutrition_order", http_method="GET", http_path="/NutritionOrder")
    async def search_nutrition_order(
        self, params: SearchNutritionOrderInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("NutritionOrder", params, trace_id)

    @nw_action("read_person", http_method="GET", http_path="/Person/{id}")
    async def read_person(
        self, params: ReadPersonInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Person", params.resource_id, trace_id)

    @nw_action("search_person", http_method="GET", http_path="/Person")
    async def search_person(
        self, params: SearchPersonInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Person", params, trace_id)

    @nw_action("read_questionnaire", http_method="GET", http_path="/Questionnaire/{id}")
    async def read_questionnaire(
        self, params: ReadQuestionnaireInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Questionnaire", params.resource_id, trace_id)

    @nw_action("search_questionnaire", http_method="GET", http_path="/Questionnaire")
    async def search_questionnaire(
        self, params: SearchQuestionnaireInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Questionnaire", params, trace_id)

    @nw_action(
        "read_questionnaire_response", http_method="GET", http_path="/QuestionnaireResponse/{id}"
    )
    async def read_questionnaire_response(
        self, params: ReadQuestionnaireResponseInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("QuestionnaireResponse", params.resource_id, trace_id)

    @nw_action(
        "search_questionnaire_response", http_method="GET", http_path="/QuestionnaireResponse"
    )
    async def search_questionnaire_response(
        self, params: SearchQuestionnaireResponseInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("QuestionnaireResponse", params, trace_id)

    @nw_action("read_related_person", http_method="GET", http_path="/RelatedPerson/{id}")
    async def read_related_person(
        self, params: ReadRelatedPersonInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("RelatedPerson", params.resource_id, trace_id)

    @nw_action("search_related_person", http_method="GET", http_path="/RelatedPerson")
    async def search_related_person(
        self, params: SearchRelatedPersonInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("RelatedPerson", params, trace_id)

    @nw_action("read_schedule", http_method="GET", http_path="/Schedule/{id}")
    async def read_schedule(
        self, params: ReadScheduleInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Schedule", params.resource_id, trace_id)

    @nw_action("search_schedule", http_method="GET", http_path="/Schedule")
    async def search_schedule(
        self, params: SearchScheduleInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Schedule", params, trace_id)

    @nw_action("read_slot", http_method="GET", http_path="/Slot/{id}")
    async def read_slot(self, params: ReadSlotInput, *, trace_id: str) -> FhirCernerOperationOutput:
        return await self._read("Slot", params.resource_id, trace_id)

    @nw_action("search_slot", http_method="GET", http_path="/Slot")
    async def search_slot(
        self, params: SearchSlotInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._search("Slot", params, trace_id)

    @nw_action("read_binary", http_method="GET", http_path="/Binary/{id}")
    async def read_binary(
        self, params: ReadBinaryInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Binary", params.resource_id, trace_id)

    @nw_action("read_media", http_method="GET", http_path="/Media/{id}")
    async def read_media(
        self, params: ReadMediaInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("Media", params.resource_id, trace_id)

    @nw_action(
        "read_operation_definition", http_method="GET", http_path="/OperationDefinition/{id}"
    )
    async def read_operation_definition(
        self, params: ReadOperationDefinitionInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("OperationDefinition", params.resource_id, trace_id)

    @nw_action(
        "read_structure_definition", http_method="GET", http_path="/StructureDefinition/{id}"
    )
    async def read_structure_definition(
        self, params: ReadStructureDefinitionInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._read("StructureDefinition", params.resource_id, trace_id)

    # ---- Wave 3: writes ------------------------------------------
    @nw_action("create_allergy_intolerance", http_method="POST", http_path="/AllergyIntolerance")
    async def create_allergy_intolerance(
        self, params: CreateAllergyIntoleranceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("AllergyIntolerance", params, trace_id)

    @nw_action("create_appointment", http_method="POST", http_path="/Appointment")
    async def create_appointment(
        self, params: CreateAppointmentInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Appointment", params, trace_id)

    @nw_action("create_communication", http_method="POST", http_path="/Communication")
    async def create_communication(
        self, params: CreateCommunicationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Communication", params, trace_id)

    @nw_action("create_condition", http_method="POST", http_path="/Condition")
    async def create_condition(
        self, params: CreateConditionInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Condition", params, trace_id)

    @nw_action("create_coverage", http_method="POST", http_path="/Coverage")
    async def create_coverage(
        self, params: CreateCoverageInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Coverage", params, trace_id)

    @nw_action("create_diagnostic_report", http_method="POST", http_path="/DiagnosticReport")
    async def create_diagnostic_report(
        self, params: CreateDiagnosticReportInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("DiagnosticReport", params, trace_id)

    @nw_action("create_encounter", http_method="POST", http_path="/Encounter")
    async def create_encounter(
        self, params: CreateEncounterInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Encounter", params, trace_id)

    @nw_action("create_family_member_history", http_method="POST", http_path="/FamilyMemberHistory")
    async def create_family_member_history(
        self, params: CreateFamilyMemberHistoryInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("FamilyMemberHistory", params, trace_id)

    @nw_action("create_immunization", http_method="POST", http_path="/Immunization")
    async def create_immunization(
        self, params: CreateImmunizationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Immunization", params, trace_id)

    @nw_action("create_medication_request", http_method="POST", http_path="/MedicationRequest")
    async def create_medication_request(
        self, params: CreateMedicationRequestInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("MedicationRequest", params, trace_id)

    @nw_action("create_observation", http_method="POST", http_path="/Observation")
    async def create_observation(
        self, params: CreateObservationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Observation", params, trace_id)

    @nw_action("create_organization", http_method="POST", http_path="/Organization")
    async def create_organization(
        self, params: CreateOrganizationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Organization", params, trace_id)

    @nw_action("create_patient", http_method="POST", http_path="/Patient")
    async def create_patient(
        self, params: CreatePatientInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Patient", params, trace_id)

    @nw_action("create_practitioner", http_method="POST", http_path="/Practitioner")
    async def create_practitioner(
        self, params: CreatePractitionerInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Practitioner", params, trace_id)

    @nw_action("create_procedure", http_method="POST", http_path="/Procedure")
    async def create_procedure(
        self, params: CreateProcedureInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Procedure", params, trace_id)

    @nw_action("create_provenance", http_method="POST", http_path="/Provenance")
    async def create_provenance(
        self, params: CreateProvenanceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("Provenance", params, trace_id)

    @nw_action("create_related_person", http_method="POST", http_path="/RelatedPerson")
    async def create_related_person(
        self, params: CreateRelatedPersonInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._create("RelatedPerson", params, trace_id)

    @nw_action(
        "update_allergy_intolerance", http_method="PUT", http_path="/AllergyIntolerance/{id}"
    )
    async def update_allergy_intolerance(
        self, params: UpdateAllergyIntoleranceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._update("AllergyIntolerance", params, trace_id)

    @nw_action("update_condition", http_method="PUT", http_path="/Condition/{id}")
    async def update_condition(
        self, params: UpdateConditionInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._update("Condition", params, trace_id)

    @nw_action("update_document_reference", http_method="PUT", http_path="/DocumentReference/{id}")
    async def update_document_reference(
        self, params: UpdateDocumentReferenceInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._update("DocumentReference", params, trace_id)

    @nw_action(
        "update_family_member_history", http_method="PUT", http_path="/FamilyMemberHistory/{id}"
    )
    async def update_family_member_history(
        self, params: UpdateFamilyMemberHistoryInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._update("FamilyMemberHistory", params, trace_id)

    @nw_action("update_immunization", http_method="PUT", http_path="/Immunization/{id}")
    async def update_immunization(
        self, params: UpdateImmunizationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._update("Immunization", params, trace_id)

    @nw_action("update_observation", http_method="PUT", http_path="/Observation/{id}")
    async def update_observation(
        self, params: UpdateObservationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._update("Observation", params, trace_id)

    @nw_action(
        "update_questionnaire_response", http_method="PUT", http_path="/QuestionnaireResponse/{id}"
    )
    async def update_questionnaire_response(
        self, params: UpdateQuestionnaireResponseInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._update("QuestionnaireResponse", params, trace_id)

    @nw_action("patch_appointment", http_method="PATCH", http_path="/Appointment/{id}")
    async def patch_appointment(
        self, params: PatchAppointmentInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._patch("Appointment", params, trace_id)

    @nw_action("patch_communication", http_method="PATCH", http_path="/Communication/{id}")
    async def patch_communication(
        self, params: PatchCommunicationInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._patch("Communication", params, trace_id)

    @nw_action("patch_coverage", http_method="PATCH", http_path="/Coverage/{id}")
    async def patch_coverage(
        self, params: PatchCoverageInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._patch("Coverage", params, trace_id)

    @nw_action("patch_encounter", http_method="PATCH", http_path="/Encounter/{id}")
    async def patch_encounter(
        self, params: PatchEncounterInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._patch("Encounter", params, trace_id)

    @nw_action("patch_medication_request", http_method="PATCH", http_path="/MedicationRequest/{id}")
    async def patch_medication_request(
        self, params: PatchMedicationRequestInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._patch("MedicationRequest", params, trace_id)

    @nw_action("patch_patient", http_method="PATCH", http_path="/Patient/{id}")
    async def patch_patient(
        self, params: PatchPatientInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._patch("Patient", params, trace_id)

    @nw_action("patch_related_person", http_method="PATCH", http_path="/RelatedPerson/{id}")
    async def patch_related_person(
        self, params: PatchRelatedPersonInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._patch("RelatedPerson", params, trace_id)

    @nw_action("patch_slot", http_method="PATCH", http_path="/Slot/{id}")
    async def patch_slot(
        self, params: PatchSlotInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._patch("Slot", params, trace_id)

    @nw_action("delete_coverage", http_method="DELETE", http_path="/Coverage/{id}")
    async def delete_coverage(
        self, params: DeleteCoverageInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        return await self._delete("Coverage", params, trace_id)
