from __future__ import annotations

import asyncio
import codecs
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
import jwt

from runtime import BaseConnector, sdk_action

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


class FhirEpicConnector(BaseConnector):
    """FHIR/Epic connector: one @sdk_action per operation."""

    connector_id = "fhir_epic"
    action = "execute"
    output_model = FhirEpicOperationOutput

    @sdk_action("read_patient")
    async def read_patient(
        self, params: FhirPatientReadInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._read_patient(params, trace_id=trace_id)
        return FhirEpicOperationOutput(resource=out.resource)

    @sdk_action("search_patients")
    async def search_patients(
        self, params: FhirPatientSearchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._search_patients(params, trace_id=trace_id)
        return FhirEpicOperationOutput(
            resources=out.resources,
            total=out.total,
            errors=out.errors,
        )

    @sdk_action("search_encounter")
    async def search_encounter(
        self, params: FhirEncounterSearchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._search_encounter(params, trace_id=trace_id)
        return FhirEpicOperationOutput(resources=out.resources, total=out.total)

    @sdk_action("create_document_reference")
    async def create_document_reference(
        self, params: FhirDocumentReferenceCreateInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._create_document_reference(params, trace_id=trace_id)
        return FhirEpicOperationOutput(resource_id=out.resource_id, resource=out.resource)

    @sdk_action("search_document_reference")
    async def search_document_reference(
        self, params: FhirDocumentReferenceSearchInput, *, trace_id: str
    ) -> FhirEpicOperationOutput:
        out = await self._search_document_reference(params, trace_id=trace_id)
        return FhirEpicOperationOutput(resources=out.resources, total=out.total)

    # ------------------------------------------------------------------
    # Shared authentication helpers
    # ------------------------------------------------------------------

    def _get_base_url(self) -> str:
        return self.secret_provider.get_secret("epic_fhir_base_url").rstrip("/")

    async def _get_auth_header(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json",
        }

        private_key_str = self.secret_provider.get_secret("epic_private_key")
        kid = self.secret_provider.get_secret("epic_kid")
        client_id = self.secret_provider.get_secret("epic_client_id")
        token_url = self.secret_provider.get_secret("epic_token_url")

        private_key_pem = codecs.decode(private_key_str, "unicode_escape")

        now = int(datetime.now(tz=timezone.utc).timestamp())
        jwt_token = jwt.encode(
            {
                "iss": client_id,
                "sub": client_id,
                "aud": token_url,
                "jti": str(uuid.uuid4()),
                "iat": now,
                "nbf": now,
                "exp": now + 300,
            },
            private_key_pem,
            algorithm="RS384",
            headers={"alg": "RS384", "typ": "JWT", "kid": kid},
        )

        logger.debug("Exchanging JWT for Epic access token", extra={"token_url": token_url})

        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": jwt_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_response.status_code != 200:
                logger.error(
                    "OAuth token exchange failed | status=%s | body=%s",
                    token_response.status_code,
                    token_response.text,
                )
                token_response.raise_for_status()
            token_data = token_response.json()

        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("Epic token response did not contain an access_token")

        headers["Authorization"] = f"Bearer {access_token}"
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
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url, headers=auth_header, params=query_params, timeout=30.0
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
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(
                            f"{base_url}/Patient/{rid}",
                            headers=auth_header,
                            timeout=30.0,
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
            return FhirPatientSearchOutput(
                resources=resources, total=len(resources), errors=errors
            )

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
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{base_url}/Patient",
                    headers=auth_header,
                    params=name_params,
                    timeout=30.0,
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
        resources: List[Dict[str, Any]] = []
        total = data.get("total")
        if data.get("resourceType") == "Bundle" and data.get("entry"):
            resources = [e["resource"] for e in data["entry"] if "resource" in e]

        logger.info(
            "FHIR Patient name search completed | found=%s | total=%s",
            len(resources),
            total,
            extra={"trace_id": trace_id},
        )
        return FhirPatientSearchOutput(resources=resources, total=total)

    async def _search_encounter(
        self, params: FhirEncounterSearchInput, *, trace_id: str
    ) -> FhirEncounterSearchOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()

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

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{base_url}/Encounter",
                    headers=auth_header,
                    params=query_params,
                    timeout=30.0,
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
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{base_url}/DocumentReference",
                    json=doc_ref,
                    headers=auth_header,
                    timeout=30.0,
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
                error_detail = " | ".join(diagnostics) if diagnostics else exc.response.text
            except Exception:
                error_detail = exc.response.text

            logger.error(
                "FHIR DocumentReference create failed | status=%s | epic_error=%s | sent_payload=%s",
                exc.response.status_code,
                error_detail,
                json.dumps(doc_ref),
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
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{base_url}/DocumentReference",
                    headers=auth_header,
                    params=params.search_params,
                    timeout=30.0,
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
