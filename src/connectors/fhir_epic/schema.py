from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Patient – Read
# ---------------------------------------------------------------------------

class FhirPatientReadInput(BaseModel):
    """Input for reading a FHIR Patient resource."""

    action: Literal["read_patient"] = "read_patient"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    resource_id: Optional[str] = None
    """Direct Patient ID lookup (e.g. 'eXYZ123')."""

    given_name: Optional[str] = None
    family_name: Optional[str] = None
    name: Optional[str] = None
    birthdate: Optional[str] = None

    search_params: Optional[Dict[str, str]] = None
    """Search parameters (e.g. {"family": "Smith", "given": "John"})."""


class FhirPatientReadOutput(BaseModel):
    """Output for reading a FHIR Patient resource."""

    resource: Dict[str, Any]
    """The raw FHIR Patient JSON object."""


# ---------------------------------------------------------------------------
# Patient – Search (multi-ID fan-out OR name search returning multiple results)
# ---------------------------------------------------------------------------

class FhirPatientSearchInput(BaseModel):
    """Input for searching / fetching multiple FHIR Patient resources from Epic."""

    action: Literal["search_patients"] = "search_patients"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    resource_ids: Optional[List[str]] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    name: Optional[str] = None
    birthdate: Optional[str] = None
    search_params: Optional[Dict[str, str]] = None


class FhirPatientSearchOutput(BaseModel):
    """Output for searching multiple FHIR Patient resources."""

    resources: List[Dict[str, Any]]
    total: Optional[int] = None
    errors: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Encounter – Search
# ---------------------------------------------------------------------------

class FhirEncounterSearchInput(BaseModel):
    """Input for searching FHIR Encounter resources."""

    action: Literal["search_encounter"] = "search_encounter"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    patient_id: Optional[str] = None
    status: Optional[str] = None
    date: Optional[str] = None
    search_params: Optional[Dict[str, str]] = None


class FhirEncounterSearchOutput(BaseModel):
    """Output for searching FHIR Encounter resources."""

    resources: list[Dict[str, Any]]
    """The list of raw FHIR Encounter JSON objects found."""

    total: Optional[int] = None
    """Total number of results reported by the Bundle."""


# ---------------------------------------------------------------------------
# DocumentReference – Create
# ---------------------------------------------------------------------------

class FhirDocumentReferenceCreateInput(BaseModel):
    """Input for creating a FHIR DocumentReference resource."""

    action: Literal["create_document_reference"] = "create_document_reference"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    identifier: list[Dict[str, Any]]
    """Document identifier."""

    status: str
    """The document status (usually 'current')."""

    type: Dict[str, Any]
    """Document type (CodeableConcept)."""

    category: Optional[list[Dict[str, Any]]] = None
    """Category (CodeableConcept). Epic does not require this field."""

    subject: str
    """Patient reference string (e.g. 'Patient/{id}'). Required by Epic."""

    data: str
    """Base64-encoded document content. Required by Epic."""

    content_type: Optional[str] = None
    """MIME type of the document content (e.g. 'text/plain', 'application/pdf'). Defaults to 'text/plain'."""

    author: Optional[list[Dict[str, Any]]] = None
    """Author of the document (e.g. Practitioner reference). Required by Epic sandbox."""

    description: Optional[str] = None
    """Human-readable description of the document."""

    context: Optional[Dict[str, Any]] = None
    """Context details for the document.

    Epic requires ``context.encounter`` for clinical note document types
    (e.g. LOINC 34108-1 Outpatient Note, 34117-2 History & Physical).
    Without it Epic returns::

        "diagnostics": "Valid encounter required",
        "expression": ["context/encounter"]

    Non-clinical document types (e.g. 34133-9 Summary of Episode) do NOT
    require an encounter.

    Example for clinical notes::

        {
            "encounter": [{"reference": "Encounter/<encounter_id>"}],
            "period": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T01:00:00Z"}
        }
    """

    additional_fields: Optional[Dict[str, Any]] = None
    """Additional FHIR DocumentReference resource fields to merge into the payload."""


class FhirDocumentReferenceCreateOutput(BaseModel):
    """Output for creating a FHIR DocumentReference resource."""

    resource_id: str
    """The new DocumentReference resource ID."""

    resource: Optional[Dict[str, Any]] = None
    """The full created resource (only present when Prefer: return=representation)."""


# ---------------------------------------------------------------------------
# DocumentReference – Search
# ---------------------------------------------------------------------------

class FhirDocumentReferenceSearchInput(BaseModel):
    """Input for searching FHIR DocumentReference resources."""

    action: Literal["search_document_reference"] = "search_document_reference"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    search_params: Dict[str, str]
    """Search parameters (e.g. {"patient": "eXYZ123"})."""


class FhirDocumentReferenceSearchOutput(BaseModel):
    """Output for searching FHIR DocumentReference resources."""

    resources: list[Dict[str, Any]]
    """The list of raw FHIR DocumentReference JSON objects found."""

    total: Optional[int] = None
    """Total number of results reported by the Bundle."""


class FhirEpicOperationOutput(BaseModel):
    """
    Unified output for all Epic FHIR actions (SDKConnector single output_model).

    Fields are populated depending on the action; unused fields are None.
    """

    resource: Optional[Dict[str, Any]] = None
    resources: Optional[list[Dict[str, Any]]] = None
    total: Optional[int] = None
    resource_id: Optional[str] = None
    errors: Optional[list[Dict[str, Any]]] = None
