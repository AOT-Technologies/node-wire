#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Inputs and output for the Cerner FHIR connector (Oracle Health Millennium, R4).

Search fields are the search parameters in Cerner's R4 capability statement (FHIR 4.0.1), snake_case of the
parameter name (``_count`` is ``count``, ``_id`` is ``id``, ``-physicalType`` is ``physical_type``). The wire
name is set explicitly where it differs. Values are strings in FHIR search syntax: a comma means "or", dates
take a prefix (``ge2024-01-01``), tokens take ``system|code``. Reads take the resource id; writes also take
the FHIR resource as JSON. Updates, patches and deletes take ``if_match``, the resource version Cerner asks for.
"""

from __future__ import annotations

import base64
from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID = r"^[A-Za-z0-9\-.]{1,64}$"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ById(_Base):
    resource_id: str = Field(
        ...,
        pattern=_ID,
        description="FHIR id of the resource (letters, digits, '-' and '.', up to 64).",
    )


class _Versioned(_Base):
    if_match: str = Field(
        ...,
        min_length=1,
        description=(
            "Current version of the resource: the ETag of its last read, create or update, "
            'for example W/"3". A bare version number such as 3 is sent as W/"3".'
        ),
    )


class _Search(_Base):
    # Simplified: Cerner answers an unscoped search with 400, and a patient-wide search can return a lot of
    # PHI, so the guard is local. ``_anchor`` lists the parameters the capability statement says one of must
    # be given (``id`` included); a resource without a documented rule needs at least one parameter.
    _anchor: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _scoped(self) -> "_Search":
        given = self.model_fields_set - {"action", "count", "include"}
        if self._anchor and not given & set(self._anchor):
            raise ValueError("this search needs one of: " + ", ".join(self._anchor))
        if not given:
            raise ValueError("give at least one search parameter")
        return self


class _Create(_Base):
    _type: ClassVar[str] = ""

    resource: Dict[str, Any] = Field(
        ...,
        description="The FHIR resource to create, as JSON. 'resourceType' is filled in when left out.",
    )

    @field_validator("resource")
    @classmethod
    def _typed(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        found = value.setdefault("resourceType", cls._type)
        if found != cls._type:
            raise ValueError(f"resourceType must be {cls._type}, not {found}")
        return value


class _Update(_Create, _Versioned):
    resource_id: str = Field(
        ...,
        pattern=_ID,
        description=(
            "FHIR id of the resource to update. The body's 'id' is set to it. Fields left out of "
            "the body are removed from the record."
        ),
    )

    @model_validator(mode="after")
    def _same_id(self) -> "_Update":
        found = self.resource.setdefault("id", self.resource_id)
        if found != self.resource_id:
            raise ValueError("the id in resource must equal resource_id")
        return self


class _Patch(_ById, _Versioned):
    operations: List[Dict[str, Any]] = Field(
        ...,
        min_length=1,
        description=(
            "RFC 6902 JSON Patch operations, each {'op', 'path', 'value'}. Cerner supports only the "
            "paths listed on the resource's page in its API documentation."
        ),
    )

    @field_validator("operations")
    @classmethod
    def _valid(cls, value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for op in value:
            if op.get("op") not in (
                "add",
                "remove",
                "replace",
                "move",
                "copy",
                "test",
            ) or not isinstance(op.get("path"), str):
                raise ValueError(
                    "each operation needs an 'op' (add, remove, replace, ...) and a 'path'"
                )
        return value


class _Delete(_ById, _Versioned):
    pass


class _Parameters(_Base):
    parameters: Dict[str, Any] = Field(
        ...,
        description="The FHIR Parameters resource of the operation. 'resourceType' is filled in.",
    )

    @field_validator("parameters")
    @classmethod
    def _typed(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        found = value.setdefault("resourceType", "Parameters")
        if found != "Parameters":
            raise ValueError(f"resourceType must be Parameters, not {found}")
        return value


class FhirCernerOperationOutput(BaseModel):
    """Output for every action. Fields not used by an action stay None."""

    resource: Optional[Dict[str, Any]] = None
    """One resource, or a bulk-export manifest or an operation result."""
    resources: Optional[List[Dict[str, Any]]] = None
    """The resources of a search Bundle."""
    total: Optional[int] = None
    """Total the Bundle reports, when it reports one."""
    next_url: Optional[str] = None
    """The Bundle's 'next' link. Pass it to fetch_next_page."""
    resource_id: Optional[str] = None
    """The id of a created or updated resource."""
    etag: Optional[str] = None
    """The resource version after a read, create, update or patch. Pass it as if_match to the next write."""
    errors: Optional[List[Dict[str, Any]]] = None
    """Per-id failures of a multi-id lookup."""
    status_url: Optional[str] = None
    """Where to poll a started bulk export."""
    status_code: Optional[int] = None
    """HTTP status, for the bulk-export calls."""
    progress: Optional[str] = None
    """Cerner's X-Progress header while a bulk export runs."""
    content: Optional[str] = None
    """The text of a bulk-export file (NDJSON) or an XML CCD."""


# ---- Wave 0: the original five actions (names and shape unchanged) -----------


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


class FhirCernerPatientSearchInput(BaseModel):
    """Input for searching / fetching multiple FHIR Patient resources from Cerner."""

    action: Literal["search_patients"] = "search_patients"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    resource_ids: Optional[List[str]] = Field(None, max_length=50)
    """List of Cerner Patient IDs to fetch concurrently."""

    given_name: Optional[str] = None
    family_name: Optional[str] = None
    name: Optional[str] = None
    birthdate: Optional[str] = None
    search_params: Optional[Dict[str, str]] = None


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

    @field_validator("data", mode="after")
    @classmethod
    def validate_base64_data(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            base64.b64decode(v, validate=True)
        except Exception:
            raise ValueError("data must be a valid base64-encoded string")
        return v

    data: Optional[str] = Field(None, max_length=10 * 1024 * 1024)
    """Base64-encoded document content. Required for both binary files (PDFs) and plain text.
    Max size 10MB.
    
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


class FhirCernerDocumentReferenceSearchInput(BaseModel):
    """Input for searching FHIR DocumentReference resources in Cerner."""

    action: Literal["search_document_reference"] = "search_document_reference"
    """Action discriminator (one endpoint, multiple actions pattern)."""

    search_params: Dict[str, str]
    """Search parameters (e.g. {"patient": "12345678"})."""


# ---- Operations ---------------------------------------------------------------
class FetchNextPageInput(_Base):
    "Follow the 'next' link of a search result"

    action: Literal["fetch_next_page"] = "fetch_next_page"
    url: str = Field(
        ...,
        description="The next_url of a previous search. It must be on the configured Cerner server.",
    )


class GetCapabilityStatementInput(_Base):
    "Read the server's capability statement (metadata)"

    action: Literal["get_capability_statement"] = "get_capability_statement"


class GetSmartConfigurationInput(_Base):
    "Read the SMART well-known configuration (token endpoint and supported scopes)"

    action: Literal["get_smart_configuration"] = "get_smart_configuration"


class GetCcdInput(_Base):
    "Generate a Continuity of Care Document for a patient (Binary/$autogen-ccd-if)"

    action: Literal["get_ccd"] = "get_ccd"
    patient: str = Field(..., description="Patient id.")
    encounter: Optional[str] = Field(None, description="Encounter id to limit the document to.")
    start: Optional[str] = Field(
        None,
        description="Start of the date range, a dateTime with a time part (2016-09-24T12:00:00.000Z).",
    )
    end: Optional[str] = Field(
        None, description="End of the date range, a dateTime with a time part. Default: up to now."
    )
    xml: bool = Field(
        False, description="Return the XML document as text in 'content' instead of JSON."
    )


class DocrefInput(_Base):
    "List a patient's documents with the Argonaut $docref operation (DocumentReference/$docref)"

    action: Literal["docref"] = "docref"
    patient: str = Field(..., description="Patient id.")
    start: Optional[str] = Field(
        None, description="Start of the date range, a dateTime with a time part."
    )
    end: Optional[str] = Field(
        None, description="End of the date range, a dateTime with a time part."
    )


class GetCaregiverOrganizationsInput(_ById):
    "List the caregiver organizations of a care unit organization (Organization/$get-cg-for-mrcu)"

    action: Literal["get_caregiver_organizations"] = "get_caregiver_organizations"


class HealthCardsIssueInput(_ById, _Parameters):
    "Issue SMART health cards for a patient (Patient/{id}/$health-cards-issue)"

    action: Literal["health_cards_issue"] = "health_cards_issue"


class ChargeItemCreateInput(_Parameters):
    "Create a charge event, which can produce one or more charge items (ChargeItem/$create)"

    action: Literal["charge_item_create"] = "charge_item_create"


class ChargeItemCreditInput(_ById, _Parameters):
    "Create an offsetting credit for a charge item (ChargeItem/{id}/$credit)"

    action: Literal["charge_item_credit"] = "charge_item_credit"


class ChargeItemModifyInput(_Parameters):
    "Create a new charge item by modifying an existing one (ChargeItem/$modify)"

    action: Literal["charge_item_modify"] = "charge_item_modify"


class CreateFinancialTransactionInput(_Create):
    "Create a financial transaction (a Basic resource)"

    _type: ClassVar[str] = "Basic"

    action: Literal["create_financial_transaction"] = "create_financial_transaction"


class ExecuteBatchInput(_Base):
    "Run a FHIR batch or transaction Bundle (POST to the server root)"

    action: Literal["execute_batch"] = "execute_batch"
    bundle: Dict[str, Any] = Field(
        ...,
        description=(
            "A Bundle of type batch or transaction. Cerner supports only some operations in it, "
            "for example creating a Provenance."
        ),
    )

    @field_validator("bundle")
    @classmethod
    def _bundle(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        found = value.setdefault("resourceType", "Bundle")
        if found != "Bundle" or value.get("type") not in ("batch", "transaction"):
            raise ValueError("bundle must be a Bundle of type batch or transaction")
        return value


class _BulkOptions(_Base):
    type: Optional[str] = Field(
        None,
        description=(
            "Comma-separated resource types to export. Without it the token's scopes decide. "
            "Naming only the types you need keeps the files small."
        ),
    )
    since: Optional[str] = Field(
        None, description="Only resources updated after this instant (2021-03-18T19:19:42.000Z)."
    )
    include_associated_data: Optional[
        Literal["LatestProvenanceResources", "RelevantProvenanceResources"]
    ] = Field(None, description="Which Provenance resources to add. Needs Provenance in 'type'.")
    type_filter: Optional[str] = Field(
        None,
        description="Comma-separated search queries, each 'Resource?param=value'. Needs 'type'.",
    )
    lenient: bool = Field(
        False, description="Ignore parameters Cerner does not support instead of failing."
    )

    @model_validator(mode="after")
    def _filter_needs_type(self) -> "_BulkOptions":
        if self.type_filter and not self.type:
            raise ValueError("type_filter needs type")
        return self


class BulkExportGroupInput(_ById, _BulkOptions):
    "Start a bulk export of a patient group (Group/{id}/$export)"

    action: Literal["bulk_export_group"] = "bulk_export_group"


class BulkExportPatientsInput(_BulkOptions):
    "Start a bulk export of a list of patients (Patient/$export)"

    action: Literal["bulk_export_patients"] = "bulk_export_patients"
    patients: List[str] = Field(
        ...,
        min_length=1,
        max_length=20000,
        description="Patient ids, at most 20,000. Cerner cannot export all patients.",
    )

    @field_validator("patients")
    @classmethod
    def _ids(cls, value: List[str]) -> List[str]:
        for pid in value:
            if not isinstance(pid, str) or not pid or len(pid) > 64:
                raise ValueError("each patient must be an id of 1 to 64 characters")
        return value


class BulkExportStatusInput(_Base):
    "Check a bulk export (status_url from bulk_export_group or bulk_export_patients)"

    action: Literal["bulk_export_status"] = "bulk_export_status"
    status_url: str


class BulkExportFileInput(_Base):
    "Download one file of a finished bulk export (a url from the status result)"

    action: Literal["bulk_export_file"] = "bulk_export_file"
    file_url: str


class BulkExportDeleteInput(_Base):
    "Delete a bulk export on the server once the files are downloaded"

    action: Literal["bulk_export_delete"] = "bulk_export_delete"
    status_url: str


# ---- Wave 1: clinical ------------------------------------------------------


class ReadAllergyIntoleranceInput(_ById):
    "Read a AllergyIntolerance by id"

    action: Literal["read_allergy_intolerance"] = "read_allergy_intolerance"


class SearchAllergyIntoleranceInput(_Search):
    "Search AllergyIntolerance resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_allergy_intolerance"] = "search_allergy_intolerance"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of AllergyIntolerance ids. It is a required field if the patient field is not given.",
    )
    patient: Optional[str] = Field(
        None,
        description="Who the sensitivity is for. It is a required field if the _id field is not given.",
    )
    clinical_status: Optional[str] = Field(
        None,
        serialization_alias="clinical-status",
        description="Certainty of the allergy or intolerance.",
    )
    last_updated: Optional[str] = Field(
        None,
        serialization_alias="_lastUpdated",
        description="A date or date range used to search for AllergyIntolerance records which were last updated in that period. The '_lastUpdated' parameter may be provided once without a prefix or time component to imply a date range or once without a prefix and with a time component to search for AllergyIntolerance records at a specific time. Alternately it may be provided twice with 'le', 'lt', 'ge', or 'gt' prefixes to search for AllergyIntolerance records within a specific range. The date and prefix pairs must create a closed range.",
    )


class ReadCarePlanInput(_ById):
    "Read a CarePlan by id"

    action: Literal["read_care_plan"] = "read_care_plan"


class SearchCarePlanInput(_Search):
    "Search CarePlan resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_care_plan"] = "search_care_plan"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of CarePlan ids.",
    )
    date: Optional[str] = Field(
        None,
        description="A date range with which to find CarePlans. If used, the ‘date’ parameter must be provided twice. Once with a 'ge' prefix representing the earliest date, and once with a 'le’ prefix representing the latest date. The date prefix pair must create a closed range.",
    )
    patient: Optional[str] = Field(
        None,
        description="Who the careplan is for. It is a required field if the _id field is not given.",
    )
    category: Optional[str] = Field(
        None,
        description="The category of the careplan. Category assess-plan is supported as of now.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return. Not honored when '_id' is set.",
    )


class ReadCareTeamInput(_ById):
    "Read a CareTeam by id"

    action: Literal["read_care_team"] = "read_care_team"


class SearchCareTeamInput(_Search):
    "Search CareTeam resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient", "encounter")

    action: Literal["search_care_team"] = "search_care_team"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="CareTeam id supports only the single id. It is a required field if the patient or encounter fields are not given.",
    )
    category: Optional[str] = Field(
        None,
        description="The category of the careteam. Category Longitudinal (patient level) or Encounter is supported as of now.",
    )
    encounter: Optional[str] = Field(
        None, description="The Encounter level CareTeam are displayed."
    )
    patient: Optional[str] = Field(
        None,
        description="Who the careteam is for. It is a required field if the _id field is not given.",
    )
    status: Optional[str] = Field(None, description="The status of the CareTeam.")


class ReadConditionInput(_ById):
    "Read a Condition by id"

    action: Literal["read_condition"] = "read_condition"


class SearchConditionInput(_Search):
    "Search Condition resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient", "subject")

    action: Literal["search_condition"] = "search_condition"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Condition ids. It is a required field if the patient or subject fields are not given.",
    )
    patient: Optional[str] = Field(
        None,
        description="Who has the condition. It is a required field if the subject or _id fields are not given.",
    )
    encounter: Optional[str] = Field(
        None, description="The encounter of the patient. Only encounter-diagnosis are returned."
    )
    subject: Optional[str] = Field(
        None,
        description="Who has the condition. It is a required field if the patient or _id fields are not given.",
    )
    clinical_status: Optional[str] = Field(
        None,
        serialization_alias="clinical-status",
        description="The clinical status of the condition.",
    )
    category: Optional[str] = Field(
        None,
        description="The category of the condition. Categories problem-list-item, encounter-diagnosis and health-concern are supported as of now.",
    )
    last_updated: Optional[str] = Field(
        None,
        serialization_alias="_lastUpdated",
        description="A date or date range used to search for conditions which were last updated in that period. The '_lastUpdated' parameter may be provided once without a prefix or time component to imply a date range or once without a prefix and with a time component to search for Conditions at a specific time. Alternately it may be provided twice with 'le', 'lt', 'ge', or 'gt' prefixes to search for conditions report within a specific range. The date and prefix pairs must create a closed range.",
    )


class ReadDeviceInput(_ById):
    "Read a Device by id"

    action: Literal["read_device"] = "read_device"


class SearchDeviceInput(_Search):
    "Search Device resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_device"] = "search_device"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Device ids. It is a required field if the patient field is not given.",
    )
    patient: Optional[str] = Field(
        None,
        description="The patient to whom Device is affixed. It is a required field if the _id field is not given.",
    )


class ReadDiagnosticReportInput(_ById):
    "Read a DiagnosticReport by id"

    action: Literal["read_diagnostic_report"] = "read_diagnostic_report"


class SearchDiagnosticReportInput(_Search):
    "Search DiagnosticReport resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_diagnostic_report"] = "search_diagnostic_report"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of DiagnosticReport ids. It is a required field if the patient field is not given.",
    )
    category: Optional[str] = Field(
        None, description="Which diagnostic discipline/department created the report."
    )
    code: Optional[str] = Field(None, description="The code for the report.")
    encounter: Optional[str] = Field(None, description="The Encounter when the report was made.")
    patient: Optional[str] = Field(
        None, description="The subject of the report. It is a required field."
    )
    date: Optional[str] = Field(
        None,
        description="A date range with which to find Documents. A date parameter may be provided once with or without prefix and time component to imply a date range. Alternately it may be provided twice with 'le', 'lt', 'ge', or 'gt' prefixes and time component to search for procedures within a specific range. The date and prefix pairs must create a closed range. Must be set with patient or subject.",
    )
    last_updated: Optional[str] = Field(
        None,
        serialization_alias="_lastUpdated",
        description="A date or date range used to search for Documents of diagnostic report which were last updated in that period. The '_lastUpdated' parameter may be provided once without a prefix or time component to imply a date range or once without a prefix and with a time component to search for Documents of diagnostic report at a specific time. Alternately it may be provided twice with 'le', 'lt', 'ge', or 'gt' prefixes to search for Documents of diagnostic report within a specific range. The date and prefix pairs must create a closed range.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page.",
    )


class ReadDocumentReferenceInput(_ById):
    "Read a DocumentReference by id"

    action: Literal["read_document_reference"] = "read_document_reference"


class ReadEncounterInput(_ById):
    "Read a Encounter by id"

    action: Literal["read_encounter"] = "read_encounter"


class ReadGoalInput(_ById):
    "Read a Goal by id"

    action: Literal["read_goal"] = "read_goal"


class SearchGoalInput(_Search):
    "Search Goal resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_goal"] = "search_goal"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="Goal id supports only the single id. It is a required field if the patient field is not given.",
    )
    patient: Optional[str] = Field(
        None,
        description="Who has the goal is for. It is a required field if the _id field is not given.",
    )
    target_date: Optional[str] = Field(
        None,
        serialization_alias="target-date",
        description="A date or date range from which to find Goals.",
    )


class ReadImmunizationInput(_ById):
    "Read a Immunization by id"

    action: Literal["read_immunization"] = "read_immunization"


class SearchImmunizationInput(_Search):
    "Search Immunization resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_immunization"] = "search_immunization"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Immunization ids. It is a required field if the patient field is not given.",
    )
    patient: Optional[str] = Field(
        None,
        description="The patient for the vaccination record. It is a required field if the _id field is not given.",
    )
    last_updated: Optional[str] = Field(
        None, serialization_alias="_lastUpdated", description="Vaccination LastUpdated Date."
    )
    date: Optional[str] = Field(None, description="Vaccination (non)-Administration Date.")
    target_disease: Optional[str] = Field(
        None,
        serialization_alias="target-disease",
        description="A single or comma separated list of target diseases the dose is being administered against.",
    )


class ReadLocationInput(_ById):
    "Read a Location by id"

    action: Literal["read_location"] = "read_location"


class SearchLocationInput(_Search):
    "Search Location resources"

    action: Literal["search_location"] = "search_location"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Location ids. It is a required field if the -physicalType field is not given",
    )
    physical_type: Optional[str] = Field(
        None,
        serialization_alias="-physicalType",
        description="The Location's physical type. It is a required field if the _id field is not given",
    )
    identifier: Optional[str] = Field(None, description="A location's identifier.")
    address: Optional[str] = Field(None, description="A (part of the) address of the location")
    address_city: Optional[str] = Field(
        None,
        serialization_alias="address-city",
        description="A city specified in an address. It is a required field if the address-state or address-postalcode fields are not given",
    )
    address_state: Optional[str] = Field(
        None, serialization_alias="address-state", description="A state specified in an address"
    )
    address_postalcode: Optional[str] = Field(
        None,
        serialization_alias="address-postalcode",
        description="A postal code specified in an address",
    )
    name: Optional[str] = Field(None, description="A portion of the location's name or alias")
    organization: Optional[str] = Field(
        None, description="Searches for locations that are managed by the provided organization"
    )
    type: Optional[str] = Field(
        None,
        description="The Location's type. It is a required field if the _id field is not given",
    )


class ReadMedicationRequestInput(_ById):
    "Read a MedicationRequest by id"

    action: Literal["read_medication_request"] = "read_medication_request"


class SearchMedicationRequestInput(_Search):
    "Search MedicationRequest resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_medication_request"] = "search_medication_request"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of MedicationRequest ids.",
    )
    patient: Optional[str] = Field(
        None,
        description="The patient to return MedicationRequests for. It is a required field if the _id field is not given.",
    )
    intent: Optional[str] = Field(None, description="The intent of the MedicationRequest.")
    status: Optional[str] = Field(None, description="The status of the MedicationRequest.")
    last_updated: Optional[str] = Field(
        None,
        serialization_alias="_lastUpdated",
        description="When the resource version last changed.",
    )
    timing_bounds_period: Optional[str] = Field(
        None,
        serialization_alias="-timing-boundsPeriod",
        description="The date-time within the period the medication should be given to the patient. Must be prefixed by 'ge'.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page. Not honored when '_id' is set.",
    )


class ReadObservationInput(_ById):
    "Read a Observation by id"

    action: Literal["read_observation"] = "read_observation"


class SearchObservationInput(_Search):
    "Search Observation resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient", "subject")

    action: Literal["search_observation"] = "search_observation"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Observation ids. It is a required field if the patient field is not given.",
    )
    patient: Optional[str] = Field(
        None,
        description="The patient the observation is about. It is a required field if the subject field is not given.",
    )
    subject: Optional[str] = Field(
        None,
        description="The patient subject the observation is about. It is a required field if the patient field is not given.",
    )
    category: Optional[str] = Field(
        None,
        description="A single or comma separated list of classifications of the type of observation.",
    )
    code: Optional[str] = Field(
        None, description="A single or comma separated list of observation types."
    )
    date: Optional[str] = Field(
        None,
        description="A date or date range from which to find observations. The 'date' parameter may be provided once without a prefix or time component to imply a date range or once without a prefix and with a time component to search for observations at a specific time. Alternately it may be provided twice with 'le', 'lt', 'ge', or 'gt' prefixes to search for observations within a specific range. The date and prefix pairs must create a closed range.",
    )
    last_updated: Optional[str] = Field(
        None,
        serialization_alias="_lastUpdated",
        description="A date or date range used to search for observations which were last updated in that period. The '_lastUpdated' parameter may be provided once without a prefix or time component to imply a date range or once without a prefix and with a time component to search for observations at a specific time. Alternately it may be provided twice with 'le', 'lt', 'ge', or 'gt' prefixes to search for observations within a specific range. The date and prefix pairs must create a closed range.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page.",
    )


class ReadOrganizationInput(_ById):
    "Read a Organization by id"

    action: Literal["read_organization"] = "read_organization"


class SearchOrganizationInput(_Search):
    "Search Organization resources"

    action: Literal["search_organization"] = "search_organization"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Organization ids.",
    )
    identifier: Optional[str] = Field(None, description="The Organization's Identifier.")
    type: Optional[str] = Field(None, description="The Organization's type.")
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page.",
    )
    name: Optional[str] = Field(None, description="The Organization's name.")
    address: Optional[str] = Field(None, description="The Organization's address.")


class ReadPractitionerInput(_ById):
    "Read a Practitioner by id"

    action: Literal["read_practitioner"] = "read_practitioner"


class SearchPractitionerInput(_Search):
    "Search Practitioner resources"

    action: Literal["search_practitioner"] = "search_practitioner"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Practitioner ids. Either the '_id', 'identifier', 'name', 'active' or 'family' parameter, or a combination of 'given' and 'family' parameters must be provided.",
    )
    identifier: Optional[str] = Field(
        None,
        description="A practitioner's Identifier. Either the '_id', 'identifier', 'name', 'active' or 'family' parameter, or a combination of 'given' and 'family' parameters must be provided.",
    )
    given: Optional[str] = Field(
        None,
        description="The beginning of the given name of the practitioner. Either the '_id', 'identifier', 'name', 'active' or 'family' parameter, or a combination of 'given' and 'family' parameters must be provided.",
    )
    family: Optional[str] = Field(
        None,
        description="The beginning of the family name of the practitioner. Either the '_id', 'identifier', 'name', 'active' or 'family' parameter, or a combination of 'given' and 'family' parameters must be provided.",
    )
    name: Optional[str] = Field(
        None,
        description="The beginning of the first, last or middle name of the practitioner. Either the '_id', 'identifier', 'name', 'active' or 'family' parameter, or a combination of 'given' and 'family' parameters must be provided.",
    )
    active: Optional[str] = Field(
        None,
        description="Whether the practitioner record is active. Either the '_id', 'identifier', 'name', 'active' or 'family' parameter, or a combination of 'given' and 'family' parameters must be provided.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page. Not honored when '_id' is set. Either the '_id', 'identifier', 'name', 'active' or 'family' parameter, or a combination of 'given' and 'family' parameters must be provided.",
    )


class ReadProcedureInput(_ById):
    "Read a Procedure by id"

    action: Literal["read_procedure"] = "read_procedure"


class SearchProcedureInput(_Search):
    "Search Procedure resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient", "subject")

    action: Literal["search_procedure"] = "search_procedure"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Procedure ids. It is a required field if the patient or subject fields are not given",
    )
    patient: Optional[str] = Field(
        None,
        description="Search by subject - a patient. It is a required field if the _id or subject fields are not given",
    )
    subject: Optional[str] = Field(
        None,
        description="Search by subject. It is a required field if the _id or patient fields are not given",
    )
    date: Optional[str] = Field(
        None,
        description="A date parameter may be provided once without a prefix or time component to imply a date range or once without a prefix and with a time component. Alternately it may be provided twice with 'le', 'lt', 'ge', or 'gt' prefixes to search for procedures within a specific range. The date and prefix pairs must create a closed range. Must be set with patient or subject.",
    )
    last_updated: Optional[str] = Field(
        None,
        serialization_alias="_lastUpdated",
        description="A date or date range used to search for procedures which were last updated in that period. The '_lastUpdated' parameter may be provided once without a prefix or time component to imply a date range or once without a prefix and with a time component to search for Procedures at a specific time. Alternately it may be provided twice with 'le', 'lt', 'ge', or 'gt' prefixes to search for procedures report within a specific range. The date and prefix pairs must create a closed range.",
    )


class ReadProvenanceInput(_ById):
    "Read a Provenance by id"

    action: Literal["read_provenance"] = "read_provenance"


class SearchProvenanceInput(_Search):
    "Search Provenance resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "target")

    action: Literal["search_provenance"] = "search_provenance"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Provenance ids. It is a required field if the target field is not given",
    )
    target: Optional[str] = Field(
        None,
        description="The resource(s) the Provenance is associated with. It is a required field if the _id field is not given",
    )


class ReadServiceRequestInput(_ById):
    "Read a ServiceRequest by id"

    action: Literal["read_service_request"] = "read_service_request"


class SearchServiceRequestInput(_Search):
    "Search ServiceRequest resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient", "subject")

    action: Literal["search_service_request"] = "search_service_request"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of ServiceRequest ids.",
    )
    patient: Optional[str] = Field(
        None,
        description="The patient to return ServiceRequests for. It is a required field if the subject or _id fields are not given.",
    )
    subject: Optional[str] = Field(
        None,
        description="Individual or Entity the service is ordered for. It is a required field if the patient or _id fields are not given.",
    )
    code: Optional[str] = Field(None, description="What is being requested/ordered.")
    last_updated: Optional[str] = Field(
        None,
        serialization_alias="_lastUpdated",
        description="When the resource version last changed.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page. Not honored when '_id' is set.",
    )


class ReadSpecimenInput(_ById):
    "Read a Specimen by id"

    action: Literal["read_specimen"] = "read_specimen"


class SearchSpecimenInput(_Search):
    "Search Specimen resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id",)

    action: Literal["search_specimen"] = "search_specimen"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Specimen ids.",
    )


# ---- Wave 2: other resources -----------------------------------------------


class ReadAccountInput(_ById):
    "Read a Account by id"

    action: Literal["read_account"] = "read_account"


class SearchAccountInput(_Search):
    "Search Account resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient", "guarantor")

    action: Literal["search_account"] = "search_account"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Account ids. It is a required field if the -guarantor or patient fields are not given.",
    )
    patient: Optional[str] = Field(
        None,
        description="The entity that caused the expenses. It is a required field if the _id or -guarantor fields are not given. Must be set with identifier.",
    )
    encounter: Optional[str] = Field(
        None,
        serialization_alias="-encounter",
        description="The encounter associated with the resource.",
    )
    guarantor: Optional[str] = Field(
        None,
        serialization_alias="-guarantor",
        description="The entity that responsible for payment. It is a required field if the _id or patient fields are not given.",
    )
    type: Optional[str] = Field(
        None, description="The identifier for the account. Required in every search request."
    )
    identifier: Optional[str] = Field(
        None, description="Statement Account number. Must be set with patients"
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page. Not honored when '_id' is set.",
    )


class ReadAppointmentInput(_ById):
    "Read a Appointment by id"

    action: Literal["read_appointment"] = "read_appointment"


class SearchAppointmentInput(_Search):
    "Search Appointment resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient", "practitioner", "location")

    action: Literal["search_appointment"] = "search_appointment"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Appointment ids. Either the '_id' parameter or one of the 'patient', 'practitioner' or 'location' parameters must be set.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page. Not honored when '_id' is set.",
    )
    date: Optional[str] = Field(
        None,
        description="A date or date range from which to find appointments. This or the '-date-or-req-period' parameter must be set when using patient, practitioner or location. The 'date' parameter may be provided once without a prefix or time component to imply a date range or once without a prefix and with a time component to search for appointments at a specific time. Alternately it may be provided twice with 'le' and 'gt' prefixes to search for appointments within a specific range. The date prefix pair must create a closed range.",
    )
    date_or_req_period: Optional[str] = Field(
        None,
        serialization_alias="-date-or-req-period",
        description="A start date or requested period from which to find appointments. This or the 'date' parameter must be set when using patient, practitioner or location. The '-date-or-req-period' parameter may be provided once without a prefix or time component to imply a date range or once without a prefix and with a time component to search for appointments at a specific time. Alternately it may be provided twice with 'le' and 'gt' prefixes to search for appointments within a specific range. The date prefix pair must create a closed range.",
    )
    patient: Optional[str] = Field(
        None,
        description="One of the individuals of the appointment is this patient. Either the '_id' parameter or one of the 'patient', 'practitioner' or 'location' parameters must be set.",
    )
    practitioner: Optional[str] = Field(
        None,
        description="One of the individuals of the appointment is this practitioner. Either the '_id' parameter or one of the 'patient', 'practitioner' or 'location' parameters must be set.",
    )
    location: Optional[str] = Field(
        None,
        description="This location is listed in the participants of the appointment. Either the '_id' parameter or one of the 'patient', 'practitioner' or 'location' parameters must be set.",
    )
    status: Optional[str] = Field(None, description="The overall status of the appointment.")


class ReadChargeItemInput(_ById):
    "Read a ChargeItem by id"

    action: Literal["read_charge_item"] = "read_charge_item"


class SearchChargeItemInput(_Search):
    "Search ChargeItem resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "context", "account")

    action: Literal["search_charge_item"] = "search_charge_item"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="ChargeItem id supports only the single id. It is a required field if the account or context fields are not given.",
    )
    context: Optional[str] = Field(
        None,
        description="Encounter associated with the ChargeItem. If provided, the '-status' parameter must also be provided. It is a required field if the _id or account fields are not given.",
    )
    account: Optional[str] = Field(
        None,
        description="Account associated with the ChargeItem. If provided, the '-status' parameter must also be provided. It is a required field if the _id or context fields are not given.",
    )
    status: Optional[str] = Field(
        None,
        serialization_alias="-status",
        description="The status of the ChargeItem. Must be set to 'billable'. Must be provided with the context and account parameter.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page. Not honored when '_id' is set.",
    )


class ReadCommunicationInput(_ById):
    "Read a Communication by id"

    action: Literal["read_communication"] = "read_communication"


class SearchCommunicationInput(_Search):
    "Search Communication resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "category")

    action: Literal["search_communication"] = "search_communication"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Communication ids. It is a required field if the category, -email-status, received and recipient fields are not given.",
    )
    category: Optional[str] = Field(
        None,
        description="The message category. It is a required field if the _id field is not given. Must be set with received, recipient and -email-status.",
    )
    received: Optional[str] = Field(
        None,
        description="When the message is received. It is a required field if the _id field is not given. Must be set with category, recipient and -email-status. If used, the ‘received’ parameter may be provided once or twice. Once with a 'ge' prefix representing the earliest received date, and/or once with a 'le’ prefix representing the latest received date.",
    )
    recipient: Optional[str] = Field(
        None,
        description="The recipient of the message. It is a required field if the _id field is not given. Must be set with category, recipient and -email-status.",
    )
    email_status: Optional[str] = Field(
        None,
        serialization_alias="-email-status",
        description="The email status of the message. It is a required field if the _id field is not given. Must be set with category, received and recipient.",
    )


class ReadConsentInput(_ById):
    "Read a Consent by id"

    action: Literal["read_consent"] = "read_consent"


class SearchConsentInput(_Search):
    "Search Consent resources"

    _anchor: ClassVar[tuple[str, ...]] = (
        "id",
        "patient",
        "actor",
        "patient_identifier",
        "actor_identifier",
    )

    action: Literal["search_consent"] = "search_consent"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Consent ids.",
    )
    patient: Optional[str] = Field(
        None,
        description="Who the consent applies to. It is a required field if the _id field is not given.",
    )
    actor: Optional[str] = Field(
        None,
        description="Resource for the actor (or group, by role). It is a required field if the _id field is not given.",
    )
    patient_identifier: Optional[str] = Field(
        None,
        serialization_alias="patient.identifier",
        description="The patient.identifier allows to search by patient aliases. Federated Person Principle, SSN, MRN etc... are supported as of now. It is a required field if the _id field is not given.",
    )
    actor_identifier: Optional[str] = Field(
        None,
        serialization_alias="actor.identifier",
        description="The actor.identifier allows to search by related person aliases. Federated Person Principle is the only supported alias as of now. It is a required field if the _id field is not given.",
    )
    include: Optional[List[str]] = Field(
        None,
        serialization_alias="_include",
        description="Resources to include. Supported: Consent:actor, Consent:patient",
    )


class ReadCoverageInput(_ById):
    "Read a Coverage by id"

    action: Literal["read_coverage"] = "read_coverage"


class SearchCoverageInput(_Search):
    "Search Coverage resources"

    action: Literal["search_coverage"] = "search_coverage"
    id: Optional[str] = Field(None, serialization_alias="_id")
    patient: Optional[str] = Field(None, description="Retrieve coverages for a patient.")
    encounter: Optional[str] = Field(
        None, serialization_alias="-encounter", description="Retrieve coverages for an encounter."
    )


class ReadFamilyMemberHistoryInput(_ById):
    "Read a FamilyMemberHistory by id"

    action: Literal["read_family_member_history"] = "read_family_member_history"


class SearchFamilyMemberHistoryInput(_Search):
    "Search FamilyMemberHistory resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_family_member_history"] = "search_family_member_history"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of FamilyMemberHistory ids. It is a required field if the patient field is not given.",
    )
    patient: Optional[str] = Field(
        None,
        description="Who the family member history is for. It is a required field if the _id field is not given. It is a required field if the status parameter is provided.",
    )
    status: Optional[str] = Field(None, description="The status of the FamilyMemberHistory.")


class ReadInsurancePlanInput(_ById):
    "Read a InsurancePlan by id"

    action: Literal["read_insurance_plan"] = "read_insurance_plan"


class SearchInsurancePlanInput(_Search):
    "Search InsurancePlan resources"

    action: Literal["search_insurance_plan"] = "search_insurance_plan"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of InsurancePlan ids.",
    )
    owned_by: Optional[str] = Field(
        None,
        serialization_alias="owned-by",
        description="The organization that is providing the health insurance product.",
    )


class ReadMedicationAdministrationInput(_ById):
    "Read a MedicationAdministration by id"

    action: Literal["read_medication_administration"] = "read_medication_administration"


class SearchMedicationAdministrationInput(_Search):
    "Search MedicationAdministration resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_medication_administration"] = "search_medication_administration"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of MedicationAdministration ids.",
    )
    patient: Optional[str] = Field(
        None,
        description="The identity of a patient to list administrations for. It is a required field if the _id field is not given.",
    )
    status: Optional[str] = Field(None, description="MedicationAdministration event status.")
    performer: Optional[str] = Field(
        None, description="The identity of the individual who administered the medication."
    )
    effective_time: Optional[str] = Field(
        None,
        serialization_alias="effective-time",
        description="A effective-time parameter may be provided once with 'le' or 'lt' or 'ge' or 'gt' prefix and time component to imply a date range. Alternately it may be provided twice with 'le', 'lt', 'ge', or 'gt' prefixes and time component to search for medications administered within a specific range. The date and prefix pairs must create a closed range.",
    )


class ReadMedicationDispenseInput(_ById):
    "Read a MedicationDispense by id"

    action: Literal["read_medication_dispense"] = "read_medication_dispense"


class SearchMedicationDispenseInput(_Search):
    "Search MedicationDispense resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_medication_dispense"] = "search_medication_dispense"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of MedicationDispense ids.",
    )
    patient: Optional[str] = Field(
        None,
        description="The identity of a patient to list dispenses for. It is a required field if the _id field is not given.",
    )
    date: Optional[str] = Field(
        None,
        description="A date-time used to find medication dispenses. Only a single value is supported. Only 'ge' and 'gt' prefixes are supported. Ranges (multiple values) are not supported. Cannot be used together with '_lastUpdated'. If both are provided, 'date' will be ignored.",
    )
    last_updated: Optional[str] = Field(
        None,
        serialization_alias="_lastUpdated",
        description="A date-time used to search for medication dispenses which were last updated in that period. Only a single value is supported and it must include a time component. Only 'ge' and 'gt' prefixes are supported. Ranges (multiple values) are not supported. Cannot be used together with 'date'. If both are provided, 'date' will be ignored.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return. Not honored when '_id' is set.",
    )


class ReadNutritionOrderInput(_ById):
    "Read a NutritionOrder by id"

    action: Literal["read_nutrition_order"] = "read_nutrition_order"


class SearchNutritionOrderInput(_Search):
    "Search NutritionOrder resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_nutrition_order"] = "search_nutrition_order"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of NutritionOrder ids.",
    )
    patient: Optional[str] = Field(
        None,
        description="The patient to return NutritionOrders for. It is a required field if the _id field is not given.",
    )
    status: Optional[str] = Field(None, description="The status of the NutritionOrder.")
    last_updated: Optional[str] = Field(
        None,
        serialization_alias="_lastUpdated",
        description="When the resource version last changed.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page. Not honored when '_id' is set.",
    )


class ReadPersonInput(_ById):
    "Read a Person by id"

    action: Literal["read_person"] = "read_person"


class SearchPersonInput(_Search):
    "Search Person resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "identifier")

    action: Literal["search_person"] = "search_person"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Person ids. It is a required field if the identifier field is not given",
    )
    identifier: Optional[str] = Field(
        None,
        description="A person's Identifier. It is a required field if the _id field is not given",
    )


class ReadQuestionnaireInput(_ById):
    "Read a Questionnaire by id"

    action: Literal["read_questionnaire"] = "read_questionnaire"


class SearchQuestionnaireInput(_Search):
    "Search Questionnaire resources"

    action: Literal["search_questionnaire"] = "search_questionnaire"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Questionnaire ids.",
    )


class ReadQuestionnaireResponseInput(_ById):
    "Read a QuestionnaireResponse by id"

    action: Literal["read_questionnaire_response"] = "read_questionnaire_response"


class SearchQuestionnaireResponseInput(_Search):
    "Search QuestionnaireResponse resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "patient")

    action: Literal["search_questionnaire_response"] = "search_questionnaire_response"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of QuestionnaireResponse ids. It is a required field if the patient field is not given.",
    )
    patient: Optional[str] = Field(
        None,
        description="The patient that is the subject of the questionnaire response. It is a required field if the _id field is not given",
    )


class ReadRelatedPersonInput(_ById):
    "Read a RelatedPerson by id"

    action: Literal["read_related_person"] = "read_related_person"


class SearchRelatedPersonInput(_Search):
    "Search RelatedPerson resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "identifier", "patient", "encounter")

    action: Literal["search_related_person"] = "search_related_person"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of RelatedPerson ids. It is a required field if the identifier or patient or -encounter fields are not given.",
    )
    identifier: Optional[str] = Field(
        None,
        description="An Identifier of the RelatedPerson. It is a required field if the id or patient or -encounter fields are not given",
    )
    patient: Optional[str] = Field(
        None,
        description="The patient this related person is related to. It is a required field if the id or identifier or -encounter fields are not given",
    )
    encounter: Optional[str] = Field(
        None,
        serialization_alias="-encounter",
        description="The encounter this related person is related to. It is a required field if the id or identifier or patient fields are not given",
    )
    relationship_level: Optional[str] = Field(
        None,
        serialization_alias="-relationship-level",
        description="The resource's relationship to either the patient or encounter level. Must be provided with Patient and Identifier searches.",
    )


class ReadScheduleInput(_ById):
    "Read a Schedule by id"

    action: Literal["read_schedule"] = "read_schedule"


class SearchScheduleInput(_Search):
    "Search Schedule resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id",)

    action: Literal["search_schedule"] = "search_schedule"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Schedule ids. The '_id' parameter must be set.",
    )


class ReadSlotInput(_ById):
    "Read a Slot by id"

    action: Literal["read_slot"] = "read_slot"


class SearchSlotInput(_Search):
    "Search Slot resources"

    _anchor: ClassVar[tuple[str, ...]] = ("id", "service_type")

    action: Literal["search_slot"] = "search_slot"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="A single or comma separated list of Slot ids. Either the '_id' parameter or 'service-type' must be set.",
    )
    start: Optional[str] = Field(
        None,
        description="A date or date range from which the slots are to be retrieved. The 'start' parameter is required when service-type, schedule.actor, -location is provided. When provided, must use both 'lt' and 'ge' prefixes to search for slots within a specific range. Time component should be provided for both the instances. The date and prefix pairs must create a closed range.",
    )
    service_type: Optional[str] = Field(
        None,
        serialization_alias="service-type",
        description="A single or comma separated list of appointment types that can be booked into the slot. Either the '_id' parameter or 'service-type' must be set.",
    )
    schedule_actor: Optional[str] = Field(
        None,
        serialization_alias="schedule.actor",
        description="A single or comma separated list of schedule actors. Must be Practitioner actors.",
    )
    location: Optional[str] = Field(
        None,
        serialization_alias="-location",
        description="A single or comma separated list of Location references.",
    )
    count: Optional[int] = Field(
        None,
        ge=1,
        serialization_alias="_count",
        description="The maximum number of results to return in a page. This value should be in the range [5,200]. If no value is provided, a default page size of 50 will be used.",
    )
    include: Optional[List[str]] = Field(
        None,
        serialization_alias="_include",
        description="Resources to include. Supported: Slot:schedule, Schedule:actor:Practitioner",
    )


class ReadBinaryInput(_ById):
    "Read a Binary by id"

    action: Literal["read_binary"] = "read_binary"


class ReadMediaInput(_ById):
    "Read a Media by id"

    action: Literal["read_media"] = "read_media"


class ReadOperationDefinitionInput(_ById):
    "Read a OperationDefinition by id"

    action: Literal["read_operation_definition"] = "read_operation_definition"


class ReadStructureDefinitionInput(_ById):
    "Read a StructureDefinition by id"

    action: Literal["read_structure_definition"] = "read_structure_definition"


# ---- Wave 3: writes --------------------------------------------------------


class CreateAllergyIntoleranceInput(_Create):
    "Create a AllergyIntolerance"

    _type: ClassVar[str] = "AllergyIntolerance"

    action: Literal["create_allergy_intolerance"] = "create_allergy_intolerance"


class CreateAppointmentInput(_Create):
    "Create a Appointment"

    _type: ClassVar[str] = "Appointment"

    action: Literal["create_appointment"] = "create_appointment"


class CreateCommunicationInput(_Create):
    "Create a Communication"

    _type: ClassVar[str] = "Communication"

    action: Literal["create_communication"] = "create_communication"


class CreateConditionInput(_Create):
    "Create a Condition"

    _type: ClassVar[str] = "Condition"

    action: Literal["create_condition"] = "create_condition"


class CreateCoverageInput(_Create):
    "Create a Coverage"

    _type: ClassVar[str] = "Coverage"

    action: Literal["create_coverage"] = "create_coverage"


class CreateDiagnosticReportInput(_Create):
    "Create a DiagnosticReport"

    _type: ClassVar[str] = "DiagnosticReport"

    action: Literal["create_diagnostic_report"] = "create_diagnostic_report"


class CreateEncounterInput(_Create):
    "Create a Encounter"

    _type: ClassVar[str] = "Encounter"

    action: Literal["create_encounter"] = "create_encounter"


class CreateFamilyMemberHistoryInput(_Create):
    "Create a FamilyMemberHistory"

    _type: ClassVar[str] = "FamilyMemberHistory"

    action: Literal["create_family_member_history"] = "create_family_member_history"


class CreateImmunizationInput(_Create):
    "Create a Immunization"

    _type: ClassVar[str] = "Immunization"

    action: Literal["create_immunization"] = "create_immunization"


class CreateMedicationRequestInput(_Create):
    "Create a MedicationRequest"

    _type: ClassVar[str] = "MedicationRequest"

    action: Literal["create_medication_request"] = "create_medication_request"


class CreateObservationInput(_Create):
    "Create a Observation"

    _type: ClassVar[str] = "Observation"

    action: Literal["create_observation"] = "create_observation"


class CreateOrganizationInput(_Create):
    "Create a Organization"

    _type: ClassVar[str] = "Organization"

    action: Literal["create_organization"] = "create_organization"


class CreatePatientInput(_Create):
    "Create a Patient"

    _type: ClassVar[str] = "Patient"

    action: Literal["create_patient"] = "create_patient"


class CreatePractitionerInput(_Create):
    "Create a Practitioner"

    _type: ClassVar[str] = "Practitioner"

    action: Literal["create_practitioner"] = "create_practitioner"


class CreateProcedureInput(_Create):
    "Create a Procedure"

    _type: ClassVar[str] = "Procedure"

    action: Literal["create_procedure"] = "create_procedure"


class CreateProvenanceInput(_Create):
    "Create a Provenance"

    _type: ClassVar[str] = "Provenance"

    action: Literal["create_provenance"] = "create_provenance"


class CreateRelatedPersonInput(_Create):
    "Create a RelatedPerson"

    _type: ClassVar[str] = "RelatedPerson"

    action: Literal["create_related_person"] = "create_related_person"


class UpdateAllergyIntoleranceInput(_Update):
    "Update a AllergyIntolerance (PUT)"

    _type: ClassVar[str] = "AllergyIntolerance"

    action: Literal["update_allergy_intolerance"] = "update_allergy_intolerance"


class UpdateConditionInput(_Update):
    "Update a Condition (PUT)"

    _type: ClassVar[str] = "Condition"

    action: Literal["update_condition"] = "update_condition"


class UpdateDocumentReferenceInput(_Update):
    "Update a DocumentReference (PUT)"

    _type: ClassVar[str] = "DocumentReference"

    action: Literal["update_document_reference"] = "update_document_reference"


class UpdateFamilyMemberHistoryInput(_Update):
    "Update a FamilyMemberHistory (PUT)"

    _type: ClassVar[str] = "FamilyMemberHistory"

    action: Literal["update_family_member_history"] = "update_family_member_history"


class UpdateImmunizationInput(_Update):
    "Update a Immunization (PUT)"

    _type: ClassVar[str] = "Immunization"

    action: Literal["update_immunization"] = "update_immunization"


class UpdateObservationInput(_Update):
    "Update a Observation (PUT)"

    _type: ClassVar[str] = "Observation"

    action: Literal["update_observation"] = "update_observation"


class UpdateQuestionnaireResponseInput(_Update):
    "Update a QuestionnaireResponse (PUT)"

    _type: ClassVar[str] = "QuestionnaireResponse"

    action: Literal["update_questionnaire_response"] = "update_questionnaire_response"


class PatchAppointmentInput(_Patch):
    "Patch a Appointment with JSON Patch"

    action: Literal["patch_appointment"] = "patch_appointment"


class PatchCommunicationInput(_Patch):
    "Patch a Communication with JSON Patch"

    action: Literal["patch_communication"] = "patch_communication"


class PatchCoverageInput(_Patch):
    "Patch a Coverage with JSON Patch"

    action: Literal["patch_coverage"] = "patch_coverage"


class PatchEncounterInput(_Patch):
    "Patch a Encounter with JSON Patch"

    action: Literal["patch_encounter"] = "patch_encounter"


class PatchMedicationRequestInput(_Patch):
    "Patch a MedicationRequest with JSON Patch"

    action: Literal["patch_medication_request"] = "patch_medication_request"


class PatchPatientInput(_Patch):
    "Patch a Patient with JSON Patch"

    action: Literal["patch_patient"] = "patch_patient"


class PatchRelatedPersonInput(_Patch):
    "Patch a RelatedPerson with JSON Patch"

    action: Literal["patch_related_person"] = "patch_related_person"


class PatchSlotInput(_Patch):
    "Patch a Slot with JSON Patch"

    action: Literal["patch_slot"] = "patch_slot"


class DeleteCoverageInput(_Delete):
    "Delete a Coverage"

    action: Literal["delete_coverage"] = "delete_coverage"
