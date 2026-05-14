#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import json

from node_wire_runtime import BaseConnector, nw_action, sdk_action
from node_wire_runtime.fhir_encounter import assert_encounter_query_has_patient
from node_wire_runtime.mcp_normalizers import (
    normalize_fhir_read_patient,
    normalize_fhir_search_encounter,
    normalize_fhir_search_patients,
)

from .schema import (
    FhirDocumentReferenceCreateInput,
    FhirDocumentReferenceCreateOutput,
    FhirDocumentReferenceSearchInput,
    FhirDocumentReferenceSearchOutput,
    FhirEncounterSearchInput,
    FhirEncounterSearchOutput,
    FhirEpicOperationOutput,
    FhirPatientReadInput,
    FhirPatientReadOutput,
    FhirPatientSearchInput,
    FhirPatientSearchOutput,
)

logger = logging.getLogger("connectors.fhir_epic")


def _safe_doc_ref_log_summary(doc_ref: Dict[str, Any]) -> Dict[str, Any]:
    attachment: Dict[str, Any] = {}
    content_items = doc_ref.get("content")
    if isinstance(content_items, list) and content_items:
        first = content_items[0]
        if isinstance(first, dict):
            attachment = (
                first.get("attachment", {}) if isinstance(first.get("attachment"), dict) else {}
            )
    data_value = attachment.get("data")
    data_len = len(data_value) if isinstance(data_value, str) else 0
    return {
        "keys": sorted(doc_ref.keys()),
        "content_items": len(content_items) if isinstance(content_items, list) else 0,
        "attachment_content_type": attachment.get("contentType"),
        "attachment_data_length": data_len,
    }


class FhirEpicConnector(BaseConnector):
    """FHIR/Epic connector: one @nw_action per operation."""

    connector_id = "fhir_epic"
    action = "execute"
    output_model = FhirEpicOperationOutput

    @sdk_action(
        "read_patient",
        alias_tolerant=True,
        mcp_normalize=normalize_fhir_read_patient,
    )
    async def read_patient(
        self, params: FhirPatientReadInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._read_patient(params, trace_id=trace_id)
        return FhirEpicOperationOutput(resource=out.resource)

    @sdk_action(
        "search_patients",
        alias_tolerant=True,
        mcp_normalize=normalize_fhir_search_patients,
    )
    async def search_patients(
        self, params: FhirPatientSearchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._search_patients(params, trace_id=trace_id)
        return FhirEpicOperationOutput(
            resources=out.resources,
            total=out.total,
            errors=out.errors,
        )

    @sdk_action(
        "search_encounter",
        alias_tolerant=True,
        mcp_normalize=normalize_fhir_search_encounter,
    )
    async def search_encounter(
        self, params: FhirEncounterSearchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._search_encounter(params, trace_id=trace_id)
        return FhirEpicOperationOutput(resources=out.resources, total=out.total)

    @nw_action("create_document_reference")
    async def create_document_reference(
        self, params: FhirDocumentReferenceCreateInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._create_document_reference(params, trace_id=trace_id)
        return FhirEpicOperationOutput(resource_id=out.resource_id, resource=out.resource)

    @nw_action("search_document_reference")
    async def search_document_reference(
        self, params: FhirDocumentReferenceSearchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._search_document_reference(params, trace_id=trace_id)
        return FhirEpicOperationOutput(resources=out.resources, total=out.total)

    # ------------------------------------------------------------------
    # Shared helpers — base URL + auth headers via AuthProvider
    # ------------------------------------------------------------------

    def _get_base_url(self) -> str:
        return self.secret_provider.get_secret("epic_fhir_base_url").rstrip("/")

    async def _get_auth_header(self) -> Dict[str, str]:
        """Delegate to the runtime AuthProvider injected by the factory.

        Returns ready-to-use FHIR request headers including the Bearer token.
        Token acquisition, JWT construction, scope resolution and caching are
        all handled by the provider.
        """
        headers = await self.get_auth_headers()
        # Ensure FHIR content types are present if the provider didn't include them (e.g. StaticTokenAuthProvider).
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/fhir+json"
        if "Accept" not in headers:
            headers["Accept"] = "application/fhir+json"

        return headers

    @staticmethod
    def _build_name_search_params(
        given_name: Optional[str],
        family_name: Optional[str],
        name: Optional[str],
        birthdate: Optional[str],
        extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        params: Dict[str, str] = dict(extra or {})

        if given_name and given_name.strip():
            params["given"] = given_name.strip()
        if family_name and family_name.strip():
            params["family"] = family_name.strip()
        if name and name.strip() and "given" not in params and "family" not in params:
            params["name"] = name.strip()
        if birthdate and birthdate.strip():
            params["birthdate"] = birthdate.strip()

        return params

    @staticmethod
    def _build_encounter_search_params(
        patient_id: Optional[str],
        status: Optional[str],
        date: Optional[str],
        extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        params: Dict[str, str] = dict(extra or {})

        if patient_id and patient_id.strip():
            params["patient"] = patient_id.strip()
        if status and status.strip():
            params["status"] = status.strip()
        if date and date.strip():
            params["date"] = date.strip()

        return params

    async def _read_patient(
        self, params: FhirPatientReadInput, *, trace_id: str
    ) -> FhirPatientReadOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()

        if params.resource_id:
            url = f"{base_url}/Patient/{params.resource_id}"
            query_params: Optional[Dict[str, str]] = None
            logger.info(
                "FHIR Patient read by ID",
                extra={"trace_id": trace_id, "resource_id": params.resource_id},
            )
        elif params.given_name or params.family_name or params.name:
            url = f"{base_url}/Patient"
            query_params = self._build_name_search_params(
                params.given_name,
                params.family_name,
                params.name,
                params.birthdate,
                params.search_params,
            )
            logger.info(
                "FHIR Patient read by name fields",
                extra={"trace_id": trace_id, "query_params": query_params},
            )
        elif params.search_params:
            url = f"{base_url}/Patient"
            query_params = params.search_params
            logger.info(
                "FHIR Patient read by search",
                extra={"trace_id": trace_id, "search_params": params.search_params},
            )
        else:
            raise ValueError(
                "Provide resource_id, or name fields (given_name/family_name/name), "
                "or search_params"
            )

        try:
            async with httpx.AsyncClient(
                timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0"))
            ) as client:
                response = await client.get(
                    url,
                    headers=auth_header,
                    params=query_params,
                    timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0")),
                )
                response.raise_for_status()
        except Exception as exc:
            logger.error(
                "FHIR Patient read failed | error=%s: %s",
                type(exc).__name__,
                str(exc),
                extra={"trace_id": trace_id},
            )
            raise

        data = response.json()
        if data.get("resourceType") == "Bundle":
            if data.get("entry"):
                resource = data["entry"][0].get("resource", {})
            else:
                raise ValueError("No patients found in search results")
        else:
            resource = data

        logger.info(
            "FHIR Patient read completed",
            extra={"trace_id": trace_id, "status_code": response.status_code},
        )
        return FhirPatientReadOutput(resource=resource)

    async def _search_patients(
        self, params: FhirPatientSearchInput, *, trace_id: str
    ) -> FhirPatientSearchOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()

        if params.resource_ids:
            ids = [rid.strip() for rid in params.resource_ids if rid.strip()]
            if not ids:
                raise ValueError("resource_ids list is empty")

            logger.info(
                "FHIR Patient multi-ID lookup | count=%s",
                len(ids),
                extra={"trace_id": trace_id, "resource_ids": ids},
            )

            async def _fetch_one(rid: str) -> tuple[str, Optional[Dict[str, Any]], Optional[str]]:
                try:
                    async with httpx.AsyncClient(
                        timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0"))
                    ) as client:
                        resp = await client.get(
                            f"{base_url}/Patient/{rid}",
                            headers=auth_header,
                            timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0")),
                        )
                        resp.raise_for_status()
                    return rid, resp.json(), None
                except Exception as exc:
                    logger.warning(
                        "FHIR Patient fetch failed | resource_id=%s | error=%s",
                        rid,
                        str(exc),
                        extra={"trace_id": trace_id},
                    )
                    return rid, None, str(exc)

            results = await asyncio.gather(*[_fetch_one(rid) for rid in ids])

            resources: List[Dict[str, Any]] = []
            errors: List[Dict[str, Any]] = []
            for rid, resource, error in results:
                if resource is not None:
                    resources.append(resource)
                else:
                    errors.append({"resource_id": rid, "error": error or "Unknown error"})

            logger.info(
                "FHIR Patient multi-ID lookup completed | found=%s | errors=%s",
                len(resources),
                len(errors),
                extra={"trace_id": trace_id},
            )
            return FhirPatientSearchOutput(resources=resources, total=len(resources), errors=errors)

        name_params = self._build_name_search_params(
            params.given_name,
            params.family_name,
            params.name,
            params.birthdate,
            params.search_params,
        )
        if not name_params:
            raise ValueError(
                "Provide resource_ids for multi-ID lookup, or at least one of "
                "given_name / family_name / name / birthdate / search_params for name-based search"
            )

        logger.info(
            "FHIR Patient name search | params=%s",
            name_params,
            extra={"trace_id": trace_id},
        )

        try:
            async with httpx.AsyncClient(
                timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0"))
            ) as client:
                response = await client.get(
                    f"{base_url}/Patient",
                    headers=auth_header,
                    params=name_params,
                    timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0")),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "FHIR Patient name search failed | status=%s | body=%s",
                exc.response.status_code,
                exc.response.text,
                extra={"trace_id": trace_id},
            )
            raise
        except Exception as exc:
            logger.error(
                "FHIR Patient name search failed | error=%s: %s",
                type(exc).__name__,
                str(exc),
                extra={"trace_id": trace_id},
            )
            raise

        data = response.json()
        bundle_resources: List[Dict[str, Any]] = []
        total = data.get("total")
        if data.get("resourceType") == "Bundle" and data.get("entry"):
            bundle_resources = [e["resource"] for e in data["entry"] if "resource" in e]

        logger.info(
            "FHIR Patient name search completed | found=%s | total=%s",
            len(bundle_resources),
            total,
            extra={"trace_id": trace_id},
        )
        return FhirPatientSearchOutput(resources=bundle_resources, total=total)

    async def _search_encounter(
        self, params: FhirEncounterSearchInput, *, trace_id: str
    ) -> FhirEncounterSearchOutput:
        base_url = self._get_base_url()

        if params.patient_id or params.status or params.date:
            query_params = self._build_encounter_search_params(
                params.patient_id, params.status, params.date, params.search_params
            )
            logger.info(
                "FHIR Encounter search by explicit fields",
                extra={"trace_id": trace_id, "query_params": query_params},
            )
        elif params.search_params:
            query_params = params.search_params
            logger.info(
                "FHIR Encounter search by raw params",
                extra={"trace_id": trace_id, "search_params": params.search_params},
            )
        else:
            raise ValueError("Provide at least patient_id, status, date OR search_params")

        assert_encounter_query_has_patient(query_params)

        auth_header = await self._get_auth_header()

        try:
            async with httpx.AsyncClient(
                timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0"))
            ) as client:
                response = await client.get(
                    f"{base_url}/Encounter",
                    headers=auth_header,
                    params=query_params,
                    timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0")),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "FHIR Encounter search failed | status=%s | body=%s",
                exc.response.status_code,
                exc.response.text,
                extra={"trace_id": trace_id},
            )
            raise
        except Exception as exc:
            logger.error(
                "FHIR Encounter search failed | error=%s: %s",
                type(exc).__name__,
                str(exc),
                extra={"trace_id": trace_id},
            )
            raise

        data = response.json()
        resources: list[Dict[str, Any]] = []
        total = data.get("total")
        if data.get("resourceType") == "Bundle" and data.get("entry"):
            resources = [e["resource"] for e in data["entry"] if "resource" in e]

        logger.info(
            "FHIR Encounter search completed | found=%s",
            len(resources),
            extra={"trace_id": trace_id},
        )
        return FhirEncounterSearchOutput(resources=resources, total=total)

    async def _create_document_reference(
        self, params: FhirDocumentReferenceCreateInput, *, trace_id: str
    ) -> FhirDocumentReferenceCreateOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()

        doc_ref: Dict[str, Any] = {
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
        if params.category:
            doc_ref["category"] = params.category
        if params.author:
            doc_ref["author"] = params.author
        if params.description:
            doc_ref["description"] = params.description
        if params.context:
            doc_ref["context"] = params.context
        if params.additional_fields:
            doc_ref.update(params.additional_fields)

        logger.info("FHIR DocumentReference create", extra={"trace_id": trace_id})

        try:
            async with httpx.AsyncClient(
                timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0"))
            ) as client:
                response = await client.post(
                    f"{base_url}/DocumentReference",
                    json=doc_ref,
                    headers=auth_header,
                    timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0")),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                resp_json = exc.response.json()
                diagnostics = []
                if resp_json.get("resourceType") == "OperationOutcome":
                    for issue in resp_json.get("issue", []):
                        if "diagnostics" in issue:
                            diagnostics.append(issue["diagnostics"])
                error_detail = (
                    " | ".join(diagnostics)
                    if diagnostics
                    else f"HTTP {exc.response.status_code} from Epic FHIR endpoint"
                )
            except Exception:
                error_detail = f"HTTP {exc.response.status_code} from Epic FHIR endpoint"

            logger.error(
                "FHIR DocumentReference create failed | status=%s | epic_error=%s | payload_summary=%s",
                exc.response.status_code,
                error_detail,
                json.dumps(_safe_doc_ref_log_summary(doc_ref)),
                extra={"trace_id": trace_id},
            )
            raise ValueError(f"Epic Error: {error_detail}") from exc
        except Exception as exc:
            logger.error(
                "FHIR DocumentReference create failed | error=%s: %s",
                type(exc).__name__,
                str(exc),
                extra={"trace_id": trace_id},
            )
            raise

        resource_id: Optional[str] = None
        body: Dict[str, Any] = {}

        location = response.headers.get("Location", "")
        if location:
            history_marker = location.find("/_history/")
            resource_id = (
                location[:history_marker].split("/")[-1]
                if history_marker != -1
                else location.split("/")[-1]
            )

        if not resource_id:
            content_length = response.headers.get("content-length", "0")
            if content_length != "0" and response.content:
                try:
                    body = response.json()
                    resource_id = body.get("id")
                except Exception:
                    pass

        if not resource_id:
            raise ValueError(
                f"Could not extract resource ID from DocumentReference create response. "
                f"Status: {response.status_code}, Location: {location!r}, Body: {response.text[:200]!r}"
            )

        logger.info(
            "FHIR DocumentReference create completed | resource_id=%s",
            resource_id,
            extra={"trace_id": trace_id},
        )
        return FhirDocumentReferenceCreateOutput(
            resource_id=resource_id, resource=body if body else None
        )

    async def _search_document_reference(
        self, params: FhirDocumentReferenceSearchInput, *, trace_id: str
    ) -> FhirDocumentReferenceSearchOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()

        logger.info(
            "FHIR DocumentReference search",
            extra={"trace_id": trace_id, "search_params": params.search_params},
        )

        try:
            async with httpx.AsyncClient(
                timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0"))
            ) as client:
                response = await client.get(
                    f"{base_url}/DocumentReference",
                    headers=auth_header,
                    params=params.search_params,
                    timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0")),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "FHIR DocumentReference search failed | status=%s | body=%s",
                exc.response.status_code,
                exc.response.text,
                extra={"trace_id": trace_id},
            )
            raise
        except Exception as exc:
            logger.error(
                "FHIR DocumentReference search failed | error=%s: %s",
                type(exc).__name__,
                str(exc),
                extra={"trace_id": trace_id},
            )
            raise

        data = response.json()
        resources: list[Dict[str, Any]] = []
        total = data.get("total")
        if data.get("resourceType") == "Bundle" and data.get("entry"):
            resources = [e["resource"] for e in data["entry"] if "resource" in e]

        logger.info(
            "FHIR DocumentReference search completed | found=%s",
            len(resources),
            extra={"trace_id": trace_id},
        )
        return FhirDocumentReferenceSearchOutput(resources=resources, total=total)
