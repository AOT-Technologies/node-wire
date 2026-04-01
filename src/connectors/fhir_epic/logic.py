from __future__ import annotations

import asyncio
import codecs
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx
import jwt

from runtime import BaseConnector, SecretProvider

from .schema import (
    FhirDocumentReferenceCreateInput,
    FhirDocumentReferenceCreateOutput,
    FhirDocumentReferenceSearchInput,
    FhirDocumentReferenceSearchOutput,
    FhirEncounterSearchInput,
    FhirEncounterSearchOutput,
    FhirPatientReadInput,
    FhirPatientReadOutput,
    FhirPatientSearchInput,
    FhirPatientSearchOutput,
)

logger = logging.getLogger("connectors.fhir_epic")

# Shared extra fields injected into every log record for this connector.
# Grafana panels filter on connector_id — this must be present on ALL log lines.
_LOG_CTX = {"connector_id": "fhir_epic", "connector_type": "fhir"}


class _FhirAction(BaseConnector[Any, Any]):
    """
    Lightweight BaseConnector that delegates execution to a FhirEpicConnector
    instance method.  One of these is created per action so that the manifest
    and REST router can discover each action's schema and route automatically.
    """

    connector_id = "fhir_epic"

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
        self.action = action        # instance attribute, overrides absent class-level action
        self._handler = handler

    async def internal_execute(self, params: Any, *, trace_id: str) -> Any:
        # ------------------------------------------------------------------ #
        # Panel 1 & 2 SUCCESS RATE: emit the two sentinel lines here so that  #
        # every action goes through a single start/success/error lifecycle.   #
        # ------------------------------------------------------------------ #
        log_extra = {**_LOG_CTX, "trace_id": trace_id, "action": self.action}

        logger.info("Starting connector execution", extra=log_extra)

        try:
            result = await self._handler(params, trace_id=trace_id)
            logger.info("Connector completed successfully", extra=log_extra)
            return result
        except Exception as exc:
            logger.error(
                "Connector failed: %s",
                str(exc),
                extra={**log_extra, "error_type": type(exc).__name__, "error_message": str(exc)},
            )
            raise


class FhirEpicConnector:
    """
    Single FHIR/Epic connector.

    ``connector_id = "fhir_epic"``.  All authentication helpers and action
    implementations live here.  The factory registers ONE instance of this
    class; ``list_actions()`` and ``get_action()`` are used by the factory to
    expose each action to the manifest and REST router.

    Supported actions:
      • read_patient          — fetch a single Patient by ID or name search
      • search_patients       — fetch multiple Patients by list of IDs or name search
      • search_encounter
      • create_document_reference
      • search_document_reference

    Name-based search parameters (``given_name``, ``family_name``, ``name``,
    ``birthdate``) are prioritised over the raw ``search_params`` dict and are
    normalised (stripped, lowercased for ``name`` token search).
    """

    connector_id = "fhir_epic"

    def __init__(self, *, secret_provider: SecretProvider) -> None:
        self._secret_provider = secret_provider

        self._actions: Dict[str, _FhirAction] = {
            "read_patient": _FhirAction(
                "read_patient", FhirPatientReadInput, FhirPatientReadOutput,
                self._read_patient, secret_provider=secret_provider,
            ),
            "search_patients": _FhirAction(
                "search_patients", FhirPatientSearchInput, FhirPatientSearchOutput,
                self._search_patients, secret_provider=secret_provider,
            ),
            "search_encounter": _FhirAction(
                "search_encounter", FhirEncounterSearchInput, FhirEncounterSearchOutput,
                self._search_encounter, secret_provider=secret_provider,
            ),
            "create_document_reference": _FhirAction(
                "create_document_reference", FhirDocumentReferenceCreateInput, FhirDocumentReferenceCreateOutput,
                self._create_document_reference, secret_provider=secret_provider,
            ),
            "search_document_reference": _FhirAction(
                "search_document_reference", FhirDocumentReferenceSearchInput, FhirDocumentReferenceSearchOutput,
                self._search_document_reference, secret_provider=secret_provider,
            ),
        }

    # ------------------------------------------------------------------
    # Action discovery — consumed by ConnectorFactory
    # ------------------------------------------------------------------

    def list_actions(self) -> List[_FhirAction]:
        """Return all registered action connectors (used by list_for_protocol)."""
        return list(self._actions.values())

    def get_action(self, name: str) -> Optional[_FhirAction]:
        """Return the action connector for the given action name."""
        return self._actions.get(name)

    # ------------------------------------------------------------------
    # Shared authentication helpers
    # ------------------------------------------------------------------

    def _get_base_url(self) -> str:
        return self._secret_provider.get_secret("epic_fhir_base_url").rstrip("/")

    async def _get_auth_header(self) -> Dict[str, str]:
        """
        Obtain an access token via Epic's SMART Backend Services (private_key_jwt)
        and return ready-to-use request headers.

        Algorithm: RS384. Token lifetime: 5 minutes (Epic maximum).
        Reference: https://fhir.epic.com/Documentation?docId=oauth2tutorial&section=cloud-based-app
        """
        headers = {
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json",
        }

        private_key_str = self._secret_provider.get_secret("epic_private_key")
        kid = self._secret_provider.get_secret("epic_kid")
        client_id = self._secret_provider.get_secret("epic_client_id")
        token_url = self._secret_provider.get_secret("epic_token_url")

        # Environment variables sometimes store newlines as escape sequences.
        private_key_pem = codecs.decode(private_key_str, "unicode_escape")

        now = int(datetime.now(tz=timezone.utc).timestamp())
        jwt_token = jwt.encode(
            {
                "iss": client_id, "sub": client_id, "aud": token_url,
                "jti": str(uuid.uuid4()), "iat": now, "nbf": now, "exp": now + 300,
            },
            private_key_pem,
            algorithm="RS384",
            headers={"alg": "RS384", "typ": "JWT", "kid": kid},
        )

        logger.debug(
            "Exchanging JWT for Epic access token",
            extra={**_LOG_CTX, "token_url": token_url},
        )

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
                    "OAuth token exchange failed",
                    extra={
                        **_LOG_CTX,
                        "status_code": token_response.status_code,
                        "body": token_response.text,
                    },
                )
                token_response.raise_for_status()
            token_data = token_response.json()

        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("Epic token response did not contain an access_token")

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
        self, params: FhirPatientReadInput, *, trace_id: str
    ) -> FhirPatientReadOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()
        log_extra = {**_LOG_CTX, "trace_id": trace_id, "action": "read_patient"}

        if params.resource_id:
            url = f"{base_url}/Patient/{params.resource_id}"
            query_params: Optional[Dict[str, str]] = None
            logger.info(
                "FHIR Patient read by ID",
                extra={**log_extra, "resource_id": params.resource_id},
            )
        elif params.given_name or params.family_name or params.name:
            url = f"{base_url}/Patient"
            query_params = self._build_name_search_params(
                params.given_name, params.family_name, params.name,
                params.birthdate, params.search_params,
            )
            logger.info(
                "FHIR Patient read by name fields",
                extra={**log_extra, "query_params": query_params},
            )
        elif params.search_params:
            url = f"{base_url}/Patient"
            query_params = params.search_params
            logger.info(
                "FHIR Patient read by search",
                extra={**log_extra, "search_params": params.search_params},
            )
        else:
            raise ValueError(
                "Provide resource_id, or name fields (given_name/family_name/name), "
                "or search_params"
            )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url, headers=auth_header, params=query_params, timeout=30.0,
                )
                response.raise_for_status()
        except Exception as exc:
            logger.error(
                "FHIR Patient read failed",
                extra={**log_extra, "error_type": type(exc).__name__, "error_message": str(exc)},
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
            extra={**log_extra, "status_code": response.status_code},
        )
        return FhirPatientReadOutput(resource=resource)

    # ------------------------------------------------------------------
    # Action: search_patients (multi-ID fan-out OR name search)
    # ------------------------------------------------------------------

    async def _search_patients(
        self, params: FhirPatientSearchInput, *, trace_id: str
    ) -> FhirPatientSearchOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()
        log_extra = {**_LOG_CTX, "trace_id": trace_id, "action": "search_patients"}

        # ---- Mode 1: Multi-ID fan-out ----
        if params.resource_ids:
            ids = [rid.strip() for rid in params.resource_ids if rid.strip()]
            if not ids:
                raise ValueError("resource_ids list is empty")

            logger.info(
                "FHIR Patient multi-ID lookup",
                extra={**log_extra, "count": len(ids), "resource_ids": ids},
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
                        "FHIR Patient fetch failed",
                        extra={**log_extra, "resource_id": rid, "error_message": str(exc)},
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
                "FHIR Patient multi-ID lookup completed",
                extra={**log_extra, "found": len(resources), "errors": len(errors)},
            )
            return FhirPatientSearchOutput(resources=resources, total=len(resources), errors=errors)

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
            "FHIR Patient name search",
            extra={**log_extra, "params": name_params},
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
                "FHIR Patient name search failed",
                extra={
                    **log_extra,
                    "status_code": exc.response.status_code,
                    "body": exc.response.text,
                },
            )
            raise
        except Exception as exc:
            logger.error(
                "FHIR Patient name search failed",
                extra={**log_extra, "error_type": type(exc).__name__, "error_message": str(exc)},
            )
            raise

        data = response.json()
        resources = []
        total = data.get("total")
        if data.get("resourceType") == "Bundle" and data.get("entry"):
            resources = [e["resource"] for e in data["entry"] if "resource" in e]

        logger.info(
            "FHIR Patient name search completed",
            extra={**log_extra, "found": len(resources), "total": total},
        )
        return FhirPatientSearchOutput(resources=resources, total=total)

    # ------------------------------------------------------------------
    # Action: search_encounter
    # ------------------------------------------------------------------

    async def _search_encounter(
        self, params: FhirEncounterSearchInput, *, trace_id: str
    ) -> FhirEncounterSearchOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()
        log_extra = {**_LOG_CTX, "trace_id": trace_id, "action": "search_encounter"}

        if params.patient_id or params.status or params.date:
            query_params = self._build_encounter_search_params(
                params.patient_id, params.status, params.date, params.search_params
            )
            logger.info(
                "FHIR Encounter search by explicit fields",
                extra={**log_extra, "query_params": query_params},
            )
        elif params.search_params:
            query_params = params.search_params
            logger.info(
                "FHIR Encounter search by raw params",
                extra={**log_extra, "search_params": params.search_params},
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
                "FHIR Encounter search failed",
                extra={
                    **log_extra,
                    "status_code": exc.response.status_code,
                    "body": exc.response.text,
                },
            )
            raise
        except Exception as exc:
            logger.error(
                "FHIR Encounter search failed",
                extra={**log_extra, "error_type": type(exc).__name__, "error_message": str(exc)},
            )
            raise

        data = response.json()
        resources: list[Dict[str, Any]] = []
        total = data.get("total")
        if data.get("resourceType") == "Bundle" and data.get("entry"):
            resources = [e["resource"] for e in data["entry"] if "resource" in e]

        logger.info(
            "FHIR Encounter search completed",
            extra={**log_extra, "found": len(resources)},
        )
        return FhirEncounterSearchOutput(resources=resources, total=total)

    # ------------------------------------------------------------------
    # Action: create_document_reference
    # ------------------------------------------------------------------

    async def _create_document_reference(
        self, params: FhirDocumentReferenceCreateInput, *, trace_id: str
    ) -> FhirDocumentReferenceCreateOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()
        log_extra = {**_LOG_CTX, "trace_id": trace_id, "action": "create_document_reference"}

        doc_ref: Dict[str, Any] = {
            "resourceType": "DocumentReference",
            "identifier": params.identifier,
            "status": params.status,
            "type": params.type,
            "subject": {"reference": params.subject},
            "date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "content": [{"attachment": {"contentType": params.content_type or "text/plain", "data": params.data}}],
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

        logger.info("FHIR DocumentReference create", extra=log_extra)

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
                "FHIR DocumentReference create failed",
                extra={
                    **log_extra,
                    "status_code": exc.response.status_code,
                    "epic_error": error_detail,
                },
            )
            raise ValueError(f"Epic Error: {error_detail}") from exc
        except Exception as exc:
            logger.error(
                "FHIR DocumentReference create failed",
                extra={**log_extra, "error_type": type(exc).__name__, "error_message": str(exc)},
            )
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

        logger.info(
            "FHIR DocumentReference create completed",
            extra={**log_extra, "resource_id": resource_id},
        )
        return FhirDocumentReferenceCreateOutput(resource_id=resource_id, resource=body if body else None)

    # ------------------------------------------------------------------
    # Action: search_document_reference
    # ------------------------------------------------------------------

    async def _search_document_reference(
        self, params: FhirDocumentReferenceSearchInput, *, trace_id: str
    ) -> FhirDocumentReferenceSearchOutput:
        base_url = self._get_base_url()
        auth_header = await self._get_auth_header()
        log_extra = {**_LOG_CTX, "trace_id": trace_id, "action": "search_document_reference"}

        logger.info(
            "FHIR DocumentReference search",
            extra={**log_extra, "search_params": params.search_params},
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
                "FHIR DocumentReference search failed",
                extra={
                    **log_extra,
                    "status_code": exc.response.status_code,
                    "body": exc.response.text,
                },
            )
            raise
        except Exception as exc:
            logger.error(
                "FHIR DocumentReference search failed",
                extra={**log_extra, "error_type": type(exc).__name__, "error_message": str(exc)},
            )
            raise

        data = response.json()
        resources: list[Dict[str, Any]] = []
        total = data.get("total")
        if data.get("resourceType") == "Bundle" and data.get("entry"):
            resources = [e["resource"] for e in data["entry"] if "resource" in e]

        logger.info(
            "FHIR DocumentReference search completed",
            extra={**log_extra, "found": len(resources)},
        )
        return FhirDocumentReferenceSearchOutput(resources=resources, total=total)