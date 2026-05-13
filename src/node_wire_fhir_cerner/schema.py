from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Patient – Read
# ---------------------------------------------------------------------------


class FhirCernerPatientReadInput(BaseModel):
    """Input for reading a FHIR Patient resource from Cerner."""

    action: Literal["read_patient"] = "read_patient"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    resource_id: Optional[str] = None
    """Direct Patient ID lookup (e.g. '12345678')."""

    # Convenience name fields — take priority over raw search_params when set.
    given_name: Optional[str] = None
    """Patient given / first name (used in name-based search)."""

    family_name: Optional[str] = None
    """Patient family / last name (used in name-based search)."""

    name: Optional[str] = None
    """Full or partial name string — mapped to FHIR 'name' search parameter."""

    birthdate: Optional[str] = None
    """Date of birth in YYYY-MM-DD format — used alongside name search."""

    search_params: Optional[Dict[str, str]] = None
    """Raw FHIR search parameters (e.g. {"family": "Smith", "given": "John"})."""


class FhirCernerPatientReadOutput(BaseModel):
    """Output for reading a FHIR Patient resource from Cerner."""

    resource: Dict[str, Any]
    """The raw FHIR Patient JSON object."""


# ---------------------------------------------------------------------------
# Patient – Search (multi-ID fan-out OR name search returning multiple results)
# ---------------------------------------------------------------------------


class FhirCernerPatientSearchInput(BaseModel):
    """Input for searching / fetching multiple FHIR Patient resources from Cerner."""

    action: Literal["search_patients"] = "search_patients"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    resource_ids: Optional[List[str]] = None
    """List of Cerner Patient IDs to fetch concurrently."""

    given_name: Optional[str] = None
    family_name: Optional[str] = None
    name: Optional[str] = None
    birthdate: Optional[str] = None
    search_params: Optional[Dict[str, str]] = None


class FhirCernerPatientSearchOutput(BaseModel):
    """Output for searching multiple FHIR Patient resources from Cerner."""

    resources: List[Dict[str, Any]]
    total: Optional[int] = None
    errors: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Encounter – Search
# ---------------------------------------------------------------------------


class FhirCernerEncounterSearchInput(BaseModel):
    """Input for searching FHIR Encounter resources in Cerner."""

    action: Literal["search_encounter"] = "search_encounter"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    patient_id: Optional[str] = None
    """Cerner Patient ID to find encounters for (maps to 'patient' FHIR param)."""

    status: Optional[str] = None
    """Status of the encounters to find (e.g. 'finished', 'arrived')."""

    date: Optional[str] = None
    """Date or date range for the encounters (e.g. '2024', 'gt2023-01-01')."""

    search_params: Optional[Dict[str, str]] = None
    """Raw FHIR search parameters. Used if explicit fields above are not provided."""


class FhirCernerEncounterSearchOutput(BaseModel):
    """Output for searching FHIR Encounter resources in Cerner."""

    resources: list[Dict[str, Any]]
    """The list of raw FHIR Encounter JSON objects found."""

    total: Optional[int] = None
    """Total number of results reported by the Bundle."""


# ---------------------------------------------------------------------------
# DocumentReference – Create
# ---------------------------------------------------------------------------


class FhirCernerDocumentReferenceCreateInput(BaseModel):
    """Input for creating a FHIR DocumentReference resource in Cerner."""

    action: Literal["create_document_reference"] = "create_document_reference"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    identifier: Optional[list[Dict[str, Any]]] = None
    """Document identifier.

    Note: Cerner does NOT support the ``identifier`` field on DocumentReference create.
    This field is defined here for schema completeness but is intentionally excluded
    from the payload sent to Cerner. If you need to include it, pass it via
    ``additional_fields`` explicitly.
    """

    status: str
    """The document status (usually 'current')."""

    doc_status: Optional[str] = None
    """The status of the underlying document (e.g. 'final', 'amended').

    Cerner REQUIRES this field on create. Defaults to 'final' if not supplied.
    Supported values for system access: 'final' and 'amended'.
    """

    type: Dict[str, Any]
    """Document type (CodeableConcept).

    Cerner REQUIRES the proprietary CodeSet 72 system, NOT a raw LOINC code.
    The coding must include 'display', 'userSelected: true', and the type object
    must include a top-level 'text' field.

    Example:
        {
            "coding": [{
                "system": "https://fhir.cerner.com/{tenant_id}/codeSet/72",
                "code": "<valid_codeset72_code>",
                "display": "Consult note",
                "userSelected": True
            }],
            "text": "Consult note"
        }
    """

    category: Optional[list[Dict[str, Any]]] = None
    """Category (CodeableConcept).

    Cerner often requires this for clinical note document types (e.g. LOINC 11488-4).
    If omitted, the connector automatically defaults to the standard clinical-note class::

        [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/document-classcodes",
                      "code": "clinical-note", "display": "Clinical Note"}]}]

    Supply an explicit value to override the default.
    """

    subject: str
    """Patient reference string (e.g. 'Patient/{id}'). Required by Cerner."""

    attachment_title: Optional[str] = None
    """Title of the document attachment.

    Cerner REQUIRES this field on create (e.g. 'Consult Note').
    """

    attachment_creation: Optional[str] = None
    """Creation datetime of the attachment in ISO 8601 format with time component.

    Cerner REQUIRES this on create (e.g. '2024-01-01T00:00:00.000Z').
    All provided dates must include a time component.
    """

    data: Optional[str] = None
    """Base64-encoded document content. Required for both binary files (PDFs) and plain text.

    Note: If you provide raw text in the ``text`` field, the connector will automatically
    encode it to base64 for you.
    """

    text: Optional[str] = None
    """Raw string content for the document attachment.

    The connector will automatically base64-encode this string and send it via
    ``attachment.data``, as the Cerner sandbox does not support ``attachment.text``.
    """

    content_type: Optional[str] = None
    """MIME type of the document content (e.g. 'text/plain', 'application/pdf'). Defaults to 'text/plain'.

    Cerner REQUIRES a charset (e.g. '; charset=UTF-8') for all 'text/*' types.
    The connector automatically appends this if it is missing.
    """

    author: Optional[list[Dict[str, Any]]] = None
    """Author of the document (e.g. Practitioner reference)."""

    authenticator: Optional[Dict[str, Any]] = None
    """Authenticator of the document (e.g. Practitioner reference).

    Cerner includes this in sandbox examples alongside 'author'.
    Example: {"reference": "Practitioner/{id}"}
    """

    description: Optional[str] = None
    """Human-readable description of the document."""

    custodian: Optional[Dict[str, Any]] = None
    """Custodian of the document (e.g. Organization reference).

    Example: {"reference": "Organization/{id}"}
    """

    context: Optional[Dict[str, Any]] = None
    """Context details for the document.

    Cerner requires ``context.encounter`` for clinical note document types,
    AND ``context.period`` (with both ``start`` and ``end``) whenever
    ``context.encounter`` is set — omitting it causes a 422 validation error.

    Example::

        {
            "encounter": [{"reference": "Encounter/<encounter_id>"}],
            "period": {
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-01-01T01:00:00Z"
            }
        }
    """

    additional_fields: Optional[Dict[str, Any]] = None
    """Additional FHIR DocumentReference resource fields to merge into the payload."""


class FhirCernerDocumentReferenceCreateOutput(BaseModel):
    """Output for creating a FHIR DocumentReference resource in Cerner."""

    resource_id: str
    """The new DocumentReference resource ID."""

    resource: Optional[Dict[str, Any]] = None
    """The full created resource (only present when Prefer: return=representation)."""


# ---------------------------------------------------------------------------
# DocumentReference – Search
# ---------------------------------------------------------------------------


class FhirCernerDocumentReferenceSearchInput(BaseModel):
    """Input for searching FHIR DocumentReference resources in Cerner."""

    action: Literal["search_document_reference"] = "search_document_reference"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    search_params: Dict[str, str]
    """Search parameters (e.g. {"patient": "12345678"})."""


class FhirCernerDocumentReferenceSearchOutput(BaseModel):
    """Output for searching FHIR DocumentReference resources in Cerner."""

    resources: list[Dict[str, Any]]
    """The list of raw FHIR DocumentReference JSON objects found."""

    total: Optional[int] = None
    """Total number of results reported by the Bundle."""


class FhirCernerOperationOutput(BaseModel):
    """
    Unified output for all Cerner FHIR actions (BaseConnector single output_model).

    Fields are populated depending on the action; unused fields are None.
    """

    resource: Optional[Dict[str, Any]] = None
    resources: Optional[list[Dict[str, Any]]] = None
    total: Optional[int] = None
    resource_id: Optional[str] = None
    errors: Optional[list[Dict[str, Any]]] = None
