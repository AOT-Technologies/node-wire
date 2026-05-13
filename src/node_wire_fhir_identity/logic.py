"""IdentityCoordinator — orchestrates cross-system patient identity workflows.

This is a plain Python class (not a connector).  Callers (playground scenarios,
scripts, tests) instantiate it with pre-built Epic and Cerner connector
instances and call its async methods directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from node_wire_fhir_cerner.logic import FhirCernerConnector
from node_wire_fhir_cerner.schema import (
    FhirCernerPatientCreateInput,
    FhirCernerPatientReadInput,
    FhirCernerPatientSearchInput,
    FhirCernerPatientUpdateInput,
)
from node_wire_fhir_epic.logic import FhirEpicConnector
from node_wire_fhir_epic.schema import (
    FhirPatientCreateInput,
    FhirPatientReadInput,
    FhirPatientSearchInput,
    FhirPatientUpdateInput,
)

from .schema import UnifiedPatientOperationOutput, UnifiedPatientSearchInput, UnifiedPatientSyncInput
from .transforms import (
    demographics_match,
    ensure_resource_type,
    extract_demographics,
    strip_source_metadata,
)

logger = logging.getLogger("fhir_identity")


class IdentityCoordinator:
    """Stateless orchestrator that bridges Epic ↔ Cerner patient identity.

    It relies on:
      * ``transforms`` — pure data helpers (no I/O)
      * Epic / Cerner connector instances — injected at construction time
    """

    def __init__(
        self,
        epic_connector: FhirEpicConnector,
        cerner_connector: FhirCernerConnector,
    ) -> None:
        self.epic = epic_connector
        self.cerner = cerner_connector

    # ------------------------------------------------------------------
    # search_patient — deterministic cross-system lookup
    # ------------------------------------------------------------------

    async def search_patient(
        self,
        params: UnifiedPatientSearchInput,
        trace_id: str,
    ) -> UnifiedPatientOperationOutput:
        """Deterministically search both systems for a patient.

        Accepts direct IDs *or* demographic criteria (name + DOB).
        """
        epic_resource: Optional[Dict[str, Any]] = None
        epic_id: Optional[str] = None
        cerner_resource: Optional[Dict[str, Any]] = None
        cerner_id: Optional[str] = None

        # --- Direct ID look-ups ------------------------------------------------
        if params.epic_patient_id:
            try:
                out = await self.epic._read_patient(
                    FhirPatientReadInput(resource_id=params.epic_patient_id),
                    trace_id=trace_id,
                )
                epic_resource = out.resource
                epic_id = epic_resource.get("id")
            except Exception as exc:
                logger.warning("Epic read by ID failed: %s", exc)

        if params.cerner_patient_id:
            try:
                out = await self.cerner._read_patient(
                    FhirCernerPatientReadInput(resource_id=params.cerner_patient_id),
                    trace_id=trace_id,
                )
                cerner_resource = out.resource
                cerner_id = cerner_resource.get("id")
            except Exception as exc:
                logger.warning("Cerner read by ID failed: %s", exc)

        # --- Demographic search (fallback) -------------------------------------
        if (
            not epic_id
            and not cerner_id
            and (params.given_name or params.family_name or params.birthdate)
        ):
            epic_id, epic_resource = await self._demographic_search_epic(
                params.given_name, params.family_name, params.birthdate, trace_id
            )
            cerner_id, cerner_resource = await self._demographic_search_cerner(
                params.given_name, params.family_name, params.birthdate, trace_id
            )

        return UnifiedPatientOperationOutput(
            status="success",
            epic_resource_id=epic_id,
            epic_resource=epic_resource,
            cerner_resource_id=cerner_id,
            cerner_resource=cerner_resource,
        )

    # ------------------------------------------------------------------
    # sync_patient — fetch from source, deduplicate, create/update target
    # ------------------------------------------------------------------

    async def sync_patient(
        self,
        params: UnifiedPatientSyncInput,
        trace_id: str,
    ) -> UnifiedPatientOperationOutput:
        """Sync a patient record from *source_system* into *target_system*.

        1. Fetch the source FHIR Patient resource.
        2. Extract demographics and search the target system for duplicates.
        3. If a match is found → ``update_patient``; otherwise → ``create_patient``.
        """
        source_id = params.source_patient_id

        # 1. Fetch source --------------------------------------------------
        source_resource = await self._fetch_patient(
            params.source_system, source_id, trace_id
        )
        if source_resource is None:
            return UnifiedPatientOperationOutput(
                status="error",
                message=f"Could not fetch {params.source_system} patient {source_id}",
            )

        # 2. Deterministic duplicate search in target ----------------------
        demo = extract_demographics(source_resource)
        target_id, target_existing = await self._demographic_search(
            params.target_system, demo.get("given"), demo.get("family"), demo.get("birthdate"), trace_id
        )

        # Optional: verify match quality using transforms helper
        if target_existing:
            target_demo = extract_demographics(target_existing)
            is_match, confidence = demographics_match(demo, target_demo)
            if not is_match:
                logger.info(
                    "Demographic match below threshold (confidence=%.2f), treating as new patient",
                    confidence,
                )
                target_id = None

        # 3. Create or update in target ------------------------------------
        payload = ensure_resource_type(strip_source_metadata(source_resource, params.target_system))

        target_resource: Optional[Dict[str, Any]] = None
        if target_id:
            # Route to HL7 for updates instead of FHIR
            try:
                from node_wire_hl7.mllp import send_adt_a08
                logger.info("Routing %s patient %s update via HL7 v2 ADT^A08", params.target_system, target_id)
                # Phase 1 stub: This will send the ADT message and wait for an ACK
                ack_status = await send_adt_a08(params.target_system, payload, target_id, trace_id=trace_id)
                if ack_status == "AA":
                    target_resource = target_existing
                else:
                    logger.warning("HL7 Update rejected with ACK %s", ack_status)
                    target_resource = target_existing
            except Exception as e:
                logger.error("Failed to send HL7 update: %s", e)
                target_resource = target_existing
        else:
            target_id, target_resource = await self._create_patient(
                params.target_system, payload, trace_id
            )

        # 4. Build output --------------------------------------------------
        result = UnifiedPatientOperationOutput(
            status="success",
            message=(
                f"Synced {params.source_system} patient {source_id} "
                f"→ {params.target_system} patient {target_id}"
            ),
        )
        if params.source_system == "epic":
            result.epic_resource_id = source_id
            result.epic_resource = source_resource
            result.cerner_resource_id = target_id
            result.cerner_resource = target_resource
        else:
            result.cerner_resource_id = source_id
            result.cerner_resource = source_resource
            result.epic_resource_id = target_id
            result.epic_resource = target_resource

        return result

    # ------------------------------------------------------------------
    # Internal helpers (thin wrappers over connectors)
    # ------------------------------------------------------------------

    async def _fetch_patient(
        self, system: str, patient_id: str, trace_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            if system == "epic":
                out = await self.epic._read_patient(
                    FhirPatientReadInput(resource_id=patient_id), trace_id=trace_id
                )
            else:
                out = await self.cerner._read_patient(
                    FhirCernerPatientReadInput(resource_id=patient_id), trace_id=trace_id
                )
            return out.resource
        except Exception as exc:
            logger.error("Failed to fetch %s patient %s: %s", system, patient_id, exc)
            return None

    async def _demographic_search(
        self,
        system: str,
        given: Optional[str],
        family: Optional[str],
        birthdate: Optional[str],
        trace_id: str,
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        if system == "epic":
            return await self._demographic_search_epic(given, family, birthdate, trace_id)
        return await self._demographic_search_cerner(given, family, birthdate, trace_id)

    async def _demographic_search_epic(
        self,
        given: Optional[str],
        family: Optional[str],
        birthdate: Optional[str],
        trace_id: str,
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        try:
            out = await self.epic._search_patients(
                FhirPatientSearchInput(
                    given_name=given, family_name=family, birthdate=birthdate
                ),
                trace_id=trace_id,
            )
            if out.resources:
                r = out.resources[0]
                return r.get("id"), r
        except Exception as exc:
            logger.warning("Epic demographic search failed: %s", exc)
        return None, None

    async def _demographic_search_cerner(
        self,
        given: Optional[str],
        family: Optional[str],
        birthdate: Optional[str],
        trace_id: str,
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        try:
            # Cerner sandbox often fails demographic matches if given name has multiple words (e.g. "Elijah John")
            safe_given = given.split()[0] if given else None
            out = await self.cerner._search_patients(
                FhirCernerPatientSearchInput(
                    given_name=safe_given, family_name=family, birthdate=birthdate
                ),
                trace_id=trace_id,
            )
            if out.resources:
                r = out.resources[0]
                return r.get("id"), r
        except Exception as exc:
            logger.warning("Cerner demographic search failed: %s", exc)
        return None, None

    async def _create_patient(
        self, system: str, payload: Dict[str, Any], trace_id: str
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        if system == "epic":
            out = await self.epic._create_patient(
                FhirPatientCreateInput(resource=payload), trace_id=trace_id
            )
        else:
            out = await self.cerner._create_patient(
                FhirCernerPatientCreateInput(resource=payload), trace_id=trace_id
            )
        return out.resource_id, out.resource

