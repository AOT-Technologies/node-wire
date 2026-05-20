"""IdentityCoordinator — orchestrates cross-system patient identity workflows.

This is a plain Python class (not a connector).  Callers (playground scenarios,
scripts, tests) instantiate it with pre-built Epic and Cerner connector
instances and call its async methods directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

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
    extract_ssn_from_resource,
    score_candidate,
    strip_source_metadata,
    _get_weights,
)

logger = logging.getLogger("fhir_identity")

# Maximum candidates to score from a single FHIR search bundle
_MAX_CANDIDATES = 10


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
        Optional enrichment fields (gender, phone, email, address, postal)
        are used for scoring and FHIR search hints with automatic fallback.
        """
        epic_resource: Optional[Dict[str, Any]] = None
        epic_id: Optional[str] = None
        cerner_resource: Optional[Dict[str, Any]] = None
        cerner_id: Optional[str] = None

        # Build query demographics flat dict for scoring
        query_demo: Dict[str, Optional[str]] = {
            "given": params.given_name,
            "family": params.family_name,
            "birthdate": params.birthdate,
            "gender": params.gender,
            "phone": params.phone,
            "email": params.email,
            "address_line": params.address_line,
            "city": params.city,
            "state": params.state,
            "postal_code": params.postal_code,
        }

        # Aggregate MPI results from both systems
        epic_score: float = 0.0
        epic_status: str = "no_match"
        epic_reasons: List[str] = []
        cerner_score: float = 0.0
        cerner_status: str = "no_match"
        cerner_reasons: List[str] = []

        # --- Direct ID look-ups -------------------------------------------
        if params.epic_patient_id:
            try:
                out = await self.epic._read_patient(
                    FhirPatientReadInput(resource_id=params.epic_patient_id),
                    trace_id=trace_id,
                )
                epic_resource = out.resource
                epic_id = epic_resource.get("id")
                # Score the retrieved resource against query demographics
                if query_demo.get("family") or query_demo.get("given"):
                    epic_score, epic_status, epic_reasons = score_candidate(query_demo, epic_resource)
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
                if query_demo.get("family") or query_demo.get("given"):
                    cerner_score, cerner_status, cerner_reasons = score_candidate(query_demo, cerner_resource)
            except Exception as exc:
                logger.warning("Cerner read by ID failed: %s", exc)

        epic_candidates: List[Dict[str, Any]] = []
        cerner_candidates: List[Dict[str, Any]] = []

        # --- Demographic search (when no direct IDs) ----------------------
        if (
            not epic_id
            and not cerner_id
            and (params.given_name or params.family_name or params.birthdate)
        ):
            (epic_id, epic_resource, epic_score, epic_status, epic_reasons, epic_candidates) = (
                await self._demographic_search_epic(query_demo, trace_id)
            )
            (cerner_id, cerner_resource, cerner_score, cerner_status, cerner_reasons, cerner_candidates) = (
                await self._demographic_search_cerner(query_demo, trace_id)
            )

        # Pick the best MPI result to surface at the top level
        best_score = max(epic_score, cerner_score)
        best_status = epic_status if epic_score >= cerner_score else cerner_status
        best_reasons = epic_reasons if epic_score >= cerner_score else cerner_reasons
        W = _get_weights()
        max_score = sum(W.values())
        confidence = round((best_score / max_score) * 100.0, 1) if max_score > 0 else 0.0

        return UnifiedPatientOperationOutput(
            status="success",
            epic_resource_id=epic_id,
            epic_resource=epic_resource,
            cerner_resource_id=cerner_id,
            cerner_resource=cerner_resource,
            match_status=best_status,
            match_confidence=confidence,
            match_score=best_score,
            match_reasons=best_reasons,
            epic_match_status=epic_status,
            epic_match_score=epic_score,
            epic_candidates=epic_candidates,
            cerner_match_status=cerner_status,
            cerner_match_score=cerner_score,
            cerner_candidates=cerner_candidates,
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
        2. Run MPI pick_best search against target system.
        3. Route based on match status:
           - definite (≥90%) → update via HL7 ADT^A08
           - probable (70–90%) → run strict demographics_match tiebreaker → update or create
           - no_match (<70%) → create new patient
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

        # 2. MPI duplicate search in target ------------------------------------
        demo = extract_demographics(source_resource)
        (target_id, target_existing, match_score, match_status, match_reasons, all_candidates) = (
            await self._demographic_search(params.target_system, demo, trace_id)
        )

        # 3. Decide action from pick_best status ----------------------------
        #   definite  (≥90%) → update  (high-confidence duplicate)
        #   probable  (70–90%) → run demographics_match as tiebreaker
        #   no_match  (<70%)  → create new patient
        payload = ensure_resource_type(strip_source_metadata(source_resource, params.target_system))

        if match_status == "definite":
            sync_action = "update"
            logger.info(
                "MPI definite match → updating %s patient %s",
                params.target_system, target_id,
                extra={"trace_id": trace_id},
            )
        elif match_status == "probable":
            _, is_strict_match = demographics_match(demo, extract_demographics(target_existing))
            if is_strict_match:
                sync_action = "update"
                logger.info(
                    "MPI probable match, strict check passed → updating %s patient %s",
                    params.target_system, target_id,
                    extra={"trace_id": trace_id},
                )
            else:
                sync_action = "create"
                target_id = None
                logger.info(
                    "MPI probable match, strict check failed → creating new %s patient",
                    params.target_system,
                    extra={"trace_id": trace_id},
                )
        else:  # no_match
            sync_action = "create"
            logger.info(
                "MPI no_match → creating new %s patient",
                params.target_system,
                extra={"trace_id": trace_id},
            )

        # 4. Create or update in target ------------------------------------
        target_resource: Optional[Dict[str, Any]] = None
        if sync_action == "update":
            try:
                from node_wire_hl7.mllp import send_adt_a08
                logger.info("Routing %s patient %s update via HL7 v2 ADT^A08", params.target_system, target_id)
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

        # 5. Build output --------------------------------------------------
        result = UnifiedPatientOperationOutput(
            status="success",
            message=(
                f"Synced {params.source_system} patient {source_id} "
                f"→ {params.target_system} patient {target_id}"
            ),
            match_status=match_status,
            match_score=match_score,
            match_confidence=round(all_candidates[0]["pct"], 2) if all_candidates else None,
            match_reasons=match_reasons or [],
        )
        if params.source_system == "epic":
            result.epic_resource_id = source_id
            result.epic_resource = source_resource
            result.cerner_resource_id = target_id
            result.cerner_resource = target_resource
            result.cerner_match_status = match_status
            result.cerner_match_score = match_score
            result.cerner_candidates = all_candidates
        else:
            result.cerner_resource_id = source_id
            result.cerner_resource = source_resource
            result.epic_resource_id = target_id
            result.epic_resource = target_resource
            result.epic_match_status = match_status
            result.epic_match_score = match_score
            result.epic_candidates = all_candidates

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
        demo: Dict[str, Optional[str]],
        trace_id: str,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], float, str, List[str], List[Dict[str, Any]]]:
        if system == "epic":
            return await self._demographic_search_epic(demo, trace_id)
        return await self._demographic_search_cerner(demo, trace_id)

    async def _demographic_search_epic(
        self,
        query: Dict[str, Optional[str]],
        trace_id: str,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], float, str, List[str], List[Dict[str, Any]]]:
        """Search Epic for candidates and return the best MPI-scored match.

        Tries enriched params first (gender, phone, email); falls back to
        base params (family + given + birthdate) if no results are returned.
        """
        given = query.get("given")
        family = query.get("family")
        birthdate = query.get("birthdate")
        gender = query.get("gender")

        # Build FHIR search params
        base_params: Dict[str, str] = {}
        if given:
            base_params["given"] = given
        if family:
            base_params["family"] = family
        if birthdate:
            base_params["birthdate"] = birthdate

        enriched_params = dict(base_params)
        if gender:
            enriched_params["gender"] = gender

        resources: List[Dict[str, Any]] = []
        fallback_used = False

        # Attempt 1: enriched search
        try:
            out = await self.epic._search_patients(
                FhirPatientSearchInput(
                    given_name=given,
                    family_name=family,
                    birthdate=birthdate,
                    search_params={"gender": gender} if gender else None,
                ),
                trace_id=trace_id,
            )
            resources = [r for r in (out.resources or []) if r.get("resourceType") != "OperationOutcome"]
            logger.info(
                "MPI Epic enriched search | params=%s | candidates=%s",
                enriched_params, len(resources),
                extra={"trace_id": trace_id},
            )
        except Exception as exc:
            logger.warning("Epic enriched search failed (%s), will try base params", exc)

        # Attempt 2: fallback to base params if no results
        if not resources and (given or family or birthdate):
            fallback_used = True
            try:
                out = await self.epic._search_patients(
                    FhirPatientSearchInput(
                        given_name=given,
                        family_name=family,
                        birthdate=birthdate,
                    ),
                    trace_id=trace_id,
                )
                resources = [r for r in (out.resources or []) if r.get("resourceType") != "OperationOutcome"]
                logger.info(
                    "MPI Epic fallback search | params=%s | fallback=True | candidates=%s",
                    base_params, len(resources),
                    extra={"trace_id": trace_id},
                )
            except Exception as exc:
                logger.warning("Epic base search failed: %s", exc)

        return self._pick_best(query, resources, "epic", fallback_used, trace_id)

    async def _demographic_search_cerner(
        self,
        query: Dict[str, Optional[str]],
        trace_id: str,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], float, str, List[str], List[Dict[str, Any]]]:
        """Search Cerner for candidates and return the best MPI-scored match.

        Always tokenizes given name to first word (Cerner sandbox limitation).
        Tries gender enrichment first; automatically falls back to base params
        if Cerner returns an error (4xx) or empty results.
        """
        given = query.get("given")
        family = query.get("family")
        birthdate = query.get("birthdate")
        gender = query.get("gender")

        # Cerner sandbox: always use only the first word of given name
        safe_given = given.split()[0] if given else None

        resources: List[Dict[str, Any]] = []
        fallback_used = False

        # Attempt 1: enriched with gender
        if gender:
            try:
                out = await self.cerner._search_patients(
                    FhirCernerPatientSearchInput(
                        given_name=safe_given,
                        family_name=family,
                        birthdate=birthdate,
                        search_params={"gender": gender},
                    ),
                    trace_id=trace_id,
                )
                resources = [r for r in (out.resources or []) if r.get("resourceType") != "OperationOutcome"]
                logger.info(
                    "MPI Cerner enriched search | given=%s | gender=%s | candidates=%s",
                    safe_given, gender, len(resources),
                    extra={"trace_id": trace_id},
                )
            except Exception as exc:
                logger.warning("Cerner enriched search failed (%s), falling back", exc)

        # Attempt 2: base params if no results
        if not resources:
            fallback_used = bool(gender)
            try:
                out = await self.cerner._search_patients(
                    FhirCernerPatientSearchInput(
                        given_name=safe_given,
                        family_name=family,
                        birthdate=birthdate,
                    ),
                    trace_id=trace_id,
                )
                resources = [r for r in (out.resources or []) if r.get("resourceType") != "OperationOutcome"]
                logger.info(
                    "MPI Cerner base search | given=%s | fallback=%s | candidates=%s",
                    safe_given, fallback_used, len(resources),
                    extra={"trace_id": trace_id},
                )
            except Exception as exc:
                logger.warning("Cerner demographic search failed: %s", exc)

        return self._pick_best(query, resources, "cerner", fallback_used, trace_id)

    def _pick_best(
        self,
        query: Dict[str, Optional[str]],
        resources: List[Dict[str, Any]],
        system: str,
        fallback_used: bool,
        trace_id: str,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]], float, str, List[str], List[Dict[str, Any]]]:
        """Score all candidates and return the best matching one."""
        if not resources:
            logger.info(
                "MPI %s | no candidates found | fallback=%s",
                system, fallback_used,
                extra={"trace_id": trace_id},
            )
            return None, None, 0.0, "no_match", [], []

        # Score each candidate (cap at _MAX_CANDIDATES)
        scored: List[Tuple[float, float, str, List[str], Dict[str, Any]]] = []
        for res in resources[:_MAX_CANDIDATES]:
            raw, pct, status, reasons = score_candidate(query, res)
            scored.append((raw, pct, status, reasons, res))
            logger.debug(
                "MPI %s candidate %s | score=%.1f | pct=%.1f | status=%s",
                system, res.get("id"), raw, pct, status,
                extra={"trace_id": trace_id},
            )

        # Sort descending by score
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, pct, best_status, best_reasons, best_resource = scored[0]
        best_id = best_resource.get("id")

        logger.info(
            "MPI %s | best=%s | score=%.1f | pct=%.1f%% | status=%s | fallback=%s",
            system, best_id, best_score, pct, best_status, fallback_used,
            extra={"trace_id": trace_id},
        )

        all_candidates = [
            {"id": res.get("id"), "resource": res, "score": s, "pct": p, "status": st, "reasons": r}
            for s, p, st, r, res in scored
        ]

        # Only return a match if it meets the probable threshold
        if best_status == "no_match":
            return None, None, best_score, best_status, best_reasons, all_candidates

        return best_id, best_resource, best_score, best_status, best_reasons, all_candidates

    async def _create_patient(
        self, system: str, payload: Dict[str, Any], trace_id: str
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if system == "epic":
            out = await self.epic._create_patient(
                FhirPatientCreateInput(resource=payload), trace_id=trace_id
            )
        else:
            out = await self.cerner._create_patient(
                FhirCernerPatientCreateInput(resource=payload), trace_id=trace_id
            )
        return out.resource_id, out.resource
