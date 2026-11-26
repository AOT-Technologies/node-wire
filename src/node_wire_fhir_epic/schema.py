#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Inputs and output for the Epic FHIR connector.

Search fields are the search parameters in Epic's R4 capability statement (Epic November 2026, FHIR 4.0.1),
snake_case of the parameter name (``_count`` is ``count``, ``_id`` is ``id``, ``-`` and ``.`` become ``_``).
The wire name is set explicitly where it differs. Values are strings in FHIR search syntax: a comma means
"or", dates take a prefix (``ge2024-01-01``), tokens take ``system|code``. Reads, creates and updates take
the resource id and, for writes, the FHIR resource as JSON.
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


class _Search(_Base):
    # Simplified: Epic answers an unscoped search with 400, and a patient-wide search can return a lot of
    # PHI, so the guard is local: a patient-scoped resource needs a patient, a subject or an id.
    _scope: ClassVar[tuple[str, ...]] = ()
    _one_of: ClassVar[tuple[str, ...]] = ()

    count: Optional[int] = Field(
        None, ge=1, serialization_alias="_count", description="Number of results per page."
    )
    include: Optional[List[str]] = Field(
        None,
        serialization_alias="_include",
        description="Resources to include, e.g. 'Observation:encounter'.",
    )
    revinclude: Optional[List[str]] = Field(
        None,
        serialization_alias="_revinclude",
        description="Resources that refer to the results, e.g. 'Provenance:target'.",
    )

    @model_validator(mode="after")
    def _scoped(self) -> "_Search":
        given = self.model_fields_set - {"action"}
        if "id" in given:  # Epic ignores every other parameter next to _id
            return self
        if self._scope and not given & set(self._scope):
            raise ValueError(
                "this search needs one of: "
                + ", ".join(self._scope)
                + " (or id for a known resource)"
            )
        if self._one_of and not given & set(self._one_of):
            raise ValueError("this search needs one of: " + ", ".join(self._one_of))
        if not given - {"count", "include", "revinclude"}:
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


class _Update(_Create):
    resource_id: str = Field(
        ...,
        pattern=_ID,
        description="FHIR id of the resource to update. The body's 'id' is set to it.",
    )

    @model_validator(mode="after")
    def _same_id(self) -> "_Update":
        found = self.resource.setdefault("id", self.resource_id)
        if found != self.resource_id:
            raise ValueError("the id in resource must equal resource_id")
        return self


class FhirEpicOperationOutput(BaseModel):
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
    errors: Optional[List[Dict[str, Any]]] = None
    """Per-id failures of a multi-id lookup."""
    status_url: Optional[str] = None
    """Where to poll a started bulk export."""
    status_code: Optional[int] = None
    """HTTP status, for the bulk-export calls."""
    progress: Optional[str] = None
    """Epic's X-Progress header while a bulk export runs."""
    content: Optional[str] = None
    """The text of a bulk-export file (NDJSON)."""


# ---- Wave 0: the original five actions (names and shape unchanged) -----------
class FhirPatientReadInput(BaseModel):
    """Read one Patient by id, or the first match of a name search."""

    action: Literal["read_patient"] = "read_patient"
    resource_id: Optional[str] = None
    """Patient id (for example 'eXYZ123')."""
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    name: Optional[str] = None
    birthdate: Optional[str] = None
    search_params: Optional[Dict[str, str]] = None
    """Raw search parameters (for example {"family": "Smith", "given": "John"})."""


class FhirPatientSearchInput(BaseModel):
    """Search Patients by name or birthdate, or fetch several by id."""

    action: Literal["search_patients"] = "search_patients"
    resource_ids: Optional[List[str]] = Field(None, max_length=50)
    """Patient ids to fetch one by one (up to 50). A failed id is reported in 'errors'."""
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    name: Optional[str] = None
    birthdate: Optional[str] = None
    search_params: Optional[Dict[str, str]] = None


class FhirEncounterSearchInput(BaseModel):
    """Search Encounters of one patient."""

    action: Literal["search_encounter"] = "search_encounter"
    patient_id: Optional[str] = None
    status: Optional[str] = None
    date: Optional[str] = None
    search_params: Optional[Dict[str, str]] = None
    """Raw search parameters. 'patient' is required, here or as patient_id."""


class FhirDocumentReferenceCreateInput(BaseModel):
    """Create a DocumentReference with a base64 attachment."""

    action: Literal["create_document_reference"] = "create_document_reference"
    identifier: List[Dict[str, Any]]
    status: str
    """Usually 'current'."""
    type: Dict[str, Any]
    """Document type (CodeableConcept)."""
    category: Optional[List[Dict[str, Any]]] = None
    subject: str
    """Patient reference, for example 'Patient/{id}'."""
    data: str = Field(..., max_length=10 * 1024 * 1024)
    """Base64 document content, up to 10 MB."""
    content_type: Optional[str] = None
    """MIME type of the content. Defaults to 'text/plain'."""
    author: Optional[List[Dict[str, Any]]] = None
    description: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    """Epic needs context.encounter for clinical notes (for example LOINC 34108-1 or 34117-2)."""
    additional_fields: Optional[Dict[str, Any]] = None
    """Further DocumentReference fields merged into the body."""

    @field_validator("data", mode="after")
    @classmethod
    def _base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except Exception:
            raise ValueError("data must be a valid base64-encoded string") from None
        return value


class FhirDocumentReferenceSearchInput(BaseModel):
    """Search DocumentReferences."""

    action: Literal["search_document_reference"] = "search_document_reference"
    search_params: Dict[str, str]
    """Search parameters (for example {"patient": "eXYZ123"})."""


# ---- Operations ---------------------------------------------------------------
class FetchNextPageInput(_Base):
    "Follow the 'next' link of a search result"

    action: Literal["fetch_next_page"] = "fetch_next_page"
    url: str = Field(
        ...,
        description="The next_url of a previous search. It must be on the configured Epic server.",
    )


class PatientMatchInput(_Base):
    "Find the patients that match demographics (Patient/$match)"

    action: Literal["patient_match"] = "patient_match"
    patient: Dict[str, Any] = Field(
        ..., description="A Patient resource holding the known demographics (name, birthDate, ...)."
    )
    only_certain_matches: Optional[bool] = Field(None, description="Return only certain matches.")
    count: Optional[int] = Field(None, ge=1, description="Maximum number of matches.")


class PatientSummaryInput(_ById):
    "International Patient Summary of one patient (Patient/{id}/$summary)"

    action: Literal["patient_summary"] = "patient_summary"


class ValueSetExpandInput(_Base):
    "Expand a value set Epic supports (ValueSet/$expand)"

    action: Literal["value_set_expand"] = "value_set_expand"
    url: str = Field(..., description="Canonical URL of the value set.")
    filter: Optional[str] = Field(None, description="Text filter for the codes.")


class BulkExportGroupInput(_ById):
    "Start a bulk export of a patient group (Group/{id}/$export)"

    action: Literal["bulk_export_group"] = "bulk_export_group"
    type: Optional[str] = Field(
        None,
        description="Comma-separated resource types to export. Binary is returned only when named here.",
    )
    include_associated_data: Optional[Literal["LatestProvenanceResources"]] = Field(
        None, description="Add the latest Provenance of each resource."
    )
    type_filter: Optional[str] = Field(
        None,
        description=(
            "Comma-separated search queries, each 'Resource?param=value'. Needs 'type'. Reserved "
            "characters in a query must be URL-encoded and its commas backslash-escaped."
        ),
    )

    @model_validator(mode="after")
    def _filter_needs_type(self) -> "BulkExportGroupInput":
        if self.type_filter and not self.type:
            raise ValueError("type_filter needs type")
        return self


class BulkExportStatusInput(_Base):
    "Check a bulk export (status_url from bulk_export_group)"

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


# ---- Wave 1: US Core -------------------------------------------------------


class ReadAllergyIntoleranceInput(_ById):
    "Read a AllergyIntolerance by id"

    action: Literal["read_allergy_intolerance"] = "read_allergy_intolerance"


class SearchAllergyIntoleranceInput(_Search):
    "Search AllergyIntolerance resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_allergy_intolerance"] = "search_allergy_intolerance"
    clinical_status: Optional[str] = Field(
        None,
        serialization_alias="clinical-status",
        description="Refine a search for AllergyIntolerance resources by clinicalStatus. Active is the only supported clinical status to search by.",
    )
    patient: Optional[str] = Field(
        None, description="Search for AllergyIntolerance resources for a specified patient ID."
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    asserter: Optional[str] = Field(None)
    category: Optional[str] = Field(None)
    code: Optional[str] = Field(None)
    criticality: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    last_date: Optional[str] = Field(None, serialization_alias="last-date")
    manifestation: Optional[str] = Field(None)
    onset: Optional[str] = Field(None)
    recorder: Optional[str] = Field(None)
    route: Optional[str] = Field(None)
    severity: Optional[str] = Field(None)
    type: Optional[str] = Field(None)
    verification_status: Optional[str] = Field(None, serialization_alias="verification-status")


class ReadCarePlanInput(_ById):
    "Read a CarePlan by id"

    action: Literal["read_care_plan"] = "read_care_plan"


class SearchCarePlanInput(_Search):
    "Search CarePlan resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_care_plan"] = "search_care_plan"
    activity_date: Optional[str] = Field(None, serialization_alias="activity-date")
    category: Optional[str] = Field(
        None,
        description="Search for CarePlans of the given type. This is a required search parameter.",
    )
    encounter: Optional[str] = Field(None)
    part_of: Optional[str] = Field(None, serialization_alias="part-of")
    patient: Optional[str] = Field(
        None,
        description='Search for CarePlan resources using a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    status: Optional[str] = Field(None)
    subject: Optional[str] = Field(
        None,
        description='Search for CarePlan resources using a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    activity_code: Optional[str] = Field(None, serialization_alias="activity-code")
    activity_reference: Optional[str] = Field(None, serialization_alias="activity-reference")
    based_on: Optional[str] = Field(None, serialization_alias="based-on")
    care_team: Optional[str] = Field(None, serialization_alias="care-team")
    condition: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    goal: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    instantiates_canonical: Optional[str] = Field(
        None, serialization_alias="instantiates-canonical"
    )
    intent: Optional[str] = Field(None)
    performer: Optional[str] = Field(None)
    replaces: Optional[str] = Field(None)


class ReadCareTeamInput(_ById):
    "Read a CareTeam by id"

    action: Literal["read_care_team"] = "read_care_team"


class SearchCareTeamInput(_Search):
    "Search CareTeam resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_care_team"] = "search_care_team"
    patient: Optional[str] = Field(
        None,
        description='Search for CareTeam resources using a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    status: Optional[str] = Field(
        None,
        description="Refine a search based on the CareTeam's status. Currently only active is supported.",
    )
    subject: Optional[str] = Field(
        None,
        description='Search for CareTeam resources using a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    category: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    encounter: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    participant: Optional[str] = Field(None)


class ReadConditionInput(_ById):
    "Read a Condition by id"

    action: Literal["read_condition"] = "read_condition"


class SearchConditionInput(_Search):
    "Search Condition resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_condition"] = "search_condition"
    abatement_date: Optional[str] = Field(
        None,
        serialization_alias="abatement-date",
        description="Search for Conditions with a specified abatement date.",
    )
    category: Optional[str] = Field(None, description="Search for Condition resources by category.")
    clinical_status: Optional[str] = Field(
        None,
        serialization_alias="clinical-status",
        description="Refine a search for Condition resources by clinicalStatus. Only clinical statuses of 'inactive', 'resolved' and 'active' are supported for health concerns and problem list items. Only clinical statuses of 'inactive' and 'active' are supported for infections.",
    )
    code: Optional[str] = Field(None, description="Search for Conditions with a specified code.")
    encounter: Optional[str] = Field(
        None, description="Search for Condition resources for specific encounters."
    )
    onset_date: Optional[str] = Field(
        None,
        serialization_alias="onset-date",
        description="Search for Conditions with a specified onset date.",
    )
    patient: Optional[str] = Field(
        None,
        description='Search for Condition resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    recorded_date: Optional[str] = Field(
        None,
        serialization_alias="recorded-date",
        description="Search for Conditions with a specified recorded date.",
    )
    subject: Optional[str] = Field(
        None,
        description='Search for Condition resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    abatement_string: Optional[str] = Field(None, serialization_alias="abatement-string")
    asserter: Optional[str] = Field(None)
    body_site: Optional[str] = Field(None, serialization_alias="body-site")
    evidence: Optional[str] = Field(None)
    evidence_detail: Optional[str] = Field(None, serialization_alias="evidence-detail")
    identifier: Optional[str] = Field(None)
    onset_info: Optional[str] = Field(None, serialization_alias="onset-info")
    severity: Optional[str] = Field(None)
    stage: Optional[str] = Field(None)
    verification_status: Optional[str] = Field(None, serialization_alias="verification-status")


class ReadCoverageInput(_ById):
    "Read a Coverage by id"

    action: Literal["read_coverage"] = "read_coverage"


class SearchCoverageInput(_Search):
    "Search Coverage resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_coverage"] = "search_coverage"
    beneficiary: Optional[str] = Field(
        None,
        description='Search for Coverage resource for a specific patient ID. You can use "patient" or "beneficiary" equivalently, but they cannot be used at the same time for different references.',
    )
    patient: Optional[str] = Field(
        None,
        description='Search for Coverage resource for a specific patient ID. You can use "patient" or "beneficiary" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    class_type: Optional[str] = Field(None, serialization_alias="class-type")
    class_value: Optional[str] = Field(None, serialization_alias="class-value")
    dependent: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    payor: Optional[str] = Field(None)
    policy_holder: Optional[str] = Field(None, serialization_alias="policy-holder")
    status: Optional[str] = Field(None)
    subscriber: Optional[str] = Field(None)
    type: Optional[str] = Field(None)


class ReadDeviceInput(_ById):
    "Read a Device by id"

    action: Literal["read_device"] = "read_device"


class SearchDeviceInput(_Search):
    "Search Device resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_device"] = "search_device"
    device_name: Optional[str] = Field(
        None,
        serialization_alias="device-name",
        description="A string that will match the Device.deviceName.name field. Not case sensitive.",
    )
    manufacturer: Optional[str] = Field(None, description="Manufacturer of the device.")
    model: Optional[str] = Field(None, description="Model number of the device.")
    patient: Optional[str] = Field(
        None,
        description="The patient in whom this device is implanted. This is a required parameter.",
    )
    udi_carrier: Optional[str] = Field(
        None,
        serialization_alias="udi-carrier",
        description="The UDI barcode string - matches static UDI.",
    )
    udi_di: Optional[str] = Field(
        None, serialization_alias="udi-di", description="The UDI device identifier (DI)."
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    identifier: Optional[str] = Field(None)
    location: Optional[str] = Field(None)
    organization: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    type: Optional[str] = Field(None)


class ReadDiagnosticReportInput(_ById):
    "Read a DiagnosticReport by id"

    action: Literal["read_diagnostic_report"] = "read_diagnostic_report"


class SearchDiagnosticReportInput(_Search):
    "Search DiagnosticReport resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_diagnostic_report"] = "search_diagnostic_report"
    category: Optional[str] = Field(
        None, description="Refine a search for DiagnosticReport resources by category."
    )
    code: Optional[str] = Field(
        None, description="Refine a search for DiagnosticReport resources by code."
    )
    date: Optional[str] = Field(
        None,
        description="Refine a search for DiagnosticReport resources by specifying a date or date range that a DiagnosticReport was resulted or recorded. Enter dates in ISO format (YYYY[-MM[-DD[THH:MM[:SS][TZ]]]]). Local server time is assumed if time zone information is not included.",
    )
    identifier: Optional[str] = Field(
        None,
        description="Refine search by specifying an identifier, such as the internal order ID or the accession number.",
    )
    patient: Optional[str] = Field(
        None,
        description='Search for DiagnosticReport resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    subject: Optional[str] = Field(
        None,
        description='Search for DiagnosticReport resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    lastupdated: Optional[str] = Field(
        None,
        serialization_alias="_lastupdated",
        description="Refine a search by the meta.lastUpdated element.",
    )
    based_on: Optional[str] = Field(None, serialization_alias="based-on")
    conclusion: Optional[str] = Field(None)
    encounter: Optional[str] = Field(None)
    issued: Optional[str] = Field(None)
    media: Optional[str] = Field(None)
    performer: Optional[str] = Field(None)
    result: Optional[str] = Field(None)
    results_interpreter: Optional[str] = Field(None, serialization_alias="results-interpreter")
    specimen: Optional[str] = Field(None)
    status: Optional[str] = Field(None)


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

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_goal"] = "search_goal"
    category: Optional[str] = Field(None, description="Refines a search by Goal category.")
    lifecycle_status: Optional[str] = Field(
        None,
        serialization_alias="lifecycle-status",
        description="Refines a search based on Goal lifecycle status.",
    )
    patient: Optional[str] = Field(
        None,
        description='Search for Patient resources for a specific patient ID. You can use "patient" or "subject" equivalently but they can\'t be used at the same time for different references.',
    )
    start_date: Optional[str] = Field(
        None,
        serialization_alias="start-date",
        description="Refines a search based on Goal start date.",
    )
    subject: Optional[str] = Field(
        None,
        description='Search for Patient resources for a specific patient ID. You can use "patient" or "subject" equivalently but they can\'t be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    achievement_status: Optional[str] = Field(None, serialization_alias="achievement-status")
    identifier: Optional[str] = Field(None)
    target_date: Optional[str] = Field(None, serialization_alias="target-date")


class ReadImmunizationInput(_ById):
    "Read a Immunization by id"

    action: Literal["read_immunization"] = "read_immunization"


class SearchImmunizationInput(_Search):
    "Search Immunization resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_immunization"] = "search_immunization"
    date: Optional[str] = Field(
        None,
        description="Refine a search for Immunization resources by vaccine administration date. Enter dates in ISO format (YYYY[-MM[-DD[THH:MM[:SS][TZ]]]]). Local server time is assumed if time zone information is not included.",
    )
    patient: Optional[str] = Field(
        None, description="Search for Immunization resources for a specified patient ID."
    )
    status: Optional[str] = Field(
        None, description="Refine a search for Immunization resources by status."
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    identifier: Optional[str] = Field(None)
    location: Optional[str] = Field(None)
    lot_number: Optional[str] = Field(None, serialization_alias="lot-number")
    manufacturer: Optional[str] = Field(None)
    performer: Optional[str] = Field(None)
    reaction: Optional[str] = Field(None)
    reaction_date: Optional[str] = Field(None, serialization_alias="reaction-date")
    reason_code: Optional[str] = Field(None, serialization_alias="reason-code")
    reason_reference: Optional[str] = Field(None, serialization_alias="reason-reference")
    series: Optional[str] = Field(None)
    status_reason: Optional[str] = Field(None, serialization_alias="status-reason")
    target_disease: Optional[str] = Field(None, serialization_alias="target-disease")
    vaccine_code: Optional[str] = Field(None, serialization_alias="vaccine-code")


class ReadLocationInput(_ById):
    "Read a Location by id"

    action: Literal["read_location"] = "read_location"


class SearchLocationInput(_Search):
    "Search Location resources"

    action: Literal["search_location"] = "search_location"
    type: Optional[str] = Field(
        None, description="Search for Location resources with a specified location type."
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    address: Optional[str] = Field(None)
    address_city: Optional[str] = Field(None, serialization_alias="address-city")
    address_country: Optional[str] = Field(None, serialization_alias="address-country")
    address_postalcode: Optional[str] = Field(None, serialization_alias="address-postalcode")
    address_state: Optional[str] = Field(None, serialization_alias="address-state")
    address_use: Optional[str] = Field(None, serialization_alias="address-use")
    endpoint: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    name: Optional[str] = Field(None)
    operational_status: Optional[str] = Field(None, serialization_alias="operational-status")
    organization: Optional[str] = Field(None)
    partof: Optional[str] = Field(None)
    status: Optional[str] = Field(None)


class ReadMedicationInput(_ById):
    "Read a Medication by id"

    action: Literal["read_medication"] = "read_medication"


class SearchMedicationInput(_Search):
    "Search Medication resources"

    action: Literal["search_medication"] = "search_medication"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadMedicationDispenseInput(_ById):
    "Read a MedicationDispense by id"

    action: Literal["read_medication_dispense"] = "read_medication_dispense"


class SearchMedicationDispenseInput(_Search):
    "Search MedicationDispense resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_medication_dispense"] = "search_medication_dispense"
    category: Optional[str] = Field(
        None,
        description="Refine a search for MedicationDispense resources by category. By default, the search returns all categories. Categories of inpatient, outpatient, community, discharge, and the Netherlands specific SNOMED code 422037009 are supported.",
    )
    patient: Optional[str] = Field(
        None,
        description='Search for MedicationDispense resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    subject: Optional[str] = Field(
        None,
        description='Search for MedicationDispense resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for difference references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    code: Optional[str] = Field(None)
    context: Optional[str] = Field(None)
    destination: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    medication: Optional[str] = Field(None)
    performer: Optional[str] = Field(None)
    prescription: Optional[str] = Field(None)
    receiver: Optional[str] = Field(None)
    responsibleparty: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    type: Optional[str] = Field(None)
    whenhandedover: Optional[str] = Field(None)
    whenprepared: Optional[str] = Field(None)


class ReadMedicationRequestInput(_ById):
    "Read a MedicationRequest by id"

    action: Literal["read_medication_request"] = "read_medication_request"


class SearchMedicationRequestInput(_Search):
    "Search MedicationRequest resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_medication_request"] = "search_medication_request"
    category: Optional[str] = Field(
        None,
        description="Refine a search for MedicationRequest resources by category. By default, the search returns all categories. Categories of inpatient, outpatient, community, and discharge are supported.",
    )
    date: Optional[str] = Field(
        None,
        description="Refine a search for MedicationRequest resources for a given patient by specifying a date or a range of dates for when the medication was activated.",
    )
    identifier: Optional[str] = Field(
        None,
        description="(Netherlands Only) Refine a search for MedicationRequest resources for a given patient by specifying the ZIB (MA/VV) identifier",
    )
    intent: Optional[str] = Field(
        None, description="Refine a search for MedicationRequest resources by one or more intents."
    )
    patient: Optional[str] = Field(
        None,
        description='Search for MedicationRequest resources for a specified patient ID. You can use "patient" and "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    period_of_use: Optional[str] = Field(
        None,
        serialization_alias="period-of-use",
        description="(Netherlands Only) Refine a search for MedicationRequest resources for a given patient by specifying the period of use of the resource",
    )
    pharmaceutical_treatment_identifier: Optional[str] = Field(
        None,
        serialization_alias="pharmaceutical-treatment-identifier",
        description="(Netherlands Only) Refine a search for MedicationRequest resources for a given patient by specifying the treatment (MBH) ID",
    )
    status: Optional[str] = Field(
        None, description="Refine a search for MedicationRequest resources by one or more statuses."
    )
    subject: Optional[str] = Field(
        None,
        description='Search for MedicationRequest resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    authoredon: Optional[str] = Field(None)
    code: Optional[str] = Field(None)
    encounter: Optional[str] = Field(None)
    intended_dispenser: Optional[str] = Field(None, serialization_alias="intended-dispenser")
    intended_performer: Optional[str] = Field(None, serialization_alias="intended-performer")
    intended_performertype: Optional[str] = Field(
        None, serialization_alias="intended-performertype"
    )
    medication: Optional[str] = Field(None)
    priority: Optional[str] = Field(None)
    requester: Optional[str] = Field(None)


class ReadObservationInput(_ById):
    "Read a Observation by id"

    action: Literal["read_observation"] = "read_observation"


class SearchObservationInput(_Search):
    "Search Observation resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")
    _one_of: ClassVar[tuple[str, ...]] = ("category", "code")

    action: Literal["search_observation"] = "search_observation"
    based_on: Optional[str] = Field(
        None,
        serialization_alias="based-on",
        description="Refine a search for Observation resources by specifying a Reference associated with the Observation.",
    )
    category: Optional[str] = Field(
        None,
        description="Refine a search for Observation resources by category or SNOMED code. Epic categories are laboratory, vital-signs, social-history, core-characteristics, LDA, LDA-property, LDA-assessment, functional-mental-status, periodontal, labor-delivery, newborn-delivery, obstetrics-gynecology, survey, sdoh, imaging, and care-experience-preference. Supported SNOMED codes include 384821006, 118228005, 252275004, 275711006, 68793005, 395124008, 314076009, 19851009, and 405825005.",
    )
    code: Optional[str] = Field(
        None,
        description="Refine a search for Observation resources by LOINC code, SNOMED code, flowsheet row IDs, or SmartData Identifiers. Codes associated with the labor-delivery and newborn-delivery categories require that the category be specified.",
    )
    date: Optional[str] = Field(
        None,
        description="Refine a search for Observation resources by specifying a date or date range that an Observation was resulted or recorded. Enter dates in ISO format (YYYY[-MM[-DD[THH:MM[:SS][TZ]]]]). Local server time is assumed if time zone information is not included.",
    )
    focus: Optional[str] = Field(
        None,
        description="Refine a search for Observation resources by specifying a Reference associated with the Observation.",
    )
    issued: Optional[str] = Field(
        None,
        description="Refine a search for Observation resources by specifying a date or date range that an Observation was made available. Enter dates in ISO format (YYYY[-MM[-DD[THH:MM[:SS][TZ]]]]). Local server time is assumed if time zone information is not included.",
    )
    patient: Optional[str] = Field(
        None,
        description='Search for Observation resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    subject: Optional[str] = Field(
        None,
        description='Search for Observation resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    lastupdated: Optional[str] = Field(
        None,
        serialization_alias="_lastupdated",
        description="Refine a search using the meta.lastUpdated element.",
    )
    combo_code: Optional[str] = Field(None, serialization_alias="combo-code")
    combo_data_absent_reason: Optional[str] = Field(
        None, serialization_alias="combo-data-absent-reason"
    )
    combo_value_concept: Optional[str] = Field(None, serialization_alias="combo-value-concept")
    component_code: Optional[str] = Field(None, serialization_alias="component-code")
    component_data_absent_reason: Optional[str] = Field(
        None, serialization_alias="component-data-absent-reason"
    )
    component_value_concept: Optional[str] = Field(
        None, serialization_alias="component-value-concept"
    )
    data_absent_reason: Optional[str] = Field(None, serialization_alias="data-absent-reason")
    derived_from: Optional[str] = Field(None, serialization_alias="derived-from")
    device: Optional[str] = Field(None)
    encounter: Optional[str] = Field(None)
    has_member: Optional[str] = Field(None, serialization_alias="has-member")
    identifier: Optional[str] = Field(None)
    method: Optional[str] = Field(None)
    part_of: Optional[str] = Field(None, serialization_alias="part-of")
    performer: Optional[str] = Field(None)
    specimen: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    value_concept: Optional[str] = Field(None, serialization_alias="value-concept")
    value_date: Optional[str] = Field(None, serialization_alias="value-date")
    value_string: Optional[str] = Field(None, serialization_alias="value-string")


class ReadOrganizationInput(_ById):
    "Read a Organization by id"

    action: Literal["read_organization"] = "read_organization"


class SearchOrganizationInput(_Search):
    "Search Organization resources"

    action: Literal["search_organization"] = "search_organization"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadPractitionerInput(_ById):
    "Read a Practitioner by id"

    action: Literal["read_practitioner"] = "read_practitioner"


class SearchPractitionerInput(_Search):
    "Search Practitioner resources"

    action: Literal["search_practitioner"] = "search_practitioner"
    address: Optional[str] = Field(
        None,
        description="Any part of an address (street, city, etc.) where a practitioner can be visited. When used, family is also required. Only respected if no other address parameters are populated. Only the first instance of this parameter is respected.",
    )
    address_city: Optional[str] = Field(
        None,
        serialization_alias="address-city",
        description="The city where a practitioner can be visited. When used, address-state is also required. Only the first instance of this parameter is respected.",
    )
    address_postalcode: Optional[str] = Field(
        None,
        serialization_alias="address-postalcode",
        description="The zip code where a practitioner can be found. When used, family is also required.",
    )
    address_state: Optional[str] = Field(
        None,
        serialization_alias="address-state",
        description="The state where a practitioner can be found. When used, family is also required. Only the first instance of this parameter is respected.",
    )
    family: Optional[str] = Field(
        None,
        description="A practitioner's family (last) name. Only the first instance of this parameter is respected.",
    )
    given: Optional[str] = Field(
        None,
        description="A practitioner's given (first) name. When used, family is also required. Only the first instance of this parameter is respected.",
    )
    identifier: Optional[str] = Field(
        None,
        description="A practitioner's identifier in the format <code system>|<code>. When this parameter is provided, all others (except _id) are ignored.",
    )
    name: Optional[str] = Field(
        None,
        description="Any part of a practitioner's name. For full names, format should be first last. When specified, family and given are ignored. Only the first instance of this parameter is respected.",
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    active: Optional[str] = Field(None)
    address_country: Optional[str] = Field(None, serialization_alias="address-country")
    address_use: Optional[str] = Field(None, serialization_alias="address-use")
    communication: Optional[str] = Field(None)
    email: Optional[str] = Field(None)
    gender: Optional[str] = Field(None)
    phone: Optional[str] = Field(None)
    phonetic: Optional[str] = Field(None)
    telecom: Optional[str] = Field(None)


class ReadPractitionerRoleInput(_ById):
    "Read a PractitionerRole by id"

    action: Literal["read_practitioner_role"] = "read_practitioner_role"


class SearchPractitionerRoleInput(_Search):
    "Search PractitionerRole resources"

    action: Literal["search_practitioner_role"] = "search_practitioner_role"
    email: Optional[str] = Field(
        None,
        description="Refine a search for a PractitionerRole by entering a valid email address. Code system is ignored.",
    )
    identifier: Optional[str] = Field(
        None,
        description="Search for PractitionerRoles using identifiers. A code system is required. The code must be prepended with URN:OID. Some codes may need to be URL encoded prior to query.",
    )
    location: Optional[str] = Field(
        None,
        description="Search for PractitionerRoles using a Location ID. Only the first instance of this parameter is respected.",
    )
    organization: Optional[str] = Field(
        None,
        description="Search for PractitionerRoles using an Organization ID. Only the first instance of this parameter is respected.",
    )
    phone: Optional[str] = Field(
        None,
        description="Refine a search for a PractitionerRole by entering a valid phone number. Code system is ignored.",
    )
    practitioner: Optional[str] = Field(
        None,
        description="Search for PractitionerRoles for a specified Practitioner ID. Only the first instance of this parameter is respected.",
    )
    role: Optional[str] = Field(
        None,
        description="Refine a search for a PractitionerRole by entering a valid role. System must be included.",
    )
    specialty: Optional[str] = Field(
        None,
        description="Search for PractitionerRoles for a given specialty. A code system is required. Depending on the organization, NUCC may be supported.",
    )
    telecom: Optional[str] = Field(
        None,
        description="Refine a search for a PractitionerRole for a specific telecom. System must be specified as either 'phone' or 'email'",
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    active: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    endpoint: Optional[str] = Field(None)
    service: Optional[str] = Field(None)


class ReadProcedureInput(_ById):
    "Read a Procedure by id"

    action: Literal["read_procedure"] = "read_procedure"


class SearchProcedureInput(_Search):
    "Search Procedure resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_procedure"] = "search_procedure"
    category: Optional[str] = Field(
        None,
        description="Refine a search for Procedure resources by category. Only the following values are supported: 103693007 (diagnostic procedures), 387713003 (surgical procedures), 9632001 (nursing procedures, Netherlands only), 225317005 or freedom-restricting-intervention (restricting intervention, Netherlands only), and 410606002 (SDOH intervention) are supported.",
    )
    code: Optional[str] = Field(None, description="Refine a search for Procedure based on code.")
    date: Optional[str] = Field(
        None,
        description="Refine a search for Procedure resources by specifying a date or date range that a Procedure was resulted. Enter dates in ISO format (YYYY[-MM[-DD]]).",
    )
    identifier: Optional[str] = Field(
        None, description="Refine a search for Procedure by identifier."
    )
    patient: Optional[str] = Field(
        None,
        description='Search for Procedure resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    subject: Optional[str] = Field(
        None,
        description='Search for Procedure resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    based_on: Optional[str] = Field(None, serialization_alias="based-on")
    encounter: Optional[str] = Field(None)
    instantiates_canonical: Optional[str] = Field(
        None, serialization_alias="instantiates-canonical"
    )
    location: Optional[str] = Field(None)
    part_of: Optional[str] = Field(None, serialization_alias="part-of")
    performer: Optional[str] = Field(None)
    reason_code: Optional[str] = Field(None, serialization_alias="reason-code")
    reason_reference: Optional[str] = Field(None, serialization_alias="reason-reference")
    status: Optional[str] = Field(None)


class ReadServiceRequestInput(_ById):
    "Read a ServiceRequest by id"

    action: Literal["read_service_request"] = "read_service_request"


class SearchServiceRequestInput(_Search):
    "Search ServiceRequest resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_service_request"] = "search_service_request"
    authored: Optional[str] = Field(
        None,
        description="Refine a search for ServiceRequests for a particular date and time. By default, all ServiceRequests are returned.",
    )
    category: Optional[str] = Field(
        None,
        description="Refine a search for ServiceRequests of a particular category. By default, all ServiceRequests are returned.",
    )
    code: Optional[str] = Field(
        None,
        description="Refine a search for ServiceRequest based on code. By default all service requests are returned.",
    )
    encounter: Optional[str] = Field(
        None,
        description="Search for ServiceRequest resources for specific encounters. If not provided, all ServiceRequest results are returned.",
    )
    identifier: Optional[str] = Field(
        None, description="Refine a search for ServiceRequest by identifier."
    )
    onlyscannable: Optional[str] = Field(
        None,
        description='Refine a search for ServiceRequest resources to scannable only. By default, all ServiceRequests are returned. Use a value of "true" to only return scannable ServiceRequests. Can only be used when the application is launched from Hyperspace.',
    )
    patient: Optional[str] = Field(
        None,
        description='Search ServiceRequest resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    requester: Optional[str] = Field(
        None,
        description="Refine a search for ServiceRequest resources by individual making the request. By default, all ServiceRequests are returned.",
    )
    status: Optional[str] = Field(
        None,
        description="Refine a search for ServiceRequest resources by status. By default, all ServiceRequests are returned. Statuses of draft, active, completed, revoked, and unknown are supported.",
    )
    subject: Optional[str] = Field(
        None,
        description='Search ServiceRequest resources for a specified patient ID. You can use "patient" or "subject" equivalently but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    based_on: Optional[str] = Field(None, serialization_alias="based-on")
    body_site: Optional[str] = Field(None, serialization_alias="body-site")
    instantiates_canonical: Optional[str] = Field(
        None, serialization_alias="instantiates-canonical"
    )
    intent: Optional[str] = Field(None)
    occurrence: Optional[str] = Field(None)
    performer: Optional[str] = Field(None)
    performer_type: Optional[str] = Field(None, serialization_alias="performer-type")
    priority: Optional[str] = Field(None)
    replaces: Optional[str] = Field(None)
    requisition: Optional[str] = Field(None)
    specimen: Optional[str] = Field(None)


class ReadSpecimenInput(_ById):
    "Read a Specimen by id"

    action: Literal["read_specimen"] = "read_specimen"


class SearchSpecimenInput(_Search):
    "Search Specimen resources"

    action: Literal["search_specimen"] = "search_specimen"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadRelatedPersonInput(_ById):
    "Read a RelatedPerson by id"

    action: Literal["read_related_person"] = "read_related_person"


class SearchRelatedPersonInput(_Search):
    "Search RelatedPerson resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_related_person"] = "search_related_person"
    patient: Optional[str] = Field(None)
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    active: Optional[str] = Field(None)
    address: Optional[str] = Field(None)
    address_city: Optional[str] = Field(None, serialization_alias="address-city")
    address_country: Optional[str] = Field(None, serialization_alias="address-country")
    address_postalcode: Optional[str] = Field(None, serialization_alias="address-postalcode")
    address_state: Optional[str] = Field(None, serialization_alias="address-state")
    address_use: Optional[str] = Field(None, serialization_alias="address-use")
    birthdate: Optional[str] = Field(None)
    email: Optional[str] = Field(None)
    gender: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    name: Optional[str] = Field(None)
    phone: Optional[str] = Field(None)
    phonetic: Optional[str] = Field(None)
    relationship: Optional[str] = Field(None)
    telecom: Optional[str] = Field(None)


class ReadBinaryInput(_ById):
    "Read a Binary by id"

    action: Literal["read_binary"] = "read_binary"


class SearchBinaryInput(_Search):
    "Search Binary resources"

    action: Literal["search_binary"] = "search_binary"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadProvenanceInput(_ById):
    "Read a Provenance by id"

    action: Literal["read_provenance"] = "read_provenance"


# ---- Wave 2: other resources -----------------------------------------------


class ReadAccountInput(_ById):
    "Read a Account by id"

    action: Literal["read_account"] = "read_account"


class SearchAccountInput(_Search):
    "Search Account resources"

    action: Literal["search_account"] = "search_account"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadAdverseEventInput(_ById):
    "Read a AdverseEvent by id"

    action: Literal["read_adverse_event"] = "read_adverse_event"


class SearchAdverseEventInput(_Search):
    "Search AdverseEvent resources"

    _scope: ClassVar[tuple[str, ...]] = ("subject",)

    action: Literal["search_adverse_event"] = "search_adverse_event"
    research_subject: Optional[str] = Field(
        None,
        serialization_alias="research-subject",
        description="Search for AdverseEvent resources for a specified research study participant.",
    )
    seriousness: Optional[str] = Field(
        None,
        description="Refine a search for AdverseEvent resources by seriousness of the event. Serious and Non-serious are the only supported values.",
    )
    study: Optional[str] = Field(
        None, description="Search for AdverseEvent resources for a specified study ID."
    )
    subject: Optional[str] = Field(
        None, description="Search for AdverseEvent resources for a specified patient ID."
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    actuality: Optional[str] = Field(None)
    category: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    event: Optional[str] = Field(None)
    location: Optional[str] = Field(None)
    recorder: Optional[str] = Field(None)
    resultingcondition: Optional[str] = Field(None)
    severity: Optional[str] = Field(None)
    substance: Optional[str] = Field(None)


class ReadAppointmentInput(_ById):
    "Read a Appointment by id"

    action: Literal["read_appointment"] = "read_appointment"


class SearchAppointmentInput(_Search):
    "Search Appointment resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_appointment"] = "search_appointment"
    date: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    onlyscannable: Optional[str] = Field(
        None,
        description='Refine a search for Appointment resources to scannable appointments only. By default, all Appointments are returned. Use a value of "true" to only return scannable appointments. Can only be used when the application is launched from Hyperspace.',
    )
    patient: Optional[str] = Field(
        None, description="Search for Appointment resources for a specified patient ID."
    )
    service_category: Optional[str] = Field(
        None,
        serialization_alias="service-category",
        description="Search on the type of appointment. Enter 'surgery' for scheduled surgery appointments, and 'appointment' for all other types of appointments.",
    )
    status: Optional[str] = Field(None)
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    actor: Optional[str] = Field(None)
    appointment_type: Optional[str] = Field(None, serialization_alias="appointment-type")
    based_on: Optional[str] = Field(None, serialization_alias="based-on")
    location: Optional[str] = Field(None)
    part_status: Optional[str] = Field(None, serialization_alias="part-status")
    practitioner: Optional[str] = Field(None)
    reason_code: Optional[str] = Field(None, serialization_alias="reason-code")
    reason_reference: Optional[str] = Field(None, serialization_alias="reason-reference")
    service_type: Optional[str] = Field(None, serialization_alias="service-type")
    slot: Optional[str] = Field(None)
    specialty: Optional[str] = Field(None)
    supporting_info: Optional[str] = Field(None, serialization_alias="supporting-info")


class ReadBodyStructureInput(_ById):
    "Read a BodyStructure by id"

    action: Literal["read_body_structure"] = "read_body_structure"


class SearchBodyStructureInput(_Search):
    "Search BodyStructure resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_body_structure"] = "search_body_structure"
    identifier: Optional[str] = Field(
        None,
        description='Refine a search for BodyStructure resources by identifier. Enter using the structure "[system]|[search string]".',
    )
    location: Optional[str] = Field(
        None, description="Refine a search for BodyStructure resources by location."
    )
    morphology: Optional[str] = Field(
        None, description="Refine a search for BodyStructure resources by morphology."
    )
    patient: Optional[str] = Field(
        None,
        description='Search for BodyStructure resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    subject: Optional[str] = Field(
        None,
        description='Search for BodyStructure resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadClaimInput(_ById):
    "Read a Claim by id"

    action: Literal["read_claim"] = "read_claim"


class SearchClaimInput(_Search):
    "Search Claim resources"

    action: Literal["search_claim"] = "search_claim"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadCommunicationInput(_ById):
    "Read a Communication by id"

    action: Literal["read_communication"] = "read_communication"


class SearchCommunicationInput(_Search):
    "Search Communication resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_communication"] = "search_communication"
    based_on: Optional[str] = Field(None, serialization_alias="based-on")
    category: Optional[str] = Field(
        None,
        description="Refine a search to include only Communication resources with the given categories.",
    )
    encounter: Optional[str] = Field(
        None,
        description="Refine a search to include Communication resources from only the encounters provided.",
    )
    part_of: Optional[str] = Field(None, serialization_alias="part-of")
    patient: Optional[str] = Field(
        None,
        description='Search for Communication resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    subject: Optional[str] = Field(
        None,
        description='Search for Communication resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    identifier: Optional[str] = Field(None)
    instantiates_canonical: Optional[str] = Field(
        None, serialization_alias="instantiates-canonical"
    )
    medium: Optional[str] = Field(None)
    received: Optional[str] = Field(None)
    recipient: Optional[str] = Field(None)
    sender: Optional[str] = Field(None)
    sent: Optional[str] = Field(None)
    status: Optional[str] = Field(None)


class ReadConsentInput(_ById):
    "Read a Consent by id"

    action: Literal["read_consent"] = "read_consent"


class SearchConsentInput(_Search):
    "Search Consent resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_consent"] = "search_consent"
    category: Optional[str] = Field(None, description="Search for Consent resources by category.")
    patient: Optional[str] = Field(
        None,
        description='Search for Consent resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    status: Optional[str] = Field(None, description="Search for Consent resources by status.")
    subject: Optional[str] = Field(
        None,
        description='Search for Consent resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    action_: Optional[str] = Field(None, serialization_alias="action")
    actor: Optional[str] = Field(None)
    consentor: Optional[str] = Field(None)
    data: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    organization: Optional[str] = Field(None)
    period: Optional[str] = Field(None)
    purpose: Optional[str] = Field(None)
    scope: Optional[str] = Field(None)
    security_label: Optional[str] = Field(None, serialization_alias="security-label")
    source_reference: Optional[str] = Field(None, serialization_alias="source-reference")


class ReadContractInput(_ById):
    "Read a Contract by id"

    action: Literal["read_contract"] = "read_contract"


class SearchContractInput(_Search):
    "Search Contract resources"

    action: Literal["search_contract"] = "search_contract"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadDeviceRequestInput(_ById):
    "Read a DeviceRequest by id"

    action: Literal["read_device_request"] = "read_device_request"


class SearchDeviceRequestInput(_Search):
    "Search DeviceRequest resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_device_request"] = "search_device_request"
    patient: Optional[str] = Field(
        None, description="Search for DeviceRequest resource for a specified patient ID."
    )
    status: Optional[str] = Field(
        None, description="Search for a DeviceRequest based on a device request status"
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    authored_on: Optional[str] = Field(None, serialization_alias="authored-on")
    based_on: Optional[str] = Field(None, serialization_alias="based-on")
    code: Optional[str] = Field(None)
    device: Optional[str] = Field(None)
    encounter: Optional[str] = Field(None)
    event_date: Optional[str] = Field(None, serialization_alias="event-date")
    group_identifier: Optional[str] = Field(None, serialization_alias="group-identifier")
    identifier: Optional[str] = Field(None)
    instantiates_canonical: Optional[str] = Field(
        None, serialization_alias="instantiates-canonical"
    )
    insurance: Optional[str] = Field(None)
    intent: Optional[str] = Field(None)
    performer: Optional[str] = Field(None)
    prior_request: Optional[str] = Field(None, serialization_alias="prior-request")
    requester: Optional[str] = Field(None)
    subject: Optional[str] = Field(None)


class ReadDeviceUseStatementInput(_ById):
    "Read a DeviceUseStatement by id"

    action: Literal["read_device_use_statement"] = "read_device_use_statement"


class SearchDeviceUseStatementInput(_Search):
    "Search DeviceUseStatement resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_device_use_statement"] = "search_device_use_statement"
    patient: Optional[str] = Field(
        None,
        description='Search for DeviceUseStatement resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    subject: Optional[str] = Field(
        None,
        description='Search for DeviceUseStatement resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    device: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)


class ReadEndpointInput(_ById):
    "Read a Endpoint by id"

    action: Literal["read_endpoint"] = "read_endpoint"


class SearchEndpointInput(_Search):
    "Search Endpoint resources"

    action: Literal["search_endpoint"] = "search_endpoint"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadEpisodeOfCareInput(_ById):
    "Read a EpisodeOfCare by id"

    action: Literal["read_episode_of_care"] = "read_episode_of_care"


class SearchEpisodeOfCareInput(_Search):
    "Search EpisodeOfCare resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_episode_of_care"] = "search_episode_of_care"
    patient: Optional[str] = Field(
        None, description="Search for EpisodeOfCare resources for a specified patient ID."
    )
    status: Optional[str] = Field(
        None,
        description="Refine a search for EpisodeOfCare resources by status. Active, finished, and cancelled are the only supported statuses.",
    )
    type: Optional[str] = Field(
        None, description="Refine a search for EpisodeOfCare resources by type."
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    care_manager: Optional[str] = Field(None, serialization_alias="care-manager")
    condition: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    incoming_referral: Optional[str] = Field(None, serialization_alias="incoming-referral")
    organization: Optional[str] = Field(None)


class ReadExplanationOfBenefitInput(_ById):
    "Read a ExplanationOfBenefit by id"

    action: Literal["read_explanation_of_benefit"] = "read_explanation_of_benefit"


class SearchExplanationOfBenefitInput(_Search):
    "Search ExplanationOfBenefit resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_explanation_of_benefit"] = "search_explanation_of_benefit"
    billable_period_start: Optional[str] = Field(
        None,
        serialization_alias="billable-period-start",
        description="Refine a search for ExplanationOfBenefit by billing period start. Only claims that have a billing period that starts in the supplied date range are returned.",
    )
    created: Optional[str] = Field(
        None,
        description="Refine a search for ExplanationOfBenefit resources by creation date for the claim. Enter dates in ISO format (YYYY[-MM[-DD[THH:MM[:SS][TZ]]]]). Local server time is assumed if time zone information is not included.",
    )
    identifier: Optional[str] = Field(
        None,
        description="Refine a search for ExplanationOfBenefit by unique claim ID identifier. Only claims matching one of the unique claim IDs will be returned.",
    )
    patient: Optional[str] = Field(
        None,
        description='Search for ExplanationOfBenefit resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    service_date: Optional[str] = Field(
        None,
        serialization_alias="service-date",
        description="Refine a search for ExplanationOfBenefit by service date. Only claims that have a service period or billing period that overlap with the date range in service-date are returned.",
    )
    service_start_date: Optional[str] = Field(
        None,
        serialization_alias="service-start-date",
        description="Refine a search for ExplanationOfBenefit by service start date. Only claims that have a service start date or billing period start in the supplied date range are returned.",
    )
    type: Optional[str] = Field(
        None,
        description='Refine a search for ExplanationOfBenefit by type. Types of "institutional", "professional", "oral", and "pharmacy" are supported. Vision claims are treated as "professional". Only claims matching one of the supplied types are returned.',
    )
    use: Optional[str] = Field(
        None,
        description="Refine a search for ExplanationOfBenefit by use. By default, the search returns all uses. Use of claim and preauthorization are supported.",
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    care_team: Optional[str] = Field(None, serialization_alias="care-team")
    claim: Optional[str] = Field(None)
    coverage: Optional[str] = Field(None)
    detail_udi: Optional[str] = Field(None, serialization_alias="detail-udi")
    disposition: Optional[str] = Field(None)
    encounter: Optional[str] = Field(None)
    enterer: Optional[str] = Field(None)
    facility: Optional[str] = Field(None)
    item_udi: Optional[str] = Field(None, serialization_alias="item-udi")
    payee: Optional[str] = Field(None)
    procedure_udi: Optional[str] = Field(None, serialization_alias="procedure-udi")
    provider: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    subdetail_udi: Optional[str] = Field(None, serialization_alias="subdetail-udi")


class ReadFamilyMemberHistoryInput(_ById):
    "Read a FamilyMemberHistory by id"

    action: Literal["read_family_member_history"] = "read_family_member_history"


class SearchFamilyMemberHistoryInput(_Search):
    "Search FamilyMemberHistory resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_family_member_history"] = "search_family_member_history"
    patient: Optional[str] = Field(
        None, description="Required: the patient whose family history you'd like to search."
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    code: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    instantiates_canonical: Optional[str] = Field(
        None, serialization_alias="instantiates-canonical"
    )
    relationship: Optional[str] = Field(None)
    sex: Optional[str] = Field(None)
    status: Optional[str] = Field(None)


class ReadFlagInput(_ById):
    "Read a Flag by id"

    action: Literal["read_flag"] = "read_flag"


class SearchFlagInput(_Search):
    "Search Flag resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_flag"] = "search_flag"
    category: Optional[str] = Field(
        None, description="Refine a search for Flag resources by category."
    )
    encounter: Optional[str] = Field(
        None, description="Refine a search for Flag resources by encounter."
    )
    patient: Optional[str] = Field(
        None,
        description='Search for Flag resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    status: Optional[str] = Field(None, description="Refine a search for Flag resources by status.")
    subject: Optional[str] = Field(
        None,
        description='Search for Flag resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    author: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)


class ReadGroupInput(_ById):
    "Read a Group by id"

    action: Literal["read_group"] = "read_group"


class SearchGroupInput(_Search):
    "Search Group resources"

    action: Literal["search_group"] = "search_group"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadImagingStudyInput(_ById):
    "Read a ImagingStudy by id"

    action: Literal["read_imaging_study"] = "read_imaging_study"


class SearchImagingStudyInput(_Search):
    "Search ImagingStudy resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_imaging_study"] = "search_imaging_study"
    identifier: Optional[str] = Field(
        None,
        description="Search for ImagingStudy resources by a study's identifier. You can use the identifier parameter as the only parameter in a search or in conjunction with other parameters. An ImagingStudy's identifier must be in the format <code system>|<code> or <ID Type>|<ID>.",
    )
    patient: Optional[str] = Field(
        None,
        description='Search for ImagingStudy resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    subject: Optional[str] = Field(
        None,
        description='Search for ImagingStudy resources for a specified patient ID. You can use "patient" and "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    basedon: Optional[str] = Field(None)
    bodysite: Optional[str] = Field(None)
    dicom_class: Optional[str] = Field(None, serialization_alias="dicom-class")
    encounter: Optional[str] = Field(None)
    endpoint: Optional[str] = Field(None)
    instance: Optional[str] = Field(None)
    interpreter: Optional[str] = Field(None)
    modality: Optional[str] = Field(None)
    performer: Optional[str] = Field(None)
    reason: Optional[str] = Field(None)
    referrer: Optional[str] = Field(None)
    series: Optional[str] = Field(None)
    started: Optional[str] = Field(None)
    status: Optional[str] = Field(None)


class ReadImmunizationRecommendationInput(_ById):
    "Read a ImmunizationRecommendation by id"

    action: Literal["read_immunization_recommendation"] = "read_immunization_recommendation"


class SearchImmunizationRecommendationInput(_Search):
    "Search ImmunizationRecommendation resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_immunization_recommendation"] = "search_immunization_recommendation"
    patient: Optional[str] = Field(
        None,
        description="The FHIR id of a patient whose immunization recommendations you'd like to obtain. Only one of either patient or subject needs to be specified.",
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    date: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    information: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    support: Optional[str] = Field(None)
    target_disease: Optional[str] = Field(None, serialization_alias="target-disease")
    vaccine_type: Optional[str] = Field(None, serialization_alias="vaccine-type")


class ReadListInput(_ById):
    "Read a List by id"

    action: Literal["read_list"] = "read_list"


class SearchListInput(_Search):
    "Search List resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_list"] = "search_list"
    code: Optional[str] = Field(
        None,
        description='Refine a search for List resources by list type. Accepted values include "medications," "allergies," "immunizations," "problems," "pedigree-list," "hospital-problems," and "patients."',
    )
    encounter: Optional[str] = Field(
        None, description="Refine a search for List resources by encounter."
    )
    identifier: Optional[str] = Field(
        None,
        description="Refine a search for List resource by internal identifier. Only applies to the Patient List sub-resource.",
    )
    patient: Optional[str] = Field(
        None,
        description='Refine a search for List resources by patient. You can use "patient" or "subject" equivalently, but not at the same time with different references.',
    )
    subject: Optional[str] = Field(
        None,
        description='Refine a search for List resources by patient. You can use "patient" or "subject" equivalently, but not at the same time with different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    date: Optional[str] = Field(None)
    empty_reason: Optional[str] = Field(None, serialization_alias="empty-reason")
    item: Optional[str] = Field(None)
    notes: Optional[str] = Field(None)
    source: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    title: Optional[str] = Field(None)


class ReadMediaInput(_ById):
    "Read a Media by id"

    action: Literal["read_media"] = "read_media"


class SearchMediaInput(_Search):
    "Search Media resources"

    action: Literal["search_media"] = "search_media"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadMedicationAdministrationInput(_ById):
    "Read a MedicationAdministration by id"

    action: Literal["read_medication_administration"] = "read_medication_administration"


class SearchMedicationAdministrationInput(_Search):
    "Search MedicationAdministration resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_medication_administration"] = "search_medication_administration"
    context: Optional[str] = Field(
        None,
        description="Refine a search for MedicationAdministration resources with one or more linked reference.",
    )
    effective_time: Optional[str] = Field(
        None,
        serialization_alias="effective-time",
        description="Refine a search for MedicationAdministration resources for a given patient by specifying a date or a range of dates for the administration.",
    )
    medication_code: Optional[str] = Field(
        None,
        serialization_alias="medication.code",
        description="Refine a search for MedicationAdministration resources by the code of the associated medication",
    )
    patient: Optional[str] = Field(
        None,
        description='Search for MedicationAdministration resources for a specified patient ID. You can use "patient" and "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    performer: Optional[str] = Field(
        None,
        description="Refine a search for MedicationAdministration resources by one or more associated reference.",
    )
    request: Optional[str] = Field(
        None,
        description="Refine a search for MedicationAdministration resources by associated reference.",
    )
    status: Optional[str] = Field(
        None,
        description="Refine a search for MedicationAdministration resources with one or more statuses.",
    )
    subject: Optional[str] = Field(
        None,
        description='Search for MedicationAdministration resources for a specified patient ID. You can use "patient" and "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    code: Optional[str] = Field(None)
    device: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    medication: Optional[str] = Field(None)
    reason_given: Optional[str] = Field(None, serialization_alias="reason-given")
    reason_not_given: Optional[str] = Field(None, serialization_alias="reason-not-given")


class ReadMedicationStatementInput(_ById):
    "Read a MedicationStatement by id"

    action: Literal["read_medication_statement"] = "read_medication_statement"


class SearchMedicationStatementInput(_Search):
    "Search MedicationStatement resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_medication_statement"] = "search_medication_statement"
    category: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    patient: Optional[str] = Field(None)
    period_of_use: Optional[str] = Field(None, serialization_alias="period-of-use")
    pharmaceutical_treatment_identifier: Optional[str] = Field(
        None, serialization_alias="pharmaceutical-treatment-identifier"
    )
    subject: Optional[str] = Field(None)
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    code: Optional[str] = Field(None)
    context: Optional[str] = Field(None)
    effective: Optional[str] = Field(None)
    medication: Optional[str] = Field(None)
    part_of: Optional[str] = Field(None, serialization_alias="part-of")
    source: Optional[str] = Field(None)
    status: Optional[str] = Field(None)


class ReadNutritionOrderInput(_ById):
    "Read a NutritionOrder by id"

    action: Literal["read_nutrition_order"] = "read_nutrition_order"


class SearchNutritionOrderInput(_Search):
    "Search NutritionOrder resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_nutrition_order"] = "search_nutrition_order"
    patient: Optional[str] = Field(
        None, description="Search for NutritionOrder resources for the specified patient ID."
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    additive: Optional[str] = Field(None)
    datetime: Optional[str] = Field(None)
    encounter: Optional[str] = Field(None)
    formula: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)
    instantiates_canonical: Optional[str] = Field(
        None, serialization_alias="instantiates-canonical"
    )
    oraldiet: Optional[str] = Field(None)
    provider: Optional[str] = Field(None)
    status: Optional[str] = Field(None)
    supplement: Optional[str] = Field(None)


class ReadQuestionnaireInput(_ById):
    "Read a Questionnaire by id"

    action: Literal["read_questionnaire"] = "read_questionnaire"


class SearchQuestionnaireInput(_Search):
    "Search Questionnaire resources"

    action: Literal["search_questionnaire"] = "search_questionnaire"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadQuestionnaireResponseInput(_ById):
    "Read a QuestionnaireResponse by id"

    action: Literal["read_questionnaire_response"] = "read_questionnaire_response"


class SearchQuestionnaireResponseInput(_Search):
    "Search QuestionnaireResponse resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_questionnaire_response"] = "search_questionnaire_response"
    encounter: Optional[str] = Field(
        None, description="Search for QuestionnaireResponses by encounter"
    )
    patient: Optional[str] = Field(
        None,
        description='Search for QuestionnaireResponses for a specified patient. You can also use "subject" equivalently',
    )
    subject: Optional[str] = Field(
        None,
        description='Search for QuestionnaireResponses for a specified patient or research subject. You can also use "patient" equivalently if subject references a patient.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    author: Optional[str] = Field(None)
    authored: Optional[str] = Field(None)
    based_on: Optional[str] = Field(None, serialization_alias="based-on")
    identifier: Optional[str] = Field(None)
    part_of: Optional[str] = Field(None, serialization_alias="part-of")
    questionnaire: Optional[str] = Field(None)
    source: Optional[str] = Field(None)
    status: Optional[str] = Field(None)


class ReadRequestGroupInput(_ById):
    "Read a RequestGroup by id"

    action: Literal["read_request_group"] = "read_request_group"


class SearchRequestGroupInput(_Search):
    "Search RequestGroup resources"

    action: Literal["search_request_group"] = "search_request_group"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadResearchStudyInput(_ById):
    "Read a ResearchStudy by id"

    action: Literal["read_research_study"] = "read_research_study"


class SearchResearchStudyInput(_Search):
    "Search ResearchStudy resources"

    action: Literal["search_research_study"] = "search_research_study"
    identifier: Optional[str] = Field(
        None,
        description="Search for the ResearchStudy resource for a specified study code or NCT number",
    )
    status: Optional[str] = Field(
        None, description="Search for the ResearchStudy resource for a specified status"
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    category: Optional[str] = Field(None)
    date: Optional[str] = Field(None)
    focus: Optional[str] = Field(None)
    keyword: Optional[str] = Field(None)
    location: Optional[str] = Field(None)
    partof: Optional[str] = Field(None)
    principalinvestigator: Optional[str] = Field(None)
    protocol: Optional[str] = Field(None)
    site: Optional[str] = Field(None)
    sponsor: Optional[str] = Field(None)
    title: Optional[str] = Field(None)


class ReadResearchSubjectInput(_ById):
    "Read a ResearchSubject by id"

    action: Literal["read_research_subject"] = "read_research_subject"


class SearchResearchSubjectInput(_Search):
    "Search ResearchSubject resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient",)

    action: Literal["search_research_subject"] = "search_research_subject"
    patient: Optional[str] = Field(
        None, description="Search for the ResearchSubject resources related to a specified patient"
    )
    status: Optional[str] = Field(
        None, description="Search for the ResearchSubject resources for a specified status."
    )
    study: Optional[str] = Field(
        None, description="Search for the ResearchSubject resources related to a specified study"
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    date: Optional[str] = Field(None)
    identifier: Optional[str] = Field(None)


class ReadSubstanceInput(_ById):
    "Read a Substance by id"

    action: Literal["read_substance"] = "read_substance"


class SearchSubstanceInput(_Search):
    "Search Substance resources"

    action: Literal["search_substance"] = "search_substance"
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )


class ReadTaskInput(_ById):
    "Read a Task by id"

    action: Literal["read_task"] = "read_task"


class SearchTaskInput(_Search):
    "Search Task resources"

    _scope: ClassVar[tuple[str, ...]] = ("patient", "subject")

    action: Literal["search_task"] = "search_task"
    code: Optional[str] = Field(
        None,
        description="Specify community-resource for CRRN tasks, episode-checklist for Episode Checklist tasks. When nothing is specified, all valid tasks are returned.",
    )
    encounter: Optional[str] = Field(
        None,
        description="Further refine the search for a task by specifying the encounter associated with the task.",
    )
    focus: Optional[str] = Field(None)
    patient: Optional[str] = Field(
        None,
        description='Search for Task resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    status: Optional[str] = Field(
        None, description="Restrict the search for tasks based on task status."
    )
    subject: Optional[str] = Field(
        None,
        description='Search for Task resources for a specified patient ID. You can use "patient" or "subject" equivalently, but they cannot be used at the same time for different references.',
    )
    id: Optional[str] = Field(
        None,
        serialization_alias="_id",
        description="FHIR resource IDs for the desired resources. Can be used in combination with _count, _include, and _revInclude. All others will be ignored.",
    )
    authored_on: Optional[str] = Field(None, serialization_alias="authored-on")
    based_on: Optional[str] = Field(None, serialization_alias="based-on")
    business_status: Optional[str] = Field(None, serialization_alias="business-status")
    group_identifier: Optional[str] = Field(None, serialization_alias="group-identifier")
    identifier: Optional[str] = Field(None)
    intent: Optional[str] = Field(None)
    modified: Optional[str] = Field(None)
    owner: Optional[str] = Field(None)
    part_of: Optional[str] = Field(None, serialization_alias="part-of")
    performer: Optional[str] = Field(None)
    period: Optional[str] = Field(None)
    priority: Optional[str] = Field(None)
    requester: Optional[str] = Field(None)


class ReadValueSetInput(_ById):
    "Read a ValueSet by id"

    action: Literal["read_value_set"] = "read_value_set"


# ---- Wave 3: writes --------------------------------------------------------


class CreateAllergyIntoleranceInput(_Create):
    "Create a AllergyIntolerance"

    _type: ClassVar[str] = "AllergyIntolerance"

    action: Literal["create_allergy_intolerance"] = "create_allergy_intolerance"


class CreateBodyStructureInput(_Create):
    "Create a BodyStructure"

    _type: ClassVar[str] = "BodyStructure"

    action: Literal["create_body_structure"] = "create_body_structure"


class CreateCommunicationInput(_Create):
    "Create a Communication"

    _type: ClassVar[str] = "Communication"

    action: Literal["create_communication"] = "create_communication"


class CreateConditionInput(_Create):
    "Create a Condition"

    _type: ClassVar[str] = "Condition"

    action: Literal["create_condition"] = "create_condition"


class CreateObservationInput(_Create):
    "Create a Observation"

    _type: ClassVar[str] = "Observation"

    action: Literal["create_observation"] = "create_observation"


class CreatePatientInput(_Create):
    "Create a Patient"

    _type: ClassVar[str] = "Patient"

    action: Literal["create_patient"] = "create_patient"


class CreateProcedureInput(_Create):
    "Create a Procedure"

    _type: ClassVar[str] = "Procedure"

    action: Literal["create_procedure"] = "create_procedure"


class CreateQuestionnaireResponseInput(_Create):
    "Create a QuestionnaireResponse"

    _type: ClassVar[str] = "QuestionnaireResponse"

    action: Literal["create_questionnaire_response"] = "create_questionnaire_response"


class CreateServiceRequestInput(_Create):
    "Create a ServiceRequest"

    _type: ClassVar[str] = "ServiceRequest"

    action: Literal["create_service_request"] = "create_service_request"


class UpdateBodyStructureInput(_Update):
    "Update a BodyStructure"

    _type: ClassVar[str] = "BodyStructure"

    action: Literal["update_body_structure"] = "update_body_structure"


class UpdateDiagnosticReportInput(_Update):
    "Update a DiagnosticReport"

    _type: ClassVar[str] = "DiagnosticReport"

    action: Literal["update_diagnostic_report"] = "update_diagnostic_report"


class UpdateDocumentReferenceInput(_Update):
    "Update a DocumentReference"

    _type: ClassVar[str] = "DocumentReference"

    action: Literal["update_document_reference"] = "update_document_reference"


class UpdateObservationInput(_Update):
    "Update a Observation"

    _type: ClassVar[str] = "Observation"

    action: Literal["update_observation"] = "update_observation"


class UpdateProcedureInput(_Update):
    "Update a Procedure"

    _type: ClassVar[str] = "Procedure"

    action: Literal["update_procedure"] = "update_procedure"


class UpdateServiceRequestInput(_Update):
    "Update a ServiceRequest"

    _type: ClassVar[str] = "ServiceRequest"

    action: Literal["update_service_request"] = "update_service_request"


class UpdateTaskInput(_Update):
    "Update a Task"

    _type: ClassVar[str] = "Task"

    action: Literal["update_task"] = "update_task"
