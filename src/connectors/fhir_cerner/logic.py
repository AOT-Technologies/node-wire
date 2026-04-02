from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
import jwt

from runtime import BaseConnector, sdk_action
from runtime.fhir_encounter import assert_encounter_query_has_patient
from runtime.mcp_normalizers import (
    normalize_fhir_read_patient,
    normalize_fhir_search_encounter,
    normalize_fhir_search_patients,
)

from . import registration
from .schema import (
    FhirCernerDocumentReferenceCreateInput,
    FhirCernerDocumentReferenceCreateOutput,
    FhirCernerDocumentReferenceSearchInput,
    FhirCernerDocumentReferenceSearchOutput,
    FhirCernerEncounterSearchInput,
    FhirCernerEncounterSearchOutput,
    FhirCernerOperationOutput,
    FhirCernerPatientReadInput,
    FhirCernerPatientReadOutput,
    FhirCernerPatientSearchInput,
    FhirCernerPatientSearchOutput,
)

logger = logging.getLogger("connectors.fhir_cerner")


class FhirCernerConnector(BaseConnector):
    """
    FHIR/Cerner connector: SMART Backend Services (private_key_jwt), RS384.

    Required secrets: cerner_fhir_base_url, cerner_private_key, cerner_kid,
    cerner_client_id, cerner_token_url (optional cerner_scopes).
    """

    connector_id = "fhir_cerner"
    action = "execute"
    output_model = FhirCernerOperationOutput

    @sdk_action(
        "read_patient",
        alias_tolerant=True,
        mcp_normalize=normalize_fhir_read_patient,
    )
    async def read_patient(
        self, params: FhirCernerPatientReadInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        out = await self._read_patient(params, trace_id=trace_id)
        return FhirCernerOperationOutput(resource=out.resource)

    @sdk_action(
        "search_patients",
        alias_tolerant=True,
        mcp_normalize=normalize_fhir_search_patients,
    )
    async def search_patients(
        self, params: FhirCernerPatientSearchInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        out = await self._search_patients(params, trace_id=trace_id)
        return FhirCernerOperationOutput(
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
        self, params: FhirCernerEncounterSearchInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        out = await self._search_encounter(params, trace_id=trace_id)
        return FhirCernerOperationOutput(resources=out.resources, total=out.total)

    @sdk_action("create_document_reference")
    async def create_document_reference(
        self, params: FhirCernerDocumentReferenceCreateInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        out = await self._create_document_reference(params, trace_id=trace_id)
        return FhirCernerOperationOutput(resource_id=out.resource_id, resource=out.resource)

    @sdk_action("search_document_reference")
    async def search_document_reference(
        self, params: FhirCernerDocumentReferenceSearchInput, *, trace_id: str
    ) -> FhirCernerOperationOutput:
        out = await self._search_document_reference(params, trace_id=trace_id)
        return FhirCernerOperationOutput(resources=out.resources, total=out.total)

    # ------------------------------------------------------------------
    # Shared authentication helpers
    # ------------------------------------------------------------------

    def _get_base_url(self) -> str:
        return self._secret_provider.get_secret("cerner_fhir_base_url").rstrip("/")

    async def _get_auth_header(self) -> Dict[str, str]:
        """
        Obtain an access token via Cerner's SMART Backend Services (private_key_jwt)
        and return ready-to-use request headers.

        Algorithm: RS384. Token lifetime: 5 minutes.
        Reference: https://code-console.cerner.com/
        """
        headers = {
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json",
        }

        private_key_str = self._secret_provider.get_secret("cerner_private_key")
        kid = self._secret_provider.get_secret("cerner_kid")
        client_id = self._secret_provider.get_secret("cerner_client_id")
        token_url = self._secret_provider.get_secret("cerner_token_url")

        # Validate required secrets are present and non-empty.
        missing = [name for name, val in [
            ("cerner_private_key", private_key_str),
            ("cerner_kid", kid),
            ("cerner_client_id", client_id),
            ("cerner_token_url", token_url),
        ] if not (val or "").strip()]
        if missing:
            raise ValueError(f"Missing or empty required Cerner secrets: {', '.join(missing)}")

        # Guard against the malformed URL pattern that embeds the FHIR host inside the auth URL.
        # Correct: .../tenants/{tenant}/protocols/oauth2/profiles/smart-v1/token
        # Wrong:   .../tenants/{tenant}/hosts/fhir-ehr-code.cerner.com/protocols/...
        if "/hosts/" in token_url:
            raise ValueError(
                "cerner_token_url appears malformed — it contains a '/hosts/' segment which is not "
                "valid for the Cerner authorization server. "
                "Correct format: https://authorization.cerner.com/tenants/{tenant_id}/protocols/oauth2/profiles/smart-v1/token"
            )

        try:
            scopes = (self._secret_provider.get_secret("cerner_scopes") or "").strip()
        except Exception:
            scopes = ""

        if not scopes:
            scopes = "system/Patient.read system/Encounter.read system/DocumentReference.read system/DocumentReference.write"

        logger.debug("Cerner token request | token_url=%s | scopes=%r | client_id=%s", token_url, scopes, client_id)

        # Decode escaped newlines in PEM key if stored as a single-line env var (e.g. "\\n" -> "\n").
        # Avoid codecs.unicode_escape which can corrupt non-ASCII bytes in some PEM keys.
        if "\\n" in private_key_str:
            private_key_pem = private_key_str.replace("\\n", "\n")
        else:
            private_key_pem = private_key_str

        now = int(datetime.now(tz=timezone.utc).timestamp())
        jwt_token = jwt.encode(
            {
                "iss": client_id,
                "sub": client_id,
                "aud": token_url,
                "jti": str(uuid.uuid4()),
                "iat": now,
                "exp": now + 300,
                "scope": scopes,
            },
            private_key_pem,
            algorithm="RS384",
            headers={"alg": "RS384", "typ": "JWT", "kid": kid},
        )

        post_data = {
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": jwt_token,
            "scope": scopes,
        }

        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                token_url,
                data=post_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_response.status_code != 200:
                logger.error(
                    "OAuth token exchange failed | status=%s | body=%s",
                    token_response.status_code, token_response.text,
                )
                token_response.raise_for_status()
            token_data = token_response.json()

        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("Cerner token response did not contain an access_token")

        headers["Authorization"] = f"Bearer {access_token}"
        return headers

    # ------------------------------------------------------------------
    # Internal name-field helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_name_search_params(
        given_name: Optional[str],
        family_name: Optional[str],
        name: Optional[str],
        birthdate: Optional[str],
        extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Build a FHIR search params dict from explicit name/date fields."""
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
        """Build a FHIR search params dict for Encounter from explicit fields."""
        params: Dict[str, str] = dict(extra or {})

        if patient_id and patient_id.strip():
            params["patient"] = patient_id.strip()
        if status and status.strip():
            params["status"] = status.strip()
        if date and date.strip():
            params["date"] = date.strip()

        return params

    # ------------------------------------------------------------------
    # Action: read_patient
    # ------------------------------------------------------------------

    async def _read_patient(
        self, params: FhirCernerPatientReadInput, *, trace_id: str
    ) -> FhirCernerPatientReadOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()

        if params.resource_id:
            url = f"{base_url}/Patient/{params.resource_id}"
            query_params: Optional[Dict[str, str]] = None
            logger.info("FHIR Patient read by ID", extra={"trace_id": trace_id, "resource_id": params.resource_id})
        elif params.given_name or params.family_name or params.name:
            url = f"{base_url}/Patient"
            query_params = self._build_name_search_params(
                params.given_name, params.family_name, params.name,
                params.birthdate, params.search_params,
            )
            logger.info("FHIR Patient read by name fields", extra={"trace_id": trace_id, "query_params": query_params})
        elif params.search_params:
            url = f"{base_url}/Patient"
            query_params = params.search_params
            logger.info("FHIR Patient read by search", extra={"trace_id": trace_id, "search_params": params.search_params})
        else:
            raise ValueError(
                "Provide resource_id, or name fields (given_name/family_name/name), "
                "or search_params"
            )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=auth_header, params=query_params, timeout=30.0)
                response.raise_for_status()
        except Exception as exc:
            logger.error("FHIR Patient read failed | error=%s: %s", type(exc).__name__, str(exc), extra={"trace_id": trace_id})
            raise

        data = response.json()
        if data.get("resourceType") == "Bundle":
            if data.get("entry"):
                resource = data["entry"][0].get("resource", {})
            else:
                raise ValueError("No patients found in search results")
        else:
            resource = data

        logger.info("FHIR Patient read completed", extra={"trace_id": trace_id, "status_code": response.status_code})
        return FhirCernerPatientReadOutput(resource=resource)

    # ------------------------------------------------------------------
    # Action: search_patients (multi-ID fan-out OR name search)
    # ------------------------------------------------------------------

    async def _search_patients(
        self, params: FhirCernerPatientSearchInput, *, trace_id: str
    ) -> FhirCernerPatientSearchOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()

        # ---- Mode 1: Multi-ID fan-out ----
        if params.resource_ids:
            ids = [rid.strip() for rid in params.resource_ids if rid.strip()]
            if not ids:
                raise ValueError("resource_ids list is empty")

            logger.info(
                "FHIR Cerner Patient multi-ID lookup | count=%s",
                len(ids),
                extra={"trace_id": trace_id, "resource_ids": ids},
            )

            async def _fetch_one(rid: str) -> tuple[str, Optional[Dict[str, Any]], Optional[str]]:
                """Return (rid, resource_or_None, error_or_None)."""
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
                        "FHIR Cerner Patient fetch failed | resource_id=%s | error=%s",
                        rid, str(exc),
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
                "FHIR Cerner Patient multi-ID lookup completed | found=%s | errors=%s",
                len(resources), len(errors),
                extra={"trace_id": trace_id},
            )
            return FhirCernerPatientSearchOutput(resources=resources, total=len(resources), errors=errors)

        # ---- Mode 2: Name-based search (returns Bundle) ----
        name_params = self._build_name_search_params(
            params.given_name, params.family_name, params.name,
            params.birthdate, params.search_params,
        )
        if not name_params:
            raise ValueError(
                "Provide resource_ids for multi-ID lookup, or at least one of "
                "given_name / family_name / name / birthdate / search_params for name-based search"
            )

        logger.info(
            "FHIR Cerner Patient name search | params=%s",
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
                "FHIR Cerner Patient name search failed | status=%s | body=%s",
                exc.response.status_code, exc.response.text,
                extra={"trace_id": trace_id},
            )
            raise
        except Exception as exc:
            logger.error(
                "FHIR Cerner Patient name search failed | error=%s: %s",
                type(exc).__name__, str(exc),
                extra={"trace_id": trace_id},
            )
            raise

        data = response.json()
        resources: List[Dict[str, Any]] = []
        total = data.get("total")
        if data.get("resourceType") == "Bundle" and data.get("entry"):
            resources = [e["resource"] for e in data["entry"] if "resource" in e]

        logger.info(
            "FHIR Cerner Patient name search completed | found=%s | total=%s",
            len(resources), total,
            extra={"trace_id": trace_id},
        )
        return FhirCernerPatientSearchOutput(resources=resources, total=total)

    # ------------------------------------------------------------------
    # Action: search_encounter
    # ------------------------------------------------------------------

    async def _search_encounter(
        self, params: FhirCernerEncounterSearchInput, *, trace_id: str
    ) -> FhirCernerEncounterSearchOutput:
        base_url = self._get_base_url()

        if params.patient_id or params.status or params.date:
            query_params = self._build_encounter_search_params(
                params.patient_id, params.status, params.date, params.search_params
            )
            logger.info("FHIR Encounter search by explicit fields", extra={"trace_id": trace_id, "query_params": query_params})
        elif params.search_params:
            query_params = params.search_params
            logger.info("FHIR Encounter search by raw params", extra={"trace_id": trace_id, "search_params": params.search_params})
        else:
            raise ValueError("Provide at least patient_id, status, date OR search_params")

        assert_encounter_query_has_patient(query_params)

        auth_header = await self._get_auth_header()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{base_url}/Encounter", headers=auth_header, params=query_params, timeout=30.0,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("FHIR Encounter search failed | status=%s | body=%s", exc.response.status_code, exc.response.text, extra={"trace_id": trace_id})
            raise
        except Exception as exc:
            logger.error("FHIR Encounter search failed | error=%s: %s", type(exc).__name__, str(exc), extra={"trace_id": trace_id})
            raise

        data = response.json()
        resources: list[Dict[str, Any]] = []
        total = data.get("total")
        if data.get("resourceType") == "Bundle" and data.get("entry"):
            resources = [e["resource"] for e in data["entry"] if "resource" in e]

        logger.info("FHIR Encounter search completed | found=%s", len(resources), extra={"trace_id": trace_id})
        return FhirCernerEncounterSearchOutput(resources=resources, total=total)

    # ------------------------------------------------------------------
    # Action: create_document_reference
    # ------------------------------------------------------------------

    async def _create_document_reference(
        self, params: FhirCernerDocumentReferenceCreateInput, *, trace_id: str
    ) -> FhirCernerDocumentReferenceCreateOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()

        # Cerner sandbox strictly requires a charset (lowercase, no space) for text types.
        # Failing to provide it results in: "a character set must be specified" (422).
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

        # Cerner requires title and creation on the attachment
        if not params.attachment_title:
            raise ValueError(
                "Cerner requires 'attachment_title' on DocumentReference create."
            )
        attachment["title"] = params.attachment_title
        attachment["creation"] = params.attachment_creation or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        doc_ref: Dict[str, Any] = {
            "resourceType": "DocumentReference",
            "status": params.status,
            "docStatus": params.doc_status or "final",  # Required by Cerner on create
            "type": params.type,
            "subject": {"reference": params.subject},
            "content": [{"attachment": attachment}],
        }
        # Cerner requires type.coding entries to have a 'display' value,
        # 'userSelected: True', and the type object itself must have a top-level 'text' field.
        # Crucially, Cerner sandbox requires CodeSet 72 proprietary coding, not raw LOINC codes.
        if "type" in doc_ref:
            codings = doc_ref["type"].get("coding", [])
            for coding in codings:
                # Validate CodeSet 72 vs LOINC
                if "loinc.org" in coding.get("system", ""):
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
                # Default text to the display of the first coding
                doc_ref["type"]["text"] = codings[0].get("display", "")

        # Cerner R4 Sandbox documentation examples do not include 'category'.
        # We only include it if explicitly passed by the user.
        if params.category:
            doc_ref["category"] = params.category

        # Note: Cerner does NOT support the 'identifier' field on DocumentReference create.

        if params.author:
            doc_ref["author"] = params.author

        # Cerner requires authenticator on create
        if params.authenticator:
            doc_ref["authenticator"] = params.authenticator
        elif params.author:
            doc_ref["authenticator"] = params.author[0]
            logger.debug(
                "Auto-set authenticator to author[0] (Cerner requires authenticator on create)",
                extra={"trace_id": trace_id},
            )

        if params.custodian:
            doc_ref["custodian"] = params.custodian

        # Note: 'description' is intentionally omitted by default 
        # as Cerner can reject it depending on tenant configuration.
        if params.context:
            context = dict(params.context)
            # Cerner REQUIRES context.period whenever context.encounter is set.
            # Auto-inject a period using the document date if the caller didn't supply one.
            if context.get("encounter") and not context.get("period"):
                # Force .000Z precision and provide a 1-hour clinical window
                start_dt = datetime.now(tz=timezone.utc)
                end_dt = start_dt + timedelta(hours=1)
                context["period"] = {
                    "start": start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "end": end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                }
                logger.debug(
                    "Auto-injected context.period (required by Cerner when encounter is set)",
                    extra={"trace_id": trace_id},
                )
            doc_ref["context"] = context
        if params.additional_fields:
            doc_ref.update(params.additional_fields)

        # Ensure no connector-specific fields leaked into the root of the FHIR resource.
        # Cerner will reject the payload with a 422 if it sees unknown root fields.
        for field in ["text", "data", "content_type", "attachment_title", "attachment_creation", "doc_status"]:
            doc_ref.pop(field, None)

        # Cerner requires at least one author for clinical note document types.
        if not params.author:
            raise ValueError(
                "Cerner requires 'author' for clinical note document types. "
                "Provide at least one author reference, e.g. [{'reference': 'Practitioner/{id}'}]"
            )

        logger.info("FHIR DocumentReference create", extra={"trace_id": trace_id})

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{base_url}/DocumentReference", json=doc_ref, headers=auth_header, timeout=30.0,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raw_body = exc.response.text
            try:
                resp_json = exc.response.json()
                diagnostics = []
                if resp_json.get("resourceType") == "OperationOutcome":
                    for issue in resp_json.get("issue", []):
                        diag = issue.get("diagnostics") or ""
                        detail_text = issue.get("details", {}).get("text", "")
                        severity = issue.get("severity", "")
                        code = issue.get("code", "")
                        parts = [p for p in [severity, code, diag or detail_text] if p]
                        if parts:
                            diagnostics.append(" ".join(parts))
                error_detail = " | ".join(diagnostics) if diagnostics else raw_body
            except Exception:
                error_detail = raw_body

            logger.error(
                "FHIR DocumentReference create failed | status=%s | cerner_error=%s | raw_body=%s | sent_payload=%s",
                exc.response.status_code, error_detail, raw_body, json.dumps(doc_ref),
                extra={"trace_id": trace_id},
            )
            raise ValueError(f"Cerner Error: {error_detail}") from exc
        except Exception as exc:
            logger.error("FHIR DocumentReference create failed | error=%s: %s", type(exc).__name__, str(exc), extra={"trace_id": trace_id})
            raise

        resource_id: Optional[str] = None
        body: Dict[str, Any] = {}

        location = response.headers.get("Location", "")
        if location:
            history_marker = location.find("/_history/")
            resource_id = location[:history_marker].split("/")[-1] if history_marker != -1 else location.split("/")[-1]

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

        logger.info("FHIR DocumentReference create completed | resource_id=%s", resource_id, extra={"trace_id": trace_id})
        return FhirCernerDocumentReferenceCreateOutput(resource_id=resource_id, resource=body if body else None)

    # ------------------------------------------------------------------
    # Action: search_document_reference
    # ------------------------------------------------------------------

    async def _search_document_reference(
        self, params: FhirCernerDocumentReferenceSearchInput, *, trace_id: str
    ) -> FhirCernerDocumentReferenceSearchOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()

        logger.info("FHIR DocumentReference search", extra={"trace_id": trace_id, "search_params": params.search_params})

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{base_url}/DocumentReference", headers=auth_header, params=params.search_params, timeout=30.0,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("FHIR DocumentReference search failed | status=%s | body=%s", exc.response.status_code, exc.response.text, extra={"trace_id": trace_id})
            raise
        except Exception as exc:
            logger.error("FHIR DocumentReference search failed | error=%s: %s", type(exc).__name__, str(exc), extra={"trace_id": trace_id})
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
        return FhirCernerDocumentReferenceSearchOutput(resources=resources, total=total)