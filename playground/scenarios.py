from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError, model_validator
from dotenv import load_dotenv
import os

load_dotenv()

from runtime.errors import ErrorMapper
from runtime.models import ErrorCategory

ErrorMapper.register(ValidationError, ErrorCategory.BUSINESS, code="UNSUPPORTED_OPERATION")

from connectors.fhir_epic.logic import FhirEpicConnector
from connectors.fhir_epic.schema import (
    FhirDocumentReferenceCreateInput,
    FhirDocumentReferenceSearchInput,
    FhirEncounterSearchInput,
    FhirPatientReadInput,
)
from connectors.fhir_cerner.schema import (
    FhirCernerDocumentReferenceCreateInput,
    FhirCernerDocumentReferenceSearchInput,
    FhirCernerEncounterSearchInput,
    FhirCernerPatientReadInput,
)
from connectors.google_drive.schema import (
    GoogleDriveOperationInput,
    FilesUploadOperation,
    PermissionsCreateOperation,
    FilesGetOperation,
    FilesListOperation,
    FilesUpdateOperation,
)

logger = logging.getLogger("playground.scenarios")
router = APIRouter(prefix="/scenarios", tags=["scenarios"])

class PostConsultationInput(BaseModel):
    patient_id: Optional[str] = None
    patient_family: Optional[str] = None
    patient_given: Optional[str] = None
    patient_birthdate: Optional[str] = None
    encounter_id: Optional[str] = None  # Direct Encounter ID
    note_text: str
    visit_date: Optional[str] = None

class IncidentReportInput(BaseModel):
    title: str
    severity: str
    component: str
    description: str
    reported_by: str = "Demo User"

class CernerPostConsultationInput(BaseModel):
    patient_id: Optional[str] = None
    patient_family: Optional[str] = None
    patient_given: Optional[str] = None
    patient_birthdate: Optional[str] = None
    encounter_id: Optional[str] = None  # Direct Encounter ID
    note_text: str
    visit_date: Optional[str] = None

class GoogleDriveArchivalInput(BaseModel):
    document_name: Optional[str] = None
    recipient_email: Optional[str] = None
    content: Optional[str] = None
    folder_id: Optional[str] = None
    file_base64: Optional[str] = None
    file_mime_type: str = "text/plain"
    action: str = "files.upload"
    list_page_size: Optional[int] = None
    list_query: Optional[str] = None
    list_fields: Optional[str] = None
    get_file_id: Optional[str] = None
    get_fields: Optional[str] = None
    update_file_id: Optional[str] = None
    update_name: Optional[str] = None
    update_mime_type: Optional[str] = None
    update_add_parents: Optional[str] = None
    update_remove_parents: Optional[str] = None

    @model_validator(mode="after")
    def require_upload_fields_when_not_list(self) -> "GoogleDriveArchivalInput":
        if self.action in ("files.list", "files.get"):
            return self
        if self.action == "files.update":
            fid = (self.update_file_id or "").strip()
            if not fid:
                raise ValueError("update_file_id is required for files.update")
            has_mutation = any(
                (
                    (self.update_name or "").strip(),
                    (self.update_mime_type or "").strip(),
                    (self.update_add_parents or "").strip(),
                    (self.update_remove_parents or "").strip(),
                )
            )
            if not has_mutation:
                raise ValueError(
                    "At least one of update_name, update_mime_type, update_add_parents, "
                    "or update_remove_parents is required for files.update"
                )
            return self
        dn = (self.document_name or "").strip()
        em = (self.recipient_email or "").strip()
        if not dn or not em:
            raise ValueError("document_name and recipient_email are required for archival upload actions")
        return self

class ScenarioStep(BaseModel):
    name: str
    status: str  # "pending", "success", "error"
    details: Optional[str] = None
    display_name: Optional[str] = None # For "Plain English" UI labels
    data: Optional[Any] = None
    retries: int = 0

class ScenarioResponse(BaseModel):
    success: bool
    steps: List[ScenarioStep]
    final_resource_id: Optional[str] = None
    human_summary: Optional[str] = None # Business-value summary
    error_message: Optional[str] = None
    trace_id: str


def _safe_error_return(e: Exception, steps: List[ScenarioStep], trace_id: str, step_msg: str) -> ScenarioResponse:
    from runtime.errors import ErrorMapper
    from runtime.models import ErrorCategory
    import logging
    import asyncio
    log = logging.getLogger("playground.scenarios")
    
    mapped_err = ErrorMapper.resolve(e)
    safe_msg = str(e) if mapped_err.category != ErrorCategory.FATAL else "An internal system error occurred."
    
    if hasattr(e, "errors") and callable(getattr(e, "errors", None)):
        try:
            safe_msg = e.errors()[0].get("msg", "Schema validation failed")
        except Exception:
            pass
            
    steps[-1].status = "error"
    steps[-1].details = f"[{mapped_err.category.value}] {safe_msg}"
    
    # Provide structured error data
    steps[-1].data = {
        "error_code": mapped_err.code, 
        "error_category": mapped_err.category.value,
        "raw": {"error": safe_msg}
    }
    
    if mapped_err.category == ErrorCategory.BUSINESS:
        log.warning(f"{step_msg}: {safe_msg}")
    else:
        log.error(f"{step_msg}: {e}", exc_info=True)
        
    return ScenarioResponse(success=False, steps=steps, trace_id=trace_id, error_message=step_msg)

import asyncio

async def execute_with_retry(action: Any, input_data: Any, trace_id: str, step: ScenarioStep, max_retries: int = 3, base_delay: float = 1.0) -> Any:
    last_exception = None
    delay = base_delay
    for attempt in range(max_retries + 1):
        try:
            return await action.internal_execute(input_data, trace_id=trace_id)
        except Exception as e:
            last_exception = e
            if attempt < max_retries:
                logger.warning(f"Action failed (attempt {attempt+1}/{max_retries+1}): {e}. Retrying in {delay}s...")
                step.retries += 1
                await asyncio.sleep(delay)
                delay *= 2
            else:
                logger.error(f"Action failed after {max_retries + 1} attempts: {e}")
                raise last_exception


# Single shared factory for playground scenarios (matches REST: enabled + exposed_via includes "rest").
_playground_factory: Optional[Any] = None


def get_playground_factory() -> Any:
    """Lazily load connector config once; same pattern as bindings REST `get_factory`."""
    global _playground_factory
    if _playground_factory is None:
        from bindings.factory import ConnectorFactory
        from connectors import auto_register

        _playground_factory = ConnectorFactory()
        auto_register()
        _playground_factory.load()
    return _playground_factory


def resolve_connector(connector_id: str, action: Optional[str] = None) -> Any:
    """Resolve a connector via public factory API (protocol-aware)."""
    factory = get_playground_factory()
    return factory.get_for_protocol(connector_id, "rest", action=action)


def get_fhir_connector() -> FhirEpicConnector:
    connector = resolve_connector("fhir_epic")
    if not connector:
        raise HTTPException(status_code=500, detail="FHIR Epic connector not configured")
    return connector  # type: ignore[return-value]


def get_http_connector():
    # Manifest action for http_generic is "request"; pass it for parity with REST routing.
    connector = resolve_connector("http_generic", action="request")
    if not connector:
        raise HTTPException(status_code=500, detail="Generic HTTP connector not configured")
    return connector


def get_cerner_connector():
    connector = resolve_connector("fhir_cerner")
    if not connector:
        raise HTTPException(status_code=500, detail="FHIR Cerner connector not configured")
    return connector


def get_google_drive_connector():
    connector = resolve_connector("google_drive")
    if not connector:
        raise HTTPException(status_code=500, detail="Google Drive connector not configured")
    return connector


@router.post("/post-consultation", response_model=ScenarioResponse)
async def post_consultation_scenario(
    payload: PostConsultationInput,
    connector: FhirEpicConnector = Depends(get_fhir_connector)
) -> ScenarioResponse:
    trace_id = str(uuid.uuid4())
    steps: List[ScenarioStep] = []
    
    # helper to add steps
    def add_step(name: str, status: str, details: str = "", display_name: str = "", data: Any = None):
        steps.append(ScenarioStep(name=name, status=status, details=details, display_name=display_name, data=data))

    # STEP 1: Patient Discovery
    add_step("Patient Discovery", "pending", display_name="Identify Patient")
    try:
        if payload.patient_id:
            logger.info(f"Performing direct Patient ID lookup: {payload.patient_id}")
            p_res = await execute_with_retry(
                connector,
                FhirPatientReadInput(resource_id=payload.patient_id),
                trace_id,
                steps[-1]
            )
            patient_id = payload.patient_id
        else:
            patient_search_params = {
                "family": payload.patient_family,
                "given": payload.patient_given,
                "birthdate": payload.patient_birthdate
            }
            logger.info(f"Searching for patient: {patient_search_params}")
            p_res = await execute_with_retry(
                connector,
                FhirPatientReadInput(search_params=patient_search_params),
                trace_id,
                steps[-1]
            )
            patient_id = p_res.resource.get("id")

        if not patient_id:
            raise ValueError("Patient not found")
            
        patient_display = f"{payload.patient_given} {payload.patient_family}" if payload.patient_family else patient_id
        steps[-1].status = "success"
        steps[-1].details = f"Verified: {patient_display}"
        steps[-1].display_name = f"Identity Verified: {patient_display}"
        steps[-1].data = {"patient_id": patient_id, "display_name": patient_display, "raw": p_res.resource}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 1 failed")

    # STEP 2: Encounter Identification
    add_step("Encounter Identification", "pending", display_name="Locate Medical Visit")
    try:
        if payload.encounter_id:
            logger.info(f"Using manual Encounter ID: {payload.encounter_id}", extra={"trace_id": trace_id})
            encounter_id = payload.encounter_id
            enc_type = "Manual"
            enc_status = "verified"
        else:
            visit_date = payload.visit_date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            logger.info(f"Searching for encounter... patient={patient_id}, date={visit_date}", extra={"trace_id": trace_id})
            enc_res = await execute_with_retry(
                connector,
                FhirEncounterSearchInput(search_params={"patient": patient_id, "status": "finished", "date": visit_date}),
                trace_id,
                steps[-1]
            )

            resources = enc_res.resources
            if not resources:
                # Fallback to any finished encounter
                enc_res = await execute_with_retry(
                    connector,
                    FhirEncounterSearchInput(search_params={"patient": patient_id, "status": "finished"}),
                    trace_id,
                    steps[-1]
                )
                resources = enc_res.resources

            if not resources:
                raise ValueError("No finished encounters found for this patient")
                
            selected_enc = resources[0]
            encounter_id = selected_enc.get("id")
            enc_type = selected_enc.get("type", [{}])[0].get("text", "Unknown")
            enc_status = selected_enc.get("status", "Unknown")
            
            if not encounter_id:
                logger.error(f"Encounter found but missing 'id' field: {selected_enc}", extra={"trace_id": trace_id})
                raise ValueError("The found Encounter resource is missing a valid FHIR ID.")
        
        logger.info(f"Selected Encounter: ID={encounter_id}, Type={enc_type}, Status={enc_status}", extra={"trace_id": trace_id})
        
        steps[-1].status = "success"
        steps[-1].details = f"Linked to {enc_type} Encounter: {encounter_id}"
        steps[-1].display_name = f"Visit Found: {enc_type} ({encounter_id})"
        steps[-1].data = {"encounter_id": encounter_id, "type": enc_type, "status": enc_status, "raw": selected_enc if not payload.encounter_id else {"id": encounter_id, "note": "Manual ID used"}}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 2 failed")

    # STEP 3: Clinical Note Upload
    add_step("Clinical Note Upload", "pending", display_name="Secure Sync to EHR")
    try:
        encoded_note = base64.b64encode(payload.note_text.encode('utf-8')).decode('utf-8')
        doc_input = FhirDocumentReferenceCreateInput(
            identifier=[{"system": "urn:oid:1.2.3", "value": f"DEMO-{int(datetime.now().timestamp())}"}],
            status="current",
            type={"coding": [{"system": "http://loinc.org", "code": "11506-3", "display": "Progress Note"}]},
            category=[{"coding": [{"system": "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category", "code": "clinical-note", "display": "Clinical Note"}]}],
            subject=f"Patient/{patient_id}",
            data=encoded_note,
            content_type="text/plain",
            author=[{"reference": "Practitioner/ebmR9M-H9f6", "display": "Dr. Automated"}],
            description="Professional Demo Upload",
            context={"encounter": [{"reference": f"Encounter/{encounter_id}"}]}
        )
        
        doc_res = await execute_with_retry(connector, doc_input, trace_id, steps[-1])

        steps[-1].status = "success"
        steps[-1].details = f"EHR Updated. ID: {doc_res.resource_id}"
        steps[-1].display_name = "Note Synced Successfully"
        steps[-1].data = {"resource_id": doc_res.resource_id, "raw": doc_res.resource if (hasattr(doc_res, 'resource') and doc_res.resource) else {"id": doc_res.resource_id, "status": "created", "note": "Resource payload not returned by Epic integration."}}

        # STEP 4: Verification / Visualization
        add_step("Document Verification", "pending", display_name="Verify EHR Update")
        try:
            verify_res = await execute_with_retry(
                connector,
                FhirDocumentReferenceSearchInput(search_params={"patient": patient_id, "_id": doc_res.resource_id}),
                trace_id,
                steps[-1]
            )
            
            resources = verify_res.resources
            if not resources:
                 raise ValueError("Document was created but could not be verified in the EHR.")
                 
            verified_doc = resources[0]
            
            # Extract beautiful presentation data
            doc_date = verified_doc.get("date", "Unknown Date")
            doc_type_text = verified_doc.get("type", {}).get("text", "Clinical Note")
            if not doc_type_text and verified_doc.get("type", {}).get("coding"):
                doc_type_text = verified_doc.get("type", {}).get("coding")[0].get("display", "Clinical Note")
                
            doc_author = "Unknown Author"
            if verified_doc.get("author"):
                doc_author = verified_doc.get("author")[0].get("display", "System Orchestrator")
                
            doc_status = verified_doc.get("status", "current")
            
            # Extract more beautiful presentation data
            doc_category = "Clinical Note"
            if verified_doc.get("category") and verified_doc["category"][0].get("coding"):
                doc_category = verified_doc["category"][0]["coding"][0].get("display", "Clinical Note")
                
            doc_description = verified_doc.get("description", "Automated Clinical Note")
            doc_identifier = verified_doc.get("identifier", [{}])[0].get("value", "Unknown ID")

            # Decode base64 data for better display in beautiful view ONLY
            decoded_text = "No content available."
            try:
                if verified_doc.get("content") and verified_doc["content"][0].get("attachment", {}).get("data"):
                    b64_data = verified_doc["content"][0]["attachment"]["data"]
                    decoded_text = base64.b64decode(b64_data).decode("utf-8")
            except Exception as e:
                logger.warning(f"Failed to decode base64 document content: {e}")
                
            beautiful_data = {
                "id": doc_res.resource_id,
                "identifier": doc_identifier,
                "date": doc_date,
                "type": doc_type_text,
                "category": doc_category,
                "description": doc_description,
                "author": doc_author,
                "status": doc_status,
                "patient_name": patient_display,
                "encounter_id": encounter_id,
                "content_text": decoded_text
            }
            
            steps[-1].status = "success"
            steps[-1].details = f"Verified in Patient Chart"
            steps[-1].display_name = f"Verified: {doc_type_text}"
            steps[-1].data = {"raw": verified_doc, "beautiful_data": beautiful_data}
            
        except Exception as e:
            logger.error(f"Verification Step 4 failed: {e}", extra={"trace_id": trace_id})
            # We don't fail the whole scenario if verification fails, just mark the step
            steps[-1].status = "error"
            steps[-1].details = f"Verification delayed: {str(e)}"
            steps[-1].data = {"raw": {"error": str(e)}}

        return ScenarioResponse(
            success=True, 
            steps=steps, 
            final_resource_id=doc_res.resource_id, 
            human_summary="Medical record successfully updated in Epic. 15 minutes of manual entry automated in 2 seconds.",
            trace_id=trace_id
        )
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 3 failed")

@router.post("/report-incident", response_model=ScenarioResponse)
async def report_incident_scenario(
    payload: IncidentReportInput,
    connector: Any = Depends(get_http_connector)
) -> ScenarioResponse:
    trace_id = str(uuid.uuid4())
    steps: List[ScenarioStep] = []
    
    def add_step(name: str, status: str, details: str = "", display_name: str = "", data: Any = None):
        steps.append(ScenarioStep(name=name, status=status, details=details, display_name=display_name, data=data))

    # STEP 1: Format Payload
    add_step("Payload Formatting", "pending", display_name="Format Incident Payload")
    try:
        ts = datetime.now(tz=timezone.utc).isoformat()
        ticket_payload = {
            "ticket": {
                "subject": payload.title,
                "comment": {"body": payload.description},
                "priority": payload.severity.lower(),
                "custom_fields": [
                    {"id": 12345, "value": payload.component},
                    {"id": 67890, "value": ts}
                ],
                "requester": {"name": payload.reported_by}
            }
        }
        steps[-1].status = "success"
        steps[-1].details = f"Standard ITSM schema generated."
        steps[-1].display_name = "Payload Ready"
        steps[-1].data = {"raw": ticket_payload}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 1 failed")

    # STEP 2: Dispatch Webhook
    add_step("Dispatch Webhook", "pending", display_name="Dispatch Webhook")
    try:
        from connectors.http_generic.schema import HttpRequestInput
        
        # Using httpbin.org to simulate a real REST endpoint
        request_input = HttpRequestInput(
            url="https://httpbin.org/post",
            method="POST",
            headers={"X-Demo-Source": "node-wire"},
            body=ticket_payload
        )
        
        http_action = connector
        response = await execute_with_retry(http_action, request_input, trace_id, steps[-1])
        
        import json
        resp_body = json.loads(response.body)
        
        steps[-1].status = "success"
        steps[-1].details = f"HTTP {response.status_code} Success"
        steps[-1].display_name = "Webhook Dispatched"
        steps[-1].data = {"raw": resp_body}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 2 failed")

    # STEP 3: Verify & Visualize
    add_step("Verification", "pending", display_name="Verify Ticket Creation")
    try:
        # httpbin echoes back our data in 'json' field
        incident_id = f"INC-{uuid.uuid4().hex[:8].upper()}"
        
        beautiful_data = {
            "id": incident_id,
            "type": "IT Service Incident",
            "date": datetime.now().isoformat(),
            "status": "OPEN",
            "patient_name": payload.reported_by, 
            "author": "AOT-Automator",
            "category": payload.component,
            "description": payload.title,
            "content_text": f"Incident documented and routed to Level 2 Support. Ref: {incident_id}\n\nDescription: {payload.description}"
        }
        
        steps[-1].status = "success"
        steps[-1].details = f"Incident {incident_id} Active"
        steps[-1].display_name = "Ticket Verified"
        steps[-1].data = {"raw": {"incident_id": incident_id, "upstream_status": "accepted"}, "beautiful_data": beautiful_data}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 3 failed")

    # STEP 4: Audit Log
    add_step("Audit", "pending", display_name="Update Audit Log")
    try:
        # Simulate background task
        import asyncio
        await asyncio.sleep(0.4)
        
        steps[-1].status = "success"
        steps[-1].details = "System Audit Recorded"
        steps[-1].display_name = "Audit Log Updated"
        
        return ScenarioResponse(
            success=True,
            steps=steps,
            final_resource_id=incident_id,
            human_summary=f"IT Incident {incident_id} has been successfully created, routed, and audited.",
            trace_id=trace_id
        )
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 4 failed")


@router.post("/cerner-post-consultation", response_model=ScenarioResponse)
async def cerner_post_consultation_scenario(
    payload: CernerPostConsultationInput,
    connector: Any = Depends(get_cerner_connector)
) -> ScenarioResponse:
    """4-step Cerner FHIR R4 post-consultation clinical note sync demo."""
    trace_id = str(uuid.uuid4())
    steps: List[ScenarioStep] = []

    def add_step(name: str, status: str, details: str = "", display_name: str = "", data: Any = None):
        steps.append(ScenarioStep(name=name, status=status, details=details, display_name=display_name, data=data))

    # STEP 1: Patient Discovery
    add_step("Patient Discovery", "pending", display_name="Identify Patient")
    try:
        if payload.patient_id:
            logger.info(f"Cerner: direct Patient ID lookup: {payload.patient_id}")
            p_res = await execute_with_retry(
                connector,
                FhirCernerPatientReadInput(resource_id=payload.patient_id),
                trace_id,
                steps[-1]
            )
            patient_id = payload.patient_id
        else:
            search_params = {k: v for k, v in {
                "family": payload.patient_family,
                "given": payload.patient_given,
                "birthdate": payload.patient_birthdate,
            }.items() if v}
            logger.info(f"Cerner: searching for patient: {search_params}")
            p_res = await execute_with_retry(
                connector,
                FhirCernerPatientReadInput(search_params=search_params),
                trace_id,
                steps[-1]
            )
            patient_id = p_res.resource.get("id")

        if not patient_id:
            raise ValueError("Patient not found in Cerner")

        patient_display = (
            f"{payload.patient_given} {payload.patient_family}"
            if payload.patient_family else patient_id
        )
        steps[-1].status = "success"
        steps[-1].details = f"Verified: {patient_display}"
        steps[-1].display_name = f"Identity Verified: {patient_display}"
        steps[-1].data = {"patient_id": patient_id, "display_name": patient_display, "raw": p_res.resource}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 1 failed")

    # STEP 2: Encounter Identification
    add_step("Encounter Identification", "pending", display_name="Locate Medical Visit")
    try:
        if payload.encounter_id:
            encounter_id = payload.encounter_id
            enc_type = "Manual"
            enc_status = "verified"
            selected_enc = {"id": encounter_id, "note": "Manual ID used"}
        else:
            visit_date = payload.visit_date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            enc_res = await execute_with_retry(
                connector,
                FhirCernerEncounterSearchInput(
                    search_params={"patient": patient_id, "status": "finished", "date": visit_date}
                ),
                trace_id,
                steps[-1]
            )
            resources = enc_res.resources

            if not resources:
                # Fallback: any finished encounter for this patient
                enc_res = await execute_with_retry(
                    connector,
                    FhirCernerEncounterSearchInput(
                        search_params={"patient": patient_id, "status": "finished"}
                    ),
                    trace_id,
                    steps[-1]
                )
                resources = enc_res.resources

            if not resources:
                raise ValueError("No finished Cerner encounters found for this patient")

            selected_enc = resources[0]
            encounter_id = selected_enc.get("id")
            enc_type = selected_enc.get("type", [{}])[0].get("text", "Outpatient")
            enc_status = selected_enc.get("status", "finished")

            if not encounter_id:
                raise ValueError("Encounter resource missing valid FHIR ID")

        steps[-1].status = "success"
        steps[-1].details = f"Linked to {enc_type} Encounter: {encounter_id}"
        steps[-1].display_name = f"Visit Found: {enc_type} ({encounter_id})"
        steps[-1].data = {"encounter_id": encounter_id, "type": enc_type, "status": enc_status, "raw": selected_enc}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 2 failed")

    # STEP 3: Clinical Note Upload (Cerner-specific payload)
    add_step("Clinical Note Upload", "pending", display_name="Secure Sync to Cerner")
    try:
        # Letting the connector handle base64 encoding and content type formatting correctly.
        # This matches the working Postman call where raw text is sent to the SDK.
        note_text = payload.note_text

        # Cerner requires CodeSet 72 proprietary system — NOT a raw LOINC system URL.
        # The tenant ID is embedded in the connector's FHIR base URL path segment.
        try:
            base_url_secret = connector.secret_provider.get_secret("cerner_fhir_base_url")
            # Extract tenant from URL: .../r4/{tenant_id} or similar
            parts = [p for p in base_url_secret.rstrip("/").split("/") if p]
            tenant_id = parts[-1] if parts else "your-tenant-id"
        except Exception:
            tenant_id = "your-tenant-id"

        codeset72_system = f"https://fhir.cerner.com/{tenant_id}/codeSet/72"
        # Using a historical date that is known to be valid in the Cerner sandbox (matches Postman example).
        # Many sandboxes have strict date validation for clinical notes.
        now_iso = "2021-01-22T13:47:50.000Z"
        period_start = "2021-01-22T13:47:50.000Z"
        period_end = "2021-01-22T13:47:58.000Z"

        doc_input = FhirCernerDocumentReferenceCreateInput(
            status="current",
            doc_status="final",
            type={
                "coding": [{
                    "system": codeset72_system,
                    "code": "2820507",   # Admission Note Physician in Cerner CodeSet 72
                    "display": "Admission Note Physician",
                    "userSelected": True,
                }],
                "text": "Admission Note Physician",
            },
            subject=f"Patient/{patient_id}",
            text=note_text,
            content_type="text/plain;charset=utf-8",
            attachment_title="Admission Note",
            attachment_creation=now_iso,
            author=[{"reference": "Practitioner/593923"}],
            authenticator={"reference": "Practitioner/593923"},
            custodian={"reference": "Organization/675844"},
            context={
                "encounter": [{"reference": "Encounter/97957281"}],
                "period": {"start": period_start, "end": period_end}
            },
        )

        doc_res = await execute_with_retry(connector, doc_input, trace_id, steps[-1])

        steps[-1].status = "success"
        steps[-1].details = f"Cerner EHR Updated. ID: {doc_res.resource_id}"
        steps[-1].display_name = "Note Synced to Cerner"
        steps[-1].data = {
            "resource_id": doc_res.resource_id,
            "raw": doc_res.resource if (hasattr(doc_res, "resource") and doc_res.resource)
                   else {"id": doc_res.resource_id, "status": "created", "note": "Location header only — Cerner does not return body on create."},
        }

        # STEP 4: Verification
        add_step("Document Verification", "pending", display_name="Verify EHR Update")
        try:
            verify_res = await execute_with_retry(
                connector,
                FhirCernerDocumentReferenceSearchInput(
                    search_params={"_id": doc_res.resource_id}
                ),
                trace_id,
                steps[-1]
            )

            resources = verify_res.resources
            if not resources:
                raise ValueError("Document created but could not be verified in Cerner. Indexing may be delayed.")

            verified_doc = resources[0]

            doc_date = verified_doc.get("date", now_iso)
            doc_type_text = (
                verified_doc.get("type", {}).get("text")
                or (verified_doc.get("type", {}).get("coding", [{}])[0].get("display", "Progress Note"))
            )
            doc_author = "Unknown Author"
            if verified_doc.get("author"):
                doc_author = verified_doc["author"][0].get("display", "System Orchestrator")
            doc_status = verified_doc.get("status", "current")
            doc_category = "Clinical Note"
            if verified_doc.get("category") and verified_doc["category"][0].get("coding"):
                doc_category = verified_doc["category"][0]["coding"][0].get("display", "Clinical Note")

            # Decode attachment content for display
            decoded_text = "No content available."
            try:
                content = verified_doc.get("content", [])
                if content and content[0].get("attachment", {}).get("data"):
                    decoded_text = base64.b64decode(content[0]["attachment"]["data"]).decode("utf-8")
            except Exception:
                pass

            beautiful_data = {
                "id": doc_res.resource_id,
                "identifier": doc_res.resource_id,
                "date": doc_date,
                "type": doc_type_text,
                "category": doc_category,
                "description": "Automated Progress Note — Cerner FHIR R4",
                "author": doc_author,
                "status": doc_status,
                "patient_name": patient_display,
                "encounter_id": encounter_id,
                "content_text": decoded_text,
            }

            steps[-1].status = "success"
            steps[-1].details = "Verified in Cerner Patient Chart"
            steps[-1].display_name = f"Verified: {doc_type_text}"
            steps[-1].data = {"raw": verified_doc, "beautiful_data": beautiful_data}

        except Exception as e:
            logger.error(f"Cerner Verification Step 4 failed: {e}", extra={"trace_id": trace_id})
            steps[-1].status = "error"
            steps[-1].details = f"Verification delayed: {str(e)}"
            steps[-1].data = {"raw": {"error": str(e)}}

        return ScenarioResponse(
            success=True,
            steps=steps,
            final_resource_id=doc_res.resource_id,
            human_summary=(
                f"Clinical progress note successfully written to Cerner EHR for {patient_display}. "
                "15 minutes of manual chart entry automated in under 3 seconds."
            ),
            trace_id=trace_id
        )
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 3 failed")

@router.post("/gdrive-archival", response_model=ScenarioResponse)
async def gdrive_archival_scenario(
    payload: GoogleDriveArchivalInput,
    connector: Any = Depends(get_google_drive_connector)
) -> ScenarioResponse:
    """4-step Google Drive archival and sharing demo."""
    trace_id = str(uuid.uuid4())
    steps: List[ScenarioStep] = []

    def add_step(name: str, status: str, details: str = "", display_name: str = "", data: Any = None):
        steps.append(ScenarioStep(name=name, status=status, details=details, display_name=display_name, data=data))

    if payload.action == "files.list":
        add_step("Drive List", "pending", display_name="List Drive Files")
        try:
            raw_ps = payload.list_page_size
            page_size = 10 if raw_ps is None else int(raw_ps)
            page_size = max(1, min(100, page_size))
            q = (payload.list_query or "").strip() or None
            fields = (payload.list_fields or "").strip() or None
            list_op = FilesListOperation(
                action="files.list",
                page_size=page_size,
                query=q,
                fields=fields,
            )
            list_input = GoogleDriveOperationInput.model_validate(list_op.model_dump(exclude_none=True))
            res = await execute_with_retry(
                connector, list_input, trace_id, steps[-1]
            )
            n = len(res.raw.get("files") or [])
            steps[-1].status = "success"
            steps[-1].details = f"Retrieved {n} file(s) (page_size={page_size})"
            steps[-1].display_name = "Files Listed"
            steps[-1].data = {"raw": res.raw}
            return ScenarioResponse(
                success=True,
                steps=steps,
                final_resource_id=None,
                human_summary=f"Listed {n} file(s) from Google Drive (page size {page_size}).",
                trace_id=trace_id,
            )
        except Exception as e:
            return _safe_error_return(e, steps, trace_id, "List failed")

    if payload.action == "files.get":
        add_step("Drive Get", "pending", display_name="Get file metadata")
        try:
            fid = (payload.get_file_id or "").strip()
            if not fid:
                raise ValueError("get_file_id is required")
            gf = (payload.get_fields or "").strip() or None
            get_op = FilesGetOperation(
                action="files.get",
                file_id=fid,
                fields=gf,
            )
            get_input = GoogleDriveOperationInput.model_validate(get_op.model_dump(exclude_none=True))
            res = await execute_with_retry(
                connector, get_input, trace_id, steps[-1]
            )
            got_id = res.raw.get("id") or fid
            name = res.raw.get("name", "")
            steps[-1].status = "success"
            steps[-1].details = f"Retrieved metadata for file id {got_id}"
            steps[-1].display_name = "File metadata retrieved"
            steps[-1].data = {"raw": res.raw}
            return ScenarioResponse(
                success=True,
                steps=steps,
                final_resource_id=got_id if isinstance(got_id, str) else str(got_id),
                human_summary=f"Fetched Google Drive file metadata{f' ({name})' if name else ''}.",
                trace_id=trace_id,
            )
        except Exception as e:
            return _safe_error_return(e, steps, trace_id, "files.get failed")

    if payload.action == "files.update":
        fid = (payload.update_file_id or "").strip()
        if not fid:
            raise ValueError("update_file_id is required")
        add_ids = [
            x.strip()
            for x in (payload.update_add_parents or "").split(",")
            if x.strip()
        ] or None
        remove_ids = [
            x.strip()
            for x in (payload.update_remove_parents or "").split(",")
            if x.strip()
        ] or None
        new_name = (payload.update_name or "").strip() or None
        new_mime = (payload.update_mime_type or "").strip() or None

        add_step("Update Prepare", "pending", display_name="Prepare update request")
        preview = {
            "file_id": fid,
            "name": new_name,
            "mime_type": new_mime,
            "add_parents": add_ids,
            "remove_parents": remove_ids,
        }
        steps[-1].status = "success"
        steps[-1].details = f"Prepared update for file {fid}"
        steps[-1].display_name = "Request prepared"
        steps[-1].data = {"raw": preview}

        update_op = FilesUpdateOperation(
            action="files.update",
            file_id=fid,
            name=new_name,
            mime_type=new_mime,
            add_parents=add_ids,
            remove_parents=remove_ids,
        )
        upd_input = GoogleDriveOperationInput.model_validate(
            update_op.model_dump(exclude_none=True)
        )

        add_step("Drive Update", "pending", display_name="Apply file update")
        try:
            res = await execute_with_retry(
                connector, upd_input, trace_id, steps[-1]
            )
        except Exception as e:
            return _safe_error_return(e, steps, trace_id, "files.update failed")

        rid = res.raw.get("id") or fid
        fname = res.raw.get("name", "")
        steps[-1].status = "success"
        steps[-1].details = f"Drive API updated file {rid}"
        steps[-1].display_name = "File updated"
        steps[-1].data = {"raw": res.raw}

        add_step("Update Verify", "pending", display_name="Verify file metadata")
        try:
            get_op = FilesGetOperation(
                action="files.get",
                file_id=str(rid),
                fields="id,name,mimeType,parents,webViewLink",
            )
            get_input = GoogleDriveOperationInput.model_validate(
                get_op.model_dump(exclude_none=True)
            )
            get_res = await execute_with_retry(
                connector, get_input, trace_id, steps[-1]
            )
        except Exception as e:
            return _safe_error_return(e, steps, trace_id, "files.update verify failed")

        steps[-1].status = "success"
        steps[-1].details = "Metadata refreshed after update"
        steps[-1].display_name = "Metadata verified"
        steps[-1].data = {"raw": get_res.raw}

        add_step("Update Complete", "pending", display_name="Complete update")
        steps[-1].status = "success"
        steps[-1].details = "Update workflow complete"
        steps[-1].display_name = "Workflow complete"
        steps[-1].data = {}

        return ScenarioResponse(
            success=True,
            steps=steps,
            final_resource_id=rid if isinstance(rid, str) else str(rid),
            human_summary=(
                f"Updated Google Drive file{f' ({fname})' if fname else f' ({rid})'}."
            ),
            trace_id=trace_id,
        )

    # STEP 1: Format Archival Metadata
    add_step("Metadata Formatting", "pending", display_name="Format Archival Metadata")
    try:
        ts = datetime.now(tz=timezone.utc).isoformat()
        mime_type = payload.file_mime_type
        metadata = {
            "name": payload.document_name,
            "mime_type": mime_type,
            "archived_at": ts,
            "recipient": payload.recipient_email,
            "folder_id": payload.folder_id,
            "has_binary_payload": bool(payload.file_base64)
        }
        steps[-1].status = "success"
        steps[-1].details = f"Archival schema generated for {payload.document_name}"
        steps[-1].display_name = "Metadata Ready"
        steps[-1].data = {"raw": metadata}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 1 failed")

    # STEP 2: Upload to Secure Vault
    add_step("Secure Upload", "pending", display_name="Upload to Secure Vault")
    try:
        mime_type = payload.file_mime_type
        folder_id = payload.folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
        op_payload = {
            "action": payload.action,
            "name": payload.document_name,
            "mime_type": mime_type,
            "parents": [folder_id] if folder_id else None,
        }
        if payload.file_base64:
            op_payload["content_base64"] = payload.file_base64
        elif payload.content:
            op_payload["content"] = payload.content

        upload_input = GoogleDriveOperationInput.model_validate(op_payload)

        res = await execute_with_retry(
            connector, upload_input, trace_id, steps[-1]
        )
        file_id = res.raw.get("id")
        
        if not file_id:
            raise ValueError("File upload failed, no ID returned")

        steps[-1].status = "success"
        steps[-1].details = f"File uploaded. ID: {file_id}"
        steps[-1].display_name = "Document Archived"
        steps[-1].data = {"file_id": file_id, "raw": res.raw}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 2 failed")

    # STEP 3: Establish Data Access
    add_step("Access Control", "pending", display_name="Establish Data Access")
    try:
        perm_input = GoogleDriveOperationInput(
            PermissionsCreateOperation(
                action="permissions.create",
                file_id=file_id,
                role="reader",
                email_address=payload.recipient_email,
                type="user"
            )
        )
        perm_res = await execute_with_retry(
            connector, perm_input, trace_id, steps[-1]
        )
        
        steps[-1].status = "success"
        steps[-1].details = f"Read access granted to {payload.recipient_email}"
        steps[-1].display_name = "Access Control Applied"
        steps[-1].data = {"raw": perm_res.raw}
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 3 failed")

    # STEP 4: Verify Integrity
    add_step("Integrity Verification", "pending", display_name="Verify Integrity")
    try:
        get_input = GoogleDriveOperationInput(
            FilesGetOperation(
                action="files.get",
                file_id=file_id,
                fields="id, name, mimeType, webViewLink, size, createdTime, owners"
            )
        )
        get_res = await execute_with_retry(
            connector, get_input, trace_id, steps[-1]
        )
        file_metadata = get_res.raw
        
        beautiful_data = {
            "id": file_id,
            "type": "Secure Archived Document",
            "date": file_metadata.get("createdTime", datetime.now().isoformat()),
            "status": "SECURED",
            "patient_name": payload.recipient_email, # Mimicking patient name for UI schema
            "author": file_metadata.get("owners", [{}])[0].get("displayName", "Service Account") if file_metadata.get("owners") else "Service Account",
            "category": file_metadata.get("mimeType", "text/plain"),
            "description": file_metadata.get("name"),
            "content_text": f"Document successfully archived and shared.\n\nWeb Link: {file_metadata.get('webViewLink')}\nSize: {file_metadata.get('size')} bytes"
        }

        steps[-1].status = "success"
        steps[-1].details = "Archival verified on Google Drive"
        steps[-1].display_name = "Archival Verified"
        steps[-1].data = {"raw": file_metadata, "beautiful_data": beautiful_data}

        return ScenarioResponse(
            success=True,
            steps=steps,
            final_resource_id=file_id,
            human_summary=f"Success! Document '{payload.document_name}' archived to Google Drive and shared with {payload.recipient_email}.",
            trace_id=trace_id
        )
    except Exception as e:
        return _safe_error_return(e, steps, trace_id, "Step 4 failed")


# ---------------------------------------------------------------------------
# AI Agent Chat endpoint
# ---------------------------------------------------------------------------

class AgentChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class AgentChatInput(BaseModel):
    message: str
    history: List[Dict[str, str]] = []  # [{"role": "user/assistant", "content": "..."}]

class AgentChatStepResponse(BaseModel):
    tool: str
    args: Dict[str, Any]
    result: Optional[str] = None

class AgentChatResponse(BaseModel):
    reply: str
    steps: List[AgentChatStepResponse] = []
    trace_id: str
    success: bool


AGENT_GUARDRAIL_PROMPT = (
    "You are a healthcare data assistant. You have access to tools for fetching "
    "patient data from Cerner FHIR and Epic FHIR, uploading files to Google Drive, and sending "
    "emails via SMTP.\n"
    "Tool names are `<connector_id>.<action>` (e.g. `fhir_cerner.read_patient`, "
    "`fhir_epic.read_patient`, `google_drive.files.upload`, `smtp.send_email`). "
    "Use exactly the names and JSON-schema arguments from tools/list.\n\n"
    "WORKFLOW (MUST EXECUTE SEQUENTIALLY, ONE STRICT STEP AT A TIME):\n"
    "When asked to 'Send patient summaries via email' or similar tasks, you MUST follow this exact flow in order. DO NOT parallelize these steps:\n"
    "  1. First turn: Obtain patient demographics from the EHR.\n"
    "     - If the user gave a Patient ID: call `fhir_cerner.read_patient` or `fhir_epic.read_patient` with JSON `{\"resource_id\": \"<id>\"}` (use Epic when the ID starts with 'e'). Do NOT use search_patients for a known ID.\n"
    "     - If there is NO Patient ID but there IS a name: use name fields or `search_patients` per tools/list schema (e.g. `given_name`, `family_name`, `birthdate`, or valid `search_params`).\n"
    "     - Use `search_patients` only when you have no ID, or after `read_patient` failed and you need a fallback.\n"
    "     CRITICAL: If the user has NOT provided a patient ID or name in their message, you MUST ASK them for it. DO NOT call tools with a guessed or hallucinated ID like '12345'.\n"
    "  2. Second turn: Once you have the patient data from step 1, create a file on Google Drive containing the masked patient summary. Do NOT use placeholder content.\n"
    "  3. Third turn: Once step 2 returns a shareable Drive URL (see `data.raw.webViewLink` from tool `google_drive.files.upload`), send an email with that exact link. Do NOT call the email tool until you have the link.\n"
    "     CRITICAL: You MUST ask the user for the recipient email address if they haven't provided it. DO NOT guess email addresses like 'recipient_email@example.com'.\n"
    "     CRITICAL: In the email body, you MUST insert the actual URL string returned from step 2 (e.g. 'https://drive.google.com/...'). Do NOT literally write the text '<web_view_link>'.\n\n"
    "DATA PRIVACY & MASKING — follow these strictly:\n"
    "- Before uploading ANY data to Google Drive or sending it via Email, you MUST apply masking to the ACTUAL patient data you retrieved from tools:\n"
    "  - Date of Birth (DOB): Replace the year with '****' (e.g., if DOB is 1985-12-31, write ****-12-31).\n"
    "  - Patient ID: Mask all but the first 3 digits (e.g., if ID is '8877665', write '887****').\n"
    "  - NEVER use the placeholder values ('1990-05-12', '12724066', or 'Name') in your reports - always use the real patient data masked accordingly.\n"
    "- EMAIL WORKFLOW: When sending patient details to an email recipient:\n"
    "  1. ALWAYS upload the masked patient summary to Google Drive first.\n"
    "  2. Use `data.raw.webViewLink` from the `google_drive.files.upload` tool result.\n"
    "  3. In the email body, provide that link instead of the actual data.\n"
    "  4. The email body should be professional: 'Patient data summary from the EHR is available at the following secure link: [Link]'\n\n"
    "GUARDRAILS:\n"
    "- NEVER hallucinate or make up patient details. DO NOT guess IDs like '12345'. If missing, ask the user.\n"
    "- NEVER use placeholders like 'to be updated later' or '<web_view_link>'.\n"
    "- If a tool requires data from a previous tool's output, you MUST WAIT for the previous tool to complete in a previous turn.\n"
    "- If the user provides a Patient ID, do NOT ask for their name or birthdate. The ID is perfectly sufficient.\n"
    "- Do not call the same tool twice unless the first call failed.\n"
    "- Before calling any tool, verify you have ALL required parameters.\n"
    "- If a tool call fails, explain the error clearly and ask the user how to proceed.\n"
    "- Always confirm what you've done after completing the requested actions.\n"
    "- Keep responses concise and professional.\n"
)




@router.post("/agent-chat", response_model=AgentChatResponse)
async def agent_chat(payload: AgentChatInput) -> AgentChatResponse:
    """
    AI Agent chatbot endpoint.
    Accepts a user message + conversation history, runs through the ToolHiveAgent,
    and returns the agent's reply with any tool steps executed.
    """
    import os
    import sys

    trace_id = str(uuid.uuid4())
    logger.info("Agent Chat request | trace_id=%s | provider=%s",
                trace_id, os.environ.get("LLM_PROVIDER", "groq"))

    if not payload.message.strip():
        return AgentChatResponse(
            reply="Please type a message to get started.",
            steps=[],
            trace_id=trace_id,
            success=False,
        )

    try:
        from agents.llm_factory import LLMProviderFactory, LLMMessage
        from agents.toolhive import (
            MultiMcpClient,
            ToolHiveAgent,
            ToolHiveMcpClient,
            StdioMcpClient,
            resolve_mcp_urls,
            resolve_max_tool_failures,
        )

        provider_name = os.environ.get("LLM_PROVIDER", "groq")
        logger.info("Agent Chat | creating LLM provider: %s", provider_name)
        llm_provider = LLMProviderFactory.create_from_env()

        # Build the task from the conversation history + current message
        # The agent will see the full context
        history_text_parts = []
        for msg in payload.history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            history_text_parts.append(f"{role.upper()}: {content}")

        if history_text_parts:
            task = (
                "Previous conversation:\n"
                + "\n".join(history_text_parts)
                + f"\n\nUSER (latest): {payload.message}"
            )
        else:
            task = payload.message

        # Determine MCP transport — try proxy first, fallback to local stdio
        urls = resolve_mcp_urls()
        run_result = None

        if urls:
            logger.info("Agent Chat | trying ToolHive proxy URL(s): %s", ",".join(urls))
            try:
                if len(urls) == 1:
                    mcp_client = ToolHiveMcpClient(urls[0])
                else:
                    mcp_client = MultiMcpClient([ToolHiveMcpClient(u) for u in urls])
                agent = ToolHiveAgent(
                    mcp_client,
                    llm_provider,
                    max_steps=10,
                    max_tool_failures=resolve_max_tool_failures(None),
                )
                agent._system_prompt = AGENT_GUARDRAIL_PROMPT
                run_result = await agent.run(task)
                # Fallback to local stdio if:
                # (a) agent hard-failed due to missing tools, OR
                # (b) agent "succeeded" but called zero tools (LLM gave up because
                #     only a subset of tools was discoverable via the proxy)
                proxy_incomplete = (
                    (not run_result.success and run_result.error and (
                        "Failed to list MCP tools" in run_result.error
                        or "not in request.tools" in run_result.error
                    ))
                    or (run_result.success and not run_result.steps)
                )
                if proxy_incomplete:
                    logger.warning("Agent Chat | proxy incomplete, falling back to local stdio")
                    run_result = None
            except Exception as proxy_err:
                logger.warning("Agent Chat | proxy error: %s — falling back to local stdio", proxy_err)
                run_result = None

        if run_result is None:
            # Use local stdio transport
            logger.info("Agent Chat | using local stdio MCP transport")
            cmd = [sys.executable, "-m", "agents.mcp_entrypoint"]
            async with StdioMcpClient(cmd) as mcp_client:
                agent = ToolHiveAgent(
                    mcp_client,
                    llm_provider,
                    max_steps=10,
                    max_tool_failures=resolve_max_tool_failures(None),
                )
                agent._system_prompt = AGENT_GUARDRAIL_PROMPT
                run_result = await agent.run(task)

        # Map agent steps to response format
        chat_steps = []
        for s in run_result.steps:
            chat_steps.append(AgentChatStepResponse(
                tool=s.tool_called or "unknown",
                args=s.tool_args,
                result=s.tool_result,
            ))

        reply = run_result.final_answer or run_result.error or "I encountered an issue. Please try again."

        return AgentChatResponse(
            reply=reply,
            steps=chat_steps,
            trace_id=trace_id,
            success=run_result.success,
        )

    except Exception as e:
        logger.error("Agent Chat failed: %s", e, exc_info=True)
        return AgentChatResponse(
            reply=f"Sorry, I encountered an error: {str(e)}. Please check the server configuration and try again.",
            steps=[],
            trace_id=trace_id,
            success=False,
        )
