from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx
import jwt

from runtime import BaseConnector, SecretProvider

from . import registration
from .schema import (
    FhirCernerDocumentReferenceCreateInput,
    FhirCernerDocumentReferenceCreateOutput,
    FhirCernerDocumentReferenceSearchInput,
    FhirCernerDocumentReferenceSearchOutput,
    FhirCernerEncounterSearchInput,
    FhirCernerEncounterSearchOutput,
    FhirCernerPatientReadInput,
    FhirCernerPatientReadOutput,
    FhirCernerPatientSearchInput,
    FhirCernerPatientSearchOutput,
)

logger = logging.getLogger("connectors.fhir_cerner")


class _FhirCernerAction(BaseConnector[Any, Any]):
    """
    Lightweight BaseConnector that delegates execution to a FhirCernerConnector
    instance method.  One of these is created per action so that the manifest
    and REST router can discover each action's schema and route automatically.
    """

    connector_id = "fhir_cerner"

    def __init__(
        self,
        action: str,
        input_model: type,
        output_model: type,
        handler: Callable,
        *,
        secret_provider: Optional[SecretProvider] = None,
    ) -> None:
        super().__init__(input_model, output_model, secret_provider=secret_provider)
        self.action = action
        self._handler = handler

    async def internal_execute(self, params: Any, *, trace_id: str) -> Any:
        return await self._handler(params, trace_id=trace_id)


class FhirCernerConnector:
    """
    Single FHIR/Cerner connector.

    ``connector_id = "fhir_cerner"``.  All authentication helpers and action
    implementations live here.  The factory registers ONE instance of this
    class; ``list_actions()`` and ``get_action()`` are used by the factory to
    expose each action to the manifest and REST router.

    Authentication uses Cerner's SMART Backend Services (private_key_jwt) flow,
    identical to Epic's implementation — RS384-signed JWT exchanged for an
    OAuth2 access token at the configured token endpoint.

    Supported actions:
      • read_patient          — fetch a single Patient by ID or name search
      • search_patients       — fetch multiple Patients by list of IDs or name search
      • search_encounter
      • create_document_reference
      • search_document_reference

    Name-based search parameters (``given_name``, ``family_name``, ``name``,
    ``birthdate``) are prioritised over the raw ``search_params`` dict.

    .. note::
        Cerner's sandbox name search is case-sensitive.  Supply names exactly
        as stored in the system.  Special characters in search values should be
        URL-encoded (httpx handles this automatically).

    Required secrets (configured via SecretProvider):
      - cerner_fhir_base_url  : Cerner FHIR R4 base URL
      - cerner_private_key    : RSA private key PEM (newlines may be escaped)
      - cerner_kid            : Key ID registered in the Cerner code console
      - cerner_client_id      : Client ID from Cerner app registration
      - cerner_token_url      : OAuth2 token endpoint URL (from .well-known/smart-configuration
                                or the Cerner code console)
    """

    connector_id = "fhir_cerner"

    def __init__(self, *, secret_provider: SecretProvider) -> None:
        self._secret_provider = secret_provider

        self._actions: Dict[str, _FhirCernerAction] = {
            "read_patient": _FhirCernerAction(
                "read_patient", FhirCernerPatientReadInput, FhirCernerPatientReadOutput,
                self._read_patient, secret_provider=secret_provider,
            ),
            "search_patients": _FhirCernerAction(
                "search_patients", FhirCernerPatientSearchInput, FhirCernerPatientSearchOutput,
                self._search_patients, secret_provider=secret_provider,
            ),
            "search_encounter": _FhirCernerAction(
                "search_encounter", FhirCernerEncounterSearchInput, FhirCernerEncounterSearchOutput,
                self._search_encounter, secret_provider=secret_provider,
            ),
            "create_document_reference": _FhirCernerAction(
                "create_document_reference", FhirCernerDocumentReferenceCreateInput, FhirCernerDocumentReferenceCreateOutput,
                self._create_document_reference, secret_provider=secret_provider,
            ),
            "search_document_reference": _FhirCernerAction(
                "search_document_reference", FhirCernerDocumentReferenceSearchInput, FhirCernerDocumentReferenceSearchOutput,
                self._search_document_reference, secret_provider=secret_provider,
            ),
        }

    # ------------------------------------------------------------------
    # Action discovery — consumed by ConnectorFactory
    # ------------------------------------------------------------------

    def list_actions(self) -> List[_FhirCernerAction]:
        """Return all registered action connectors (used by list_for_protocol)."""
        return list(self._actions.values())

    def get_action(self, name: str) -> Optional[_FhirCernerAction]:
        """Return the action connector for the given action name."""
        return self._actions.get(name)

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
        """Build a FHIR search params dict from explicit name/date fields.

        Priority: given_name/family_name > name > (nothing).
        The ``extra`` dict (raw search_params) is merged at lowest priority.

        .. note::
            Cerner's sandbox name matching is case-sensitive — supply names
            with the same capitalisation as stored in the system.
        """
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
        resources = []
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
        auth_header = await self._get_auth_header()

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

        # Validate context early so callers get the most actionable error.
        if params.context:
            ctx = dict(params.context)
            if ctx.get("encounter") and not ctx.get("period"):
                raise ValueError("Cerner requires 'context.period' when 'context.encounter' is provided.")

        # Cerner sandbox strictly requires a charset (lowercase, no space) for text types.
        # Failing to provide it results in: "a character set must be specified" (422).
        content_type = (params.content_type or "text/plain").strip().lower()
        if content_type.startswith("text/"):
            if "charset=" not in content_type:
                # Match the formatting expected by tests and common HTTP conventions.
                content_type = f"{content_type}; charset=UTF-8"

        attachment: Dict[str, Any] = {"contentType": content_type}
        if params.data:
            attachment["data"] = params.data
        elif params.text:
            attachment["data"] = base64.b64encode(params.text.encode("utf-8")).decode("ascii")
        else:
            raise ValueError("Either 'text' or 'data' must be provided")

        # Some Cerner tenants require title/creation; default safely when omitted.
        attachment["title"] = params.attachment_title or "Document"
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
            doc_ref["context"] = dict(params.context)
        if params.additional_fields:
            doc_ref.update(params.additional_fields)

        # Ensure no connector-specific fields leaked into the root of the FHIR resource.
        # Cerner will reject the payload with a 422 if it sees unknown root fields.
        for field in ["text", "data", "content_type", "attachment_title", "attachment_creation", "doc_status"]:
            doc_ref.pop(field, None)

        # Note: Some Cerner tenants require author/authenticator. The connector does not
        # enforce those fields universally; tenants that require them will return 4xx
        # with OperationOutcome diagnostics.

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