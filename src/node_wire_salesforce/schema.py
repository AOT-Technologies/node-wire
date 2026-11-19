#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
import re

SALESFORCE_ID_REGEX = re.compile(r"^[a-zA-Z0-9]{15,18}$")

CONTACT_UPDATE_FIELD_ALIASES = {
    "first_name": "FirstName",
    "last_name": "LastName",
    "email": "Email",
    "account_id": "AccountId",
}

LEAD_UPDATE_FIELD_ALIASES = {
    "first_name": "FirstName",
    "last_name": "LastName",
    "company": "Company",
    "email": "Email",
}


class SalesforceError(BaseModel):
    message: str
    code: Optional[str] = None
    fields: Optional[List[str]] = None


class SalesforceOperationOutput(BaseModel):
    success: bool = True
    resource_id: Optional[str] = None
    resource_type: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    errors: Optional[List[SalesforceError]] = None


def _validate_salesforce_id(v: str, *, field_name: str) -> str:
    if not SALESFORCE_ID_REGEX.match(v):
        raise ValueError(
            f"Invalid Salesforce {field_name} format (must be 15 or 18 alphanumeric characters)"
        )
    return v


def _coalesce_record_id(data: Dict[str, Any], *, aliases: tuple[str, ...]) -> None:
    if (data.get("record_id") or "").strip():
        return
    for key in aliases:
        val = data.get(key)
        if val is not None and str(val).strip():
            data["record_id"] = str(val).strip()
            return


def _coalesce_update_fields(data: Dict[str, Any], alias_map: Dict[str, str]) -> None:
    fields = data.get("fields")
    if not isinstance(fields, dict):
        fields = {}
    root_keys = set(alias_map.keys()) | set(alias_map.values())
    for key in root_keys:
        if key in data and data[key] is not None:
            fields[key] = data.pop(key)
    normalized: Dict[str, Any] = {}
    for key, value in fields.items():
        api_name = alias_map.get(key, key)
        if api_name not in normalized:
            normalized[api_name] = value
    data["fields"] = normalized


def coalesce_update_contact_args(data: Dict[str, Any]) -> Dict[str, Any]:
    """Merge LLM/MCP alias shapes into canonical record_id + fields for updates."""
    out = dict(data)
    _coalesce_record_id(out, aliases=("contact_id", "id", "recordId"))
    for key in ("contact_id", "id", "recordId"):
        out.pop(key, None)
    _coalesce_update_fields(out, CONTACT_UPDATE_FIELD_ALIASES)
    return out


def coalesce_update_lead_args(data: Dict[str, Any]) -> Dict[str, Any]:
    """Merge LLM/MCP alias shapes into canonical record_id + fields for updates."""
    out = dict(data)
    _coalesce_record_id(out, aliases=("lead_id", "id", "recordId"))
    for key in ("lead_id", "id", "recordId"):
        out.pop(key, None)
    _coalesce_update_fields(out, LEAD_UPDATE_FIELD_ALIASES)
    return out


def coalesce_read_delete_args(
    data: Dict[str, Any], *, id_aliases: tuple[str, ...]
) -> Dict[str, Any]:
    out = dict(data)
    _coalesce_record_id(out, aliases=id_aliases)
    for key in id_aliases:
        out.pop(key, None)
    return out


# Creation Models
class CreateLeadInput(BaseModel):
    action: Literal["create_lead"] = "create_lead"
    last_name: str = Field(..., alias="LastName")
    company: str = Field(..., alias="Company")
    first_name: Optional[str] = Field(None, alias="FirstName")
    title: Optional[str] = Field(None, alias="Title")
    email: Optional[str] = Field(None, alias="Email")
    phone: Optional[str] = Field(None, alias="Phone")
    mobile_phone: Optional[str] = Field(None, alias="MobilePhone")
    street: Optional[str] = Field(None, alias="Street")
    city: Optional[str] = Field(None, alias="City")
    state: Optional[str] = Field(None, alias="State")
    postal_code: Optional[str] = Field(None, alias="PostalCode")
    country: Optional[str] = Field(None, alias="Country")
    description: Optional[str] = Field(None, alias="Description")
    lead_source: Optional[str] = Field(None, alias="LeadSource")
    status: Optional[str] = Field(None, alias="Status")
    rating: Optional[str] = Field(None, alias="Rating")
    website: Optional[str] = Field(None, alias="Website")
    number_of_employees: Optional[int] = Field(None, alias="NumberOfEmployees")
    industry: Optional[str] = Field(None, alias="Industry")
    annual_revenue: Optional[float] = Field(None, alias="AnnualRevenue")

    model_config = ConfigDict(populate_by_name=True)


class CreateContactInput(BaseModel):
    action: Literal["create_contact"] = "create_contact"
    last_name: str = Field(..., alias="LastName")
    first_name: Optional[str] = Field(None, alias="FirstName")
    account_id: Optional[str] = Field(None, alias="AccountId")
    title: Optional[str] = Field(None, alias="Title")

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, v: Optional[str]) -> Optional[str]:
        if v and not SALESFORCE_ID_REGEX.match(v):
            raise ValueError(
                "Invalid Salesforce AccountId format (must be 15 or 18 alphanumeric characters)"
            )
        return v

    email: Optional[str] = Field(None, alias="Email")
    phone: Optional[str] = Field(None, alias="Phone")
    mobile_phone: Optional[str] = Field(None, alias="MobilePhone")
    mailing_street: Optional[str] = Field(None, alias="MailingStreet")
    mailing_city: Optional[str] = Field(None, alias="MailingCity")
    mailing_state: Optional[str] = Field(None, alias="MailingState")
    mailing_postal_code: Optional[str] = Field(None, alias="MailingPostalCode")
    mailing_country: Optional[str] = Field(None, alias="MailingCountry")
    description: Optional[str] = Field(None, alias="Description")
    lead_source: Optional[str] = Field(None, alias="LeadSource")
    department: Optional[str] = Field(None, alias="Department")

    model_config = ConfigDict(populate_by_name=True)


# Read/Delete Models — optional ID aliases for MCP JSON Schema (coalesced before validation)
class ReadLeadInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["read_lead"] = "read_lead"
    record_id: Optional[str] = None
    lead_id: Optional[str] = Field(None, description="Alias for record_id")
    id: Optional[str] = Field(None, description="Alias for record_id")

    @model_validator(mode="before")
    @classmethod
    def _coalesce(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return coalesce_read_delete_args(data, id_aliases=("lead_id", "id", "recordId"))
        return data

    @model_validator(mode="after")
    def _require_record_id(self) -> "ReadLeadInput":
        if not self.record_id:
            raise ValueError("record_id is required")
        _validate_salesforce_id(self.record_id, field_name="record_id")
        return self


class DeleteLeadInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["delete_lead"] = "delete_lead"
    record_id: Optional[str] = None
    lead_id: Optional[str] = None
    id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coalesce(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return coalesce_read_delete_args(data, id_aliases=("lead_id", "id", "recordId"))
        return data

    @model_validator(mode="after")
    def _require_record_id(self) -> "DeleteLeadInput":
        if not self.record_id:
            raise ValueError("record_id is required")
        _validate_salesforce_id(self.record_id, field_name="record_id")
        return self


class ReadContactInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["read_contact"] = "read_contact"
    record_id: Optional[str] = None
    contact_id: Optional[str] = Field(None, description="Alias for record_id")
    id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coalesce(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return coalesce_read_delete_args(data, id_aliases=("contact_id", "id", "recordId"))
        return data

    @model_validator(mode="after")
    def _require_record_id(self) -> "ReadContactInput":
        if not self.record_id:
            raise ValueError("record_id is required")
        _validate_salesforce_id(self.record_id, field_name="record_id")
        return self


class DeleteContactInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["delete_contact"] = "delete_contact"
    record_id: Optional[str] = None
    contact_id: Optional[str] = None
    id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coalesce(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return coalesce_read_delete_args(data, id_aliases=("contact_id", "id", "recordId"))
        return data

    @model_validator(mode="after")
    def _require_record_id(self) -> "DeleteContactInput":
        if not self.record_id:
            raise ValueError("record_id is required")
        _validate_salesforce_id(self.record_id, field_name="record_id")
        return self


# Update Models — optional fields at MCP schema layer; coalesced before validation
class UpdateLeadInput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    action: Literal["update_lead"] = "update_lead"
    record_id: Optional[str] = None
    lead_id: Optional[str] = Field(None, description="Alias for record_id")
    id: Optional[str] = Field(None, description="Alias for record_id")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Lead fields to update (Salesforce API names, e.g. LastName).",
    )
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    FirstName: Optional[str] = None
    LastName: Optional[str] = None
    Company: Optional[str] = None
    Email: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coalesce(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return coalesce_update_lead_args(data)
        return data

    @model_validator(mode="after")
    def _require_id_and_fields(self) -> "UpdateLeadInput":
        if not self.record_id:
            raise ValueError("record_id is required")
        _validate_salesforce_id(self.record_id, field_name="record_id")
        if not self.fields:
            raise ValueError("at least one field to update is required")
        return self


class UpdateContactInput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    action: Literal["update_contact"] = "update_contact"
    record_id: Optional[str] = None
    contact_id: Optional[str] = Field(None, description="Alias for record_id")
    id: Optional[str] = Field(None, description="Alias for record_id")
    fields: Dict[str, Any] = Field(
        default_factory=dict,
        description="Contact fields to update (Salesforce API names, e.g. LastName).",
    )
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    account_id: Optional[str] = None
    FirstName: Optional[str] = None
    LastName: Optional[str] = None
    Email: Optional[str] = None
    AccountId: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coalesce(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return coalesce_update_contact_args(data)
        return data

    @model_validator(mode="after")
    def _require_id_and_fields(self) -> "UpdateContactInput":
        if not self.record_id:
            raise ValueError("record_id is required")
        _validate_salesforce_id(self.record_id, field_name="record_id")
        if not self.fields:
            raise ValueError("at least one field to update is required")
        return self


SalesforceInput = Union[
    CreateLeadInput,
    CreateContactInput,
    ReadLeadInput,
    UpdateLeadInput,
    DeleteLeadInput,
    ReadContactInput,
    UpdateContactInput,
    DeleteContactInput,
]


# ---- REST API v68.0 and Bulk API 2.0 actions ----------------------------------------------------
# One input model per action. Fields named in the action's path go in the path; the rest go to the
# query (GET, DELETE) or the JSON body, unless a field says otherwise with `nw_in`.

SObjectName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=80)]
SalesforceRecordId = Annotated[str, Field(pattern=r"^[a-zA-Z0-9]{15,18}$")]
QueryLocator = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=100)]
ApiName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+$", max_length=100)]
PathPart = Annotated[str, Field(min_length=1, max_length=255)]


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListApiVersionsInput(_Op):
    """List the REST API versions the org supports."""

    action: Literal["list_api_versions"] = "list_api_versions"


class ListResourcesInput(_Op):
    """List the REST resources available in the configured API version."""

    action: Literal["list_resources"] = "list_resources"


class GetLimitsInput(_Op):
    """Get the org's API and feature limits."""

    action: Literal["get_limits"] = "get_limits"


class GetRecordCountsInput(_Op):
    """Get the approximate record counts of objects."""

    action: Literal["get_record_counts"] = "get_record_counts"
    sobjects: Optional[List[str]] = Field(
        None,
        description="Object names to count; all when left out",
        json_schema_extra={"nw_in": "query"},
    )


class DescribeGlobalInput(_Op):
    """List the objects in the org with basic information."""

    action: Literal["describe_global"] = "describe_global"


class GetTabsInput(_Op):
    """List the tabs the user can see."""

    action: Literal["get_tabs"] = "get_tabs"


class GetThemeInput(_Op):
    """Get the org's theme (colors and icons by object)."""

    action: Literal["get_theme"] = "get_theme"


class GetAppMenuTypesInput(_Op):
    """List the app menu types."""

    action: Literal["get_app_menu_types"] = "get_app_menu_types"


class GetAppMenuItemsInput(_Op):
    """List the items of the app switcher menu."""

    action: Literal["get_app_menu_items"] = "get_app_menu_items"


class GetMobileAppMenuItemsInput(_Op):
    """List the items of the Salesforce mobile app menu."""

    action: Literal["get_mobile_app_menu_items"] = "get_mobile_app_menu_items"


class GetSobjectInfoInput(_Op):
    """Get basic information about an object and its recent records."""

    action: Literal["get_sobject_info"] = "get_sobject_info"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )


class DescribeSobjectInput(_Op):
    """Describe an object: fields, record types, relationships."""

    action: Literal["describe_sobject"] = "describe_sobject"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )


class GetDeletedRecordsInput(_Op):
    """List records of an object deleted in a time range."""

    action: Literal["get_deleted_records"] = "get_deleted_records"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    start: str = Field(
        ..., description="Start of the range, ISO 8601", json_schema_extra={"nw_in": "query"}
    )
    end: str = Field(
        ..., description="End of the range, ISO 8601", json_schema_extra={"nw_in": "query"}
    )


class GetUpdatedRecordsInput(_Op):
    """List records of an object updated in a time range."""

    action: Literal["get_updated_records"] = "get_updated_records"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    start: str = Field(
        ..., description="Start of the range, ISO 8601", json_schema_extra={"nw_in": "query"}
    )
    end: str = Field(
        ..., description="End of the range, ISO 8601", json_schema_extra={"nw_in": "query"}
    )


class GetNamedLayoutInput(_Op):
    """Get a named layout of an object."""

    action: Literal["get_named_layout"] = "get_named_layout"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    layout_name: PathPart = Field(...)


class GetLayoutsInput(_Op):
    """Get the page layouts of an object."""

    action: Literal["get_layouts"] = "get_layouts"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )


class GetLayoutsForRecordTypeInput(_Op):
    """Get the layout of an object for one record type."""

    action: Literal["get_layouts_for_record_type"] = "get_layouts_for_record_type"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_type_id: SalesforceRecordId = Field(...)


class GetGlobalPublisherLayoutsInput(_Op):
    """Get the global publisher layouts."""

    action: Literal["get_global_publisher_layouts"] = "get_global_publisher_layouts"


class GetCompactLayoutsInput(_Op):
    """Get the compact layouts of an object."""

    action: Literal["get_compact_layouts"] = "get_compact_layouts"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )


class GetCompactLayoutsForObjectsInput(_Op):
    """Get the compact layouts of several objects."""

    action: Literal["get_compact_layouts_for_objects"] = "get_compact_layouts_for_objects"
    objects: List[str] = Field(
        ...,
        serialization_alias="q",
        description="Object names",
        json_schema_extra={"nw_in": "query"},
    )


class GetApprovalLayoutsInput(_Op):
    """Get the approval layouts of an object."""

    action: Literal["get_approval_layouts"] = "get_approval_layouts"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )


class GetApprovalLayoutInput(_Op):
    """Get the layout of one approval process of an object."""

    action: Literal["get_approval_layout"] = "get_approval_layout"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    approval_process_name: PathPart = Field(...)


class GetPlatformActionInput(_Op):
    """Get the platform actions (buttons and links) the user can see."""

    action: Literal["get_platform_action"] = "get_platform_action"


class CreateRecordInput(_Op):
    """Create a record."""

    action: Literal["create_record"] = "create_record"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    fields: Dict[str, Any] = Field(
        ...,
        description="Field values of the new record, by API name",
        json_schema_extra={"nw_in": "body"},
    )


class GetRecordInput(_Op):
    """Get a record by Id."""

    action: Literal["get_record"] = "get_record"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")
    fields: Optional[List[str]] = Field(
        None,
        description="Fields to return; all when left out",
        json_schema_extra={"nw_in": "query"},
    )


class UpdateRecordInput(_Op):
    """Update a record."""

    action: Literal["update_record"] = "update_record"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")
    fields: Dict[str, Any] = Field(
        ..., description="Field values to change, by API name", json_schema_extra={"nw_in": "body"}
    )


class DeleteRecordInput(_Op):
    """Delete a record."""

    action: Literal["delete_record"] = "delete_record"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")


class GetRecordByExternalIdInput(_Op):
    """Get a record by an external Id field."""

    action: Literal["get_record_by_external_id"] = "get_record_by_external_id"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    field_name: PathPart = Field(..., description="API name of the external Id field")
    field_value: PathPart = Field(...)
    fields: Optional[List[str]] = Field(
        None,
        description="Fields to return; all when left out",
        json_schema_extra={"nw_in": "query"},
    )


class UpsertRecordByExternalIdInput(_Op):
    """Create or update a record by an external Id field."""

    action: Literal["upsert_record_by_external_id"] = "upsert_record_by_external_id"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    field_name: PathPart = Field(..., description="API name of the external Id field")
    field_value: PathPart = Field(...)
    fields: Dict[str, Any] = Field(
        ..., description="Field values, by API name", json_schema_extra={"nw_in": "body"}
    )


class DeleteRecordByExternalIdInput(_Op):
    """Delete a record by an external Id field."""

    action: Literal["delete_record_by_external_id"] = "delete_record_by_external_id"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    field_name: PathPart = Field(..., description="API name of the external Id field")
    field_value: PathPart = Field(...)


class GetBlobFieldInput(_Op):
    """Get the content of a blob field (an attachment or document body). Returned as base64."""

    action: Literal["get_blob_field"] = "get_blob_field"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")
    blob_field: PathPart = Field(..., description="API name of the blob field, for example Body")


class GetRichTextImageInput(_Op):
    """Get an image from a rich text field. Returned as base64."""

    action: Literal["get_rich_text_image"] = "get_rich_text_image"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")
    field_name: PathPart = Field(...)
    content_reference_id: PathPart = Field(...)


class GetRelatedRecordsInput(_Op):
    """Get the records of a relationship of a record."""

    action: Literal["get_related_records"] = "get_related_records"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")
    relationship_name: PathPart = Field(...)
    fields: Optional[List[str]] = Field(
        None,
        description="Fields to return; all when left out",
        json_schema_extra={"nw_in": "query"},
    )


class UpdateRelatedRecordInput(_Op):
    """Update the related record of a relationship."""

    action: Literal["update_related_record"] = "update_related_record"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")
    relationship_name: PathPart = Field(...)
    fields: Dict[str, Any] = Field(
        ..., description="Field values to change, by API name", json_schema_extra={"nw_in": "body"}
    )


class DeleteRelatedRecordInput(_Op):
    """Delete the related record of a relationship."""

    action: Literal["delete_related_record"] = "delete_related_record"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")
    relationship_name: PathPart = Field(...)


class CreateRecordsInput(_Op):
    """Create up to 200 records in one call."""

    action: Literal["create_records"] = "create_records"
    records: List[Dict[str, Any]] = Field(
        ..., description="Records, each with an attributes.type and its fields"
    )
    all_or_none: Optional[bool] = Field(None, serialization_alias="allOrNone")


class GetRecordsInput(_Op):
    """Get several records of one object by Id."""

    action: Literal["get_records"] = "get_records"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    ids: List[str] = Field(..., json_schema_extra={"nw_in": "query"})
    fields: List[str] = Field(..., json_schema_extra={"nw_in": "query"})


class GetRecordsWithBodyInput(_Op):
    """Get several records of one object by Id, with the Ids in the body."""

    action: Literal["get_records_with_body"] = "get_records_with_body"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    ids: List[str] = Field(...)
    fields: List[str] = Field(...)


class UpdateRecordsInput(_Op):
    """Update up to 200 records in one call."""

    action: Literal["update_records"] = "update_records"
    records: List[Dict[str, Any]] = Field(
        ..., description="Records, each with an id, an attributes.type and the fields to change"
    )
    all_or_none: Optional[bool] = Field(None, serialization_alias="allOrNone")


class UpsertRecordsInput(_Op):
    """Create or update up to 200 records by an external Id field."""

    action: Literal["upsert_records"] = "upsert_records"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    external_id_field: PathPart = Field(...)
    records: List[Dict[str, Any]] = Field(...)
    all_or_none: Optional[bool] = Field(None, serialization_alias="allOrNone")


class DeleteRecordsInput(_Op):
    """Delete up to 200 records by Id."""

    action: Literal["delete_records"] = "delete_records"
    ids: List[str] = Field(..., json_schema_extra={"nw_in": "query"})
    all_or_none: Optional[bool] = Field(
        None, serialization_alias="allOrNone", json_schema_extra={"nw_in": "query"}
    )


class CompositeInput(_Op):
    """Run up to 25 subrequests in one call, each able to use the results of earlier ones."""

    action: Literal["composite"] = "composite"
    composite_request: List[Dict[str, Any]] = Field(..., description="The subrequests")
    all_or_none: Optional[bool] = Field(None, serialization_alias="allOrNone")
    collate_subrequests: Optional[bool] = Field(None, serialization_alias="collateSubrequests")


class GetCompositeResourcesInput(_Op):
    """List the composite resources."""

    action: Literal["get_composite_resources"] = "get_composite_resources"


class CompositeGraphInput(_Op):
    """Run one or more graphs of subrequests."""

    action: Literal["composite_graph"] = "composite_graph"
    graphs: List[Dict[str, Any]] = Field(...)


class CompositeBatchInput(_Op):
    """Run up to 25 independent subrequests in one call."""

    action: Literal["composite_batch"] = "composite_batch"
    batch_requests: List[Dict[str, Any]] = Field(...)
    halt_on_error: Optional[bool] = Field(None, serialization_alias="haltOnError")


class CreateRecordTreeInput(_Op):
    """Create a tree of records (parents with children) in one call."""

    action: Literal["create_record_tree"] = "create_record_tree"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    records: List[Dict[str, Any]] = Field(...)


class QueryInput(_Op):
    """Run a SOQL query."""

    action: Literal["query"] = "query"
    q: str = Field(..., description="The SOQL query", json_schema_extra={"nw_in": "query"})


class QueryMoreInput(_Op):
    """Get the next page of a SOQL query."""

    action: Literal["query_more"] = "query_more"
    locator: QueryLocator = Field(...)


class QueryAllInput(_Op):
    """Run a SOQL query that includes deleted and archived records."""

    action: Literal["query_all"] = "query_all"
    q: str = Field(..., description="The SOQL query", json_schema_extra={"nw_in": "query"})


class QueryAllMoreInput(_Op):
    """Get the next page of a queryAll."""

    action: Literal["query_all_more"] = "query_all_more"
    locator: QueryLocator = Field(...)


class SearchInput(_Op):
    """Run a SOSL search."""

    action: Literal["search"] = "search"
    q: str = Field(
        ...,
        description="The SOSL search, for example FIND {Acme}",
        json_schema_extra={"nw_in": "query"},
    )


class ParameterizedSearchGetInput(_Op):
    """Search with simple parameters instead of SOSL."""

    action: Literal["parameterized_search_get"] = "parameterized_search_get"
    q: str = Field(..., json_schema_extra={"nw_in": "query"})
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Other parameters: sobject, fields, defaultLimit, overallLimit, offset, in, division, metadata...",
        json_schema_extra={"nw_in": "query_map"},
    )


class ParameterizedSearchPostInput(_Op):
    """Search with a request body for complex searches (several objects, filters, data categories)."""

    action: Literal["parameterized_search_post"] = "parameterized_search_post"
    search: Dict[str, Any] = Field(
        ...,
        description="The search request. It must contain q",
        json_schema_extra={"nw_in": "body"},
    )


class GetSearchScopeOrderInput(_Op):
    """Get the objects in the user's search scope and order."""

    action: Literal["get_search_scope_order"] = "get_search_scope_order"


class GetSearchResultLayoutsInput(_Op):
    """Get the search result layouts of objects."""

    action: Literal["get_search_result_layouts"] = "get_search_result_layouts"
    objects: List[str] = Field(
        ...,
        serialization_alias="q",
        description="Object names",
        json_schema_extra={"nw_in": "query"},
    )


class SearchSuggestionsInput(_Op):
    """Get record suggestions for a search string (autocomplete)."""

    action: Literal["search_suggestions"] = "search_suggestions"
    q: str = Field(..., json_schema_extra={"nw_in": "query"})
    sobject: str = Field(..., description="Object to search", json_schema_extra={"nw_in": "query"})
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Other parameters: language, limit, topSuggestionsOnly...",
        json_schema_extra={"nw_in": "query_map"},
    )


class SearchTitleMatchesInput(_Op):
    """Get knowledge article titles that match a search string."""

    action: Literal["search_title_matches"] = "search_title_matches"
    q: str = Field(..., json_schema_extra={"nw_in": "query"})
    language: str = Field(..., json_schema_extra={"nw_in": "query"})
    publish_status: str = Field(
        ..., serialization_alias="publishStatus", json_schema_extra={"nw_in": "query"}
    )
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Other parameters: articleTypes, topArticlesOnly, limit...",
        json_schema_extra={"nw_in": "query_map"},
    )


class SearchSuggestedQueriesInput(_Op):
    """Get suggested search queries for a search string."""

    action: Literal["search_suggested_queries"] = "search_suggested_queries"
    q: str = Field(..., json_schema_extra={"nw_in": "query"})
    language: str = Field(..., json_schema_extra={"nw_in": "query"})
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Other parameters: limit, channel...",
        json_schema_extra={"nw_in": "query_map"},
    )


class RunNamedQueryInput(_Op):
    """Run a named query API (a SOQL query saved in Setup)."""

    action: Literal["run_named_query"] = "run_named_query"
    query_name: ApiName = Field(...)
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="The named query's own parameters",
        json_schema_extra={"nw_in": "query_map"},
    )


class ListListViewsInput(_Op):
    """List the list views of an object."""

    action: Literal["list_list_views"] = "list_list_views"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    parameters: Optional[Dict[str, Any]] = Field(
        None, description="Parameters: limit, offset, q", json_schema_extra={"nw_in": "query_map"}
    )


class GetListViewInput(_Op):
    """Get the basic information of a list view."""

    action: Literal["get_list_view"] = "get_list_view"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    list_view_id: SalesforceRecordId = Field(...)


class DescribeListViewInput(_Op):
    """Describe a list view: columns, filters, sort."""

    action: Literal["describe_list_view"] = "describe_list_view"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    list_view_id: SalesforceRecordId = Field(...)


class GetListViewResultsInput(_Op):
    """Run a list view and return its records."""

    action: Literal["get_list_view_results"] = "get_list_view_results"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    list_view_id: SalesforceRecordId = Field(...)
    limit: Optional[int] = Field(None, json_schema_extra={"nw_in": "query"})
    offset: Optional[int] = Field(None, json_schema_extra={"nw_in": "query"})
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Other parameters: q, orderBy...",
        json_schema_extra={"nw_in": "query_map"},
    )


class GetRecentListViewsInput(_Op):
    """Get the list views the user used recently for an object."""

    action: Literal["get_recent_list_views"] = "get_recent_list_views"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )


class GetRecentItemsInput(_Op):
    """Get the items the user viewed or referenced recently."""

    action: Literal["get_recent_items"] = "get_recent_items"
    limit: Optional[int] = Field(None, json_schema_extra={"nw_in": "query"})


class GetRelevantItemsInput(_Op):
    """Get the items most relevant to the user."""

    action: Literal["get_relevant_items"] = "get_relevant_items"
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Parameters: sobjects, lastUpdatedId",
        json_schema_extra={"nw_in": "query_map"},
    )


class ListQuickActionsInput(_Op):
    """List the global quick actions."""

    action: Literal["list_quick_actions"] = "list_quick_actions"


class CreateRecordWithQuickActionInput(_Op):
    """Run a global quick action that creates a record."""

    action: Literal["create_record_with_quick_action"] = "create_record_with_quick_action"
    action_name: PathPart = Field(...)
    record: Dict[str, Any] = Field(..., description="Field values of the record, by API name")


class ListSobjectQuickActionsInput(_Op):
    """List the quick actions of an object."""

    action: Literal["list_sobject_quick_actions"] = "list_sobject_quick_actions"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )


class GetSobjectQuickActionInput(_Op):
    """Get a quick action of an object."""

    action: Literal["get_sobject_quick_action"] = "get_sobject_quick_action"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    action_name: PathPart = Field(...)


class InvokeSobjectQuickActionInput(_Op):
    """Run a quick action of an object."""

    action: Literal["invoke_sobject_quick_action"] = "invoke_sobject_quick_action"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    action_name: PathPart = Field(...)
    record: Dict[str, Any] = Field(..., description="Field values of the record, by API name")
    context_id: Optional[SalesforceRecordId] = Field(
        None, serialization_alias="contextId", description="Id of the record the action runs on"
    )


class DescribeSobjectQuickActionInput(_Op):
    """Describe a quick action of an object."""

    action: Literal["describe_sobject_quick_action"] = "describe_sobject_quick_action"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    action_name: PathPart = Field(...)


class GetQuickActionDefaultValuesInput(_Op):
    """Get the default values of a quick action."""

    action: Literal["get_quick_action_default_values"] = "get_quick_action_default_values"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    action_name: PathPart = Field(...)


class GetQuickActionDefaultValuesForRecordInput(_Op):
    """Get the default values of a quick action for a record."""

    action: Literal["get_quick_action_default_values_for_record"] = (
        "get_quick_action_default_values_for_record"
    )
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    action_name: PathPart = Field(...)
    context_id: SalesforceRecordId = Field(...)


class ListProcessApprovalsInput(_Op):
    """List the approval processes and pending approvals."""

    action: Literal["list_process_approvals"] = "list_process_approvals"


class SubmitProcessApprovalInput(_Op):
    """Submit, approve or reject records in approval processes."""

    action: Literal["submit_process_approval"] = "submit_process_approval"
    requests: List[Dict[str, Any]] = Field(
        ..., description="Approval requests, each with actionType, contextId and so on"
    )


class ListProcessRulesInput(_Op):
    """List the workflow rules of all objects."""

    action: Literal["list_process_rules"] = "list_process_rules"


class TriggerProcessRulesInput(_Op):
    """Trigger the workflow rules of records."""

    action: Literal["trigger_process_rules"] = "trigger_process_rules"
    context_ids: List[str] = Field(
        ..., serialization_alias="contextIds", description="Ids of the records"
    )


class ListProcessRulesForSobjectInput(_Op):
    """List the workflow rules of an object."""

    action: Literal["list_process_rules_for_sobject"] = "list_process_rules_for_sobject"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )


class GetProcessRuleInput(_Op):
    """Get a workflow rule of an object."""

    action: Literal["get_process_rule"] = "get_process_rule"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    rule_id: SalesforceRecordId = Field(...)


class TriggerProcessRuleInput(_Op):
    """Trigger a workflow rule for a record."""

    action: Literal["trigger_process_rule"] = "trigger_process_rule"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    rule_id: SalesforceRecordId = Field(...)
    context_id: SalesforceRecordId = Field(
        ..., serialization_alias="contextId", description="Id of the record"
    )


class ListInvocableActionsInput(_Op):
    """List the types of invocable actions."""

    action: Literal["list_invocable_actions"] = "list_invocable_actions"


class ListStandardInvocableActionsInput(_Op):
    """List the standard invocable actions."""

    action: Literal["list_standard_invocable_actions"] = "list_standard_invocable_actions"


class ListCustomInvocableActionsInput(_Op):
    """List the custom invocable actions."""

    action: Literal["list_custom_invocable_actions"] = "list_custom_invocable_actions"


class DeployDataKitComponentsInput(_Op):
    """Deploy data kit components with the Deploy Data Kit Components flow."""

    action: Literal["deploy_data_kit_components"] = "deploy_data_kit_components"
    inputs: List[Dict[str, Any]] = Field(..., description="Flow inputs")


class GetProductSchedulesInput(_Op):
    """Get the schedules of an opportunity product."""

    action: Literal["get_product_schedules"] = "get_product_schedules"
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")


class CreateProductSchedulesInput(_Op):
    """Establish or re-establish the schedules of an opportunity product."""

    action: Literal["create_product_schedules"] = "create_product_schedules"
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")
    schedule: Dict[str, Any] = Field(
        ...,
        description="The schedule: type, quantity, quantityScheduleType, revenue...",
        json_schema_extra={"nw_in": "body"},
    )


class DeleteProductSchedulesInput(_Op):
    """Delete the schedules of an opportunity product."""

    action: Literal["delete_product_schedules"] = "delete_product_schedules"
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")


class GetSupportResourcesInput(_Op):
    """List the support resources."""

    action: Literal["get_support_resources"] = "get_support_resources"


class ListDataCategoryGroupsInput(_Op):
    """List the data category groups."""

    action: Literal["list_data_category_groups"] = "list_data_category_groups"
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Parameters: sObjectName, topCategoriesDepth",
        json_schema_extra={"nw_in": "query_map"},
    )


class GetDataCategoryDetailInput(_Op):
    """Get a data category and its children."""

    action: Literal["get_data_category_detail"] = "get_data_category_detail"
    group: PathPart = Field(...)
    category: PathPart = Field(...)
    parameters: Optional[Dict[str, Any]] = Field(
        None, description="Parameters: sObjectName", json_schema_extra={"nw_in": "query_map"}
    )


class ListKnowledgeArticlesInput(_Op):
    """List or search knowledge articles."""

    action: Literal["list_knowledge_articles"] = "list_knowledge_articles"
    language: str = Field(
        ...,
        serialization_alias="Accept-Language",
        description="Language code of the articles, for example en-US",
        json_schema_extra={"nw_in": "header"},
    )
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Parameters: q, channel, categories, queryMethod, sort, order, pageSize",
        json_schema_extra={"nw_in": "query_map"},
    )


class GetKnowledgeArticleInput(_Op):
    """Get a knowledge article by Id or URL name."""

    action: Literal["get_knowledge_article"] = "get_knowledge_article"
    article_id_or_url_name: PathPart = Field(...)
    language: str = Field(
        ...,
        serialization_alias="Accept-Language",
        description="Language code of the articles, for example en-US",
        json_schema_extra={"nw_in": "header"},
    )


class GetKnowledgeLanguageSettingsInput(_Op):
    """Get the knowledge language settings."""

    action: Literal["get_knowledge_language_settings"] = "get_knowledge_language_settings"


class GetSuggestedArticlesInput(_Op):
    """Get articles suggested for a new record of an object."""

    action: Literal["get_suggested_articles"] = "get_suggested_articles"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    language: str = Field(..., json_schema_extra={"nw_in": "query"})
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Parameters: subject, description, articleTypes, limit...",
        json_schema_extra={"nw_in": "query_map"},
    )


class GetSuggestedArticlesForRecordInput(_Op):
    """Get articles suggested for a record."""

    action: Literal["get_suggested_articles_for_record"] = "get_suggested_articles_for_record"
    sobject: SObjectName = Field(
        ..., description="API name of the object, for example Account or MyObject__c"
    )
    record_id: SalesforceRecordId = Field(..., description="15 or 18 character record Id")
    language: str = Field(..., json_schema_extra={"nw_in": "query"})
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Parameters: articleTypes, limit...",
        json_schema_extra={"nw_in": "query_map"},
    )


class GetEmbeddedServiceConfigInput(_Op):
    """Get an Embedded Service configuration."""

    action: Literal["get_embedded_service_config"] = "get_embedded_service_config"
    config_name: PathPart = Field(..., description="Developer name of the configuration")


class GetEventSchemaByNameInput(_Op):
    """Get the schema of a platform event by its name."""

    action: Literal["get_event_schema_by_name"] = "get_event_schema_by_name"
    event_name: SObjectName = Field(...)
    payload_format: Optional[str] = Field(
        None,
        serialization_alias="payloadFormat",
        description="EXPANDED or COMPACT",
        json_schema_extra={"nw_in": "query"},
    )


class GetEventSchemaByIdInput(_Op):
    """Get the schema of a platform event by schema Id."""

    action: Literal["get_event_schema_by_id"] = "get_event_schema_by_id"
    schema_id: PathPart = Field(...)
    payload_format: Optional[str] = Field(
        None,
        serialization_alias="payloadFormat",
        description="EXPANDED or COMPACT",
        json_schema_extra={"nw_in": "query"},
    )


class CompileConsentInput(_Op):
    """Compile the consent settings of records for one action."""

    action: Literal["compile_consent"] = "compile_consent"
    consent_action: PathPart = Field(
        ..., description="For example processing, portability or shouldforget"
    )
    ids: List[str] = Field(..., json_schema_extra={"nw_in": "query"})
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="Other parameters: mode, status...",
        json_schema_extra={"nw_in": "query_map"},
    )


class CompileMultiConsentInput(_Op):
    """Compile the consent settings of records for several actions."""

    action: Literal["compile_multi_consent"] = "compile_multi_consent"
    actions: List[str] = Field(..., json_schema_extra={"nw_in": "query"})
    ids: List[str] = Field(..., json_schema_extra={"nw_in": "query"})
    parameters: Optional[Dict[str, Any]] = Field(
        None, description="Other parameters: mode...", json_schema_extra={"nw_in": "query_map"}
    )


class WriteConsentInput(_Op):
    """Write consent settings for records."""

    action: Literal["write_consent"] = "write_consent"
    consent_action: PathPart = Field(...)
    ids: List[str] = Field(..., json_schema_extra={"nw_in": "query"})
    consent: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional settings: captureDate, captureSource, consentName...",
        json_schema_extra={"nw_in": "body"},
    )


class RequestPortabilityDataInput(_Op):
    """Start compiling the data of a data subject for a portability request."""

    action: Literal["request_portability_data"] = "request_portability_data"
    data_subject_id: SalesforceRecordId = Field(..., serialization_alias="dataSubjectId")
    policy_name: str = Field(..., serialization_alias="policyName")


class GetPortabilityStatusInput(_Op):
    """Get the status of a portability request."""

    action: Literal["get_portability_status"] = "get_portability_status"
    policy_file_id: str = Field(
        ..., serialization_alias="policyFileId", json_schema_extra={"nw_in": "query"}
    )


class GetSchedulingResourcesInput(_Op):
    """List the Salesforce Scheduler resources."""

    action: Literal["get_scheduling_resources"] = "get_scheduling_resources"


class GetAppointmentSlotsInput(_Op):
    """Get the appointment slots for a work type and territories."""

    action: Literal["get_appointment_slots"] = "get_appointment_slots"
    request: Dict[str, Any] = Field(
        ...,
        description="The request: workType, territoryIds, startTime, endTime, accountId...",
        json_schema_extra={"nw_in": "body"},
    )


class GetAppointmentCandidatesInput(_Op):
    """Get the service resources available for appointments."""

    action: Literal["get_appointment_candidates"] = "get_appointment_candidates"
    request: Dict[str, Any] = Field(
        ...,
        description="The request: workType, territoryIds, startTime, endTime, accountId...",
        json_schema_extra={"nw_in": "body"},
    )


class SetSurveyFieldTranslationInput(_Op):
    """Add or change the translation of a survey field."""

    action: Literal["set_survey_field_translation"] = "set_survey_field_translation"
    developer_name: PathPart = Field(...)
    language: PathPart = Field(...)
    value: str = Field(..., description="The translated value")


class SetSurveyFieldTranslationsInput(_Op):
    """Add or change the translations of several survey fields."""

    action: Literal["set_survey_field_translations"] = "set_survey_field_translations"
    request: Dict[str, Any] = Field(
        ...,
        description="The request body: the values to upsert, by developer name and language",
        json_schema_extra={"nw_in": "body"},
    )


class DeleteSurveyFieldTranslationInput(_Op):
    """Delete the translation of a survey field."""

    action: Literal["delete_survey_field_translation"] = "delete_survey_field_translation"
    developer_name: PathPart = Field(...)
    language: PathPart = Field(...)


class DeleteSurveyFieldTranslationsInput(_Op):
    """Delete the translations of several survey fields."""

    action: Literal["delete_survey_field_translations"] = "delete_survey_field_translations"
    request: Dict[str, Any] = Field(
        ...,
        description="The request body: the values to delete, by developer name and language",
        json_schema_extra={"nw_in": "body"},
    )


class GetSurveyFieldTranslationInput(_Op):
    """Get the translation of a survey field."""

    action: Literal["get_survey_field_translation"] = "get_survey_field_translation"
    developer_name: PathPart = Field(...)
    language: PathPart = Field(...)


class GetSurveyFieldTranslationsInput(_Op):
    """Get the translations of several survey fields."""

    action: Literal["get_survey_field_translations"] = "get_survey_field_translations"
    request: Dict[str, Any] = Field(
        ...,
        description="The request body: the values to get, by developer name and language",
        json_schema_extra={"nw_in": "body"},
    )


class GetUserPasswordStatusInput(_Op):
    """Get whether the password of a user is expired."""

    action: Literal["get_user_password_status"] = "get_user_password_status"
    user_id: SalesforceRecordId = Field(...)


class SetUserPasswordInput(_Op):
    """Set the password of a user."""

    action: Literal["set_user_password"] = "set_user_password"
    user_id: SalesforceRecordId = Field(...)
    new_password: str = Field(
        ..., serialization_alias="NewPassword", description="The new password", repr=False
    )


class ResetUserPasswordInput(_Op):
    """Reset the password of a user. The response holds the new password."""

    action: Literal["reset_user_password"] = "reset_user_password"
    user_id: SalesforceRecordId = Field(...)


class GetSelfServiceUserPasswordStatusInput(_Op):
    """Get whether the password of a self-service user is expired."""

    action: Literal["get_self_service_user_password_status"] = (
        "get_self_service_user_password_status"
    )
    self_service_user_id: SalesforceRecordId = Field(...)


class SetSelfServiceUserPasswordInput(_Op):
    """Set the password of a self-service user."""

    action: Literal["set_self_service_user_password"] = "set_self_service_user_password"
    self_service_user_id: SalesforceRecordId = Field(...)
    new_password: str = Field(
        ..., serialization_alias="NewPassword", description="The new password", repr=False
    )


class ResetSelfServiceUserPasswordInput(_Op):
    """Reset the password of a self-service user. The response holds the new password."""

    action: Literal["reset_self_service_user_password"] = "reset_self_service_user_password"
    self_service_user_id: SalesforceRecordId = Field(...)


class GetLightningToggleMetricsInput(_Op):
    """Get Lightning Experience toggle metrics."""

    action: Literal["get_lightning_toggle_metrics"] = "get_lightning_toggle_metrics"


class GetLightningUsageByAppTypeInput(_Op):
    """Get Lightning usage grouped by app type."""

    action: Literal["get_lightning_usage_by_app_type"] = "get_lightning_usage_by_app_type"


class GetLightningUsageByBrowserInput(_Op):
    """Get Lightning usage grouped by browser."""

    action: Literal["get_lightning_usage_by_browser"] = "get_lightning_usage_by_browser"


class GetLightningUsageByPageInput(_Op):
    """Get Lightning usage grouped by page."""

    action: Literal["get_lightning_usage_by_page"] = "get_lightning_usage_by_page"


class GetLightningUsageByFlexipageInput(_Op):
    """Get Lightning usage grouped by Lightning page."""

    action: Literal["get_lightning_usage_by_flexipage"] = "get_lightning_usage_by_flexipage"


class GetLightningExitByPageInput(_Op):
    """Get the pages users exit Lightning Experience from."""

    action: Literal["get_lightning_exit_by_page"] = "get_lightning_exit_by_page"


class CreateIngestJobInput(_Op):
    """Create a Bulk API 2.0 ingest job (insert, update, upsert, delete)."""

    action: Literal["create_ingest_job"] = "create_ingest_job"
    object: str = Field(..., description="Object the records belong to")
    operation: Literal["insert", "delete", "hardDelete", "update", "upsert"] = Field(...)
    content_type: Optional[Literal["CSV"]] = Field(None, serialization_alias="contentType")
    column_delimiter: Optional[
        Literal["BACKQUOTE", "CARET", "COMMA", "PIPE", "SEMICOLON", "TAB"]
    ] = Field(None, serialization_alias="columnDelimiter")
    line_ending: Optional[Literal["LF", "CRLF"]] = Field(None, serialization_alias="lineEnding")
    external_id_field_name: Optional[str] = Field(
        None, serialization_alias="externalIdFieldName", description="Required for upsert"
    )
    assignment_rule_id: Optional[SalesforceRecordId] = Field(
        None, serialization_alias="assignmentRuleId"
    )


class UploadIngestJobDataInput(_Op):
    """Upload the CSV data of an ingest job."""

    action: Literal["upload_ingest_job_data"] = "upload_ingest_job_data"
    job_id: SalesforceRecordId = Field(...)
    csv_data: str = Field(
        ..., description="The records, as CSV text", json_schema_extra={"nw_in": "raw"}
    )


class CompleteIngestJobInput(_Op):
    """Tell Salesforce the upload is done, so the job starts processing."""

    action: Literal["complete_ingest_job"] = "complete_ingest_job"
    job_id: SalesforceRecordId = Field(...)


class AbortIngestJobInput(_Op):
    """Abort an ingest job."""

    action: Literal["abort_ingest_job"] = "abort_ingest_job"
    job_id: SalesforceRecordId = Field(...)


class DeleteIngestJobInput(_Op):
    """Delete an ingest job and its data."""

    action: Literal["delete_ingest_job"] = "delete_ingest_job"
    job_id: SalesforceRecordId = Field(...)


class GetIngestJobInput(_Op):
    """Get the status of an ingest job."""

    action: Literal["get_ingest_job"] = "get_ingest_job"
    job_id: SalesforceRecordId = Field(...)


class ListIngestJobsInput(_Op):
    """List the ingest jobs."""

    action: Literal["list_ingest_jobs"] = "list_ingest_jobs"
    is_pk_chunking_enabled: Optional[bool] = Field(
        None, serialization_alias="isPkChunkingEnabled", json_schema_extra={"nw_in": "query"}
    )
    job_type: Optional[str] = Field(
        None, serialization_alias="jobType", json_schema_extra={"nw_in": "query"}
    )
    query_locator: Optional[str] = Field(
        None, serialization_alias="queryLocator", json_schema_extra={"nw_in": "query"}
    )


class GetIngestSuccessfulResultsInput(_Op):
    """Get the records an ingest job processed successfully, as CSV."""

    action: Literal["get_ingest_successful_results"] = "get_ingest_successful_results"
    job_id: SalesforceRecordId = Field(...)


class GetIngestFailedResultsInput(_Op):
    """Get the records an ingest job failed to process, as CSV."""

    action: Literal["get_ingest_failed_results"] = "get_ingest_failed_results"
    job_id: SalesforceRecordId = Field(...)


class GetIngestUnprocessedRecordsInput(_Op):
    """Get the records an ingest job did not process, as CSV."""

    action: Literal["get_ingest_unprocessed_records"] = "get_ingest_unprocessed_records"
    job_id: SalesforceRecordId = Field(...)


class CreateQueryJobInput(_Op):
    """Create a Bulk API 2.0 query job."""

    action: Literal["create_query_job"] = "create_query_job"
    operation: Literal["query", "queryAll"] = Field(...)
    query: str = Field(..., description="The SOQL query")
    content_type: Optional[Literal["CSV"]] = Field(None, serialization_alias="contentType")
    column_delimiter: Optional[
        Literal["BACKQUOTE", "CARET", "COMMA", "PIPE", "SEMICOLON", "TAB"]
    ] = Field(None, serialization_alias="columnDelimiter")
    line_ending: Optional[Literal["LF", "CRLF"]] = Field(None, serialization_alias="lineEnding")


class GetQueryJobInput(_Op):
    """Get the status of a query job."""

    action: Literal["get_query_job"] = "get_query_job"
    job_id: SalesforceRecordId = Field(...)


class AbortQueryJobInput(_Op):
    """Abort a query job."""

    action: Literal["abort_query_job"] = "abort_query_job"
    job_id: SalesforceRecordId = Field(...)


class DeleteQueryJobInput(_Op):
    """Delete a query job and its results."""

    action: Literal["delete_query_job"] = "delete_query_job"
    job_id: SalesforceRecordId = Field(...)


class ListQueryJobsInput(_Op):
    """List the query jobs."""

    action: Literal["list_query_jobs"] = "list_query_jobs"
    is_pk_chunking_enabled: Optional[bool] = Field(
        None, serialization_alias="isPkChunkingEnabled", json_schema_extra={"nw_in": "query"}
    )
    job_type: Optional[str] = Field(
        None, serialization_alias="jobType", json_schema_extra={"nw_in": "query"}
    )
    query_locator: Optional[str] = Field(
        None, serialization_alias="queryLocator", json_schema_extra={"nw_in": "query"}
    )


class GetQueryJobResultsInput(_Op):
    """Get the results of a query job, as CSV."""

    action: Literal["get_query_job_results"] = "get_query_job_results"
    job_id: SalesforceRecordId = Field(...)
    locator: Optional[str] = Field(None, json_schema_extra={"nw_in": "query"})
    max_records: Optional[int] = Field(
        None, serialization_alias="maxRecords", json_schema_extra={"nw_in": "query"}
    )


class GetQueryJobResultPagesInput(_Op):
    """Get the result pages of a query job, to download in parallel."""

    action: Literal["get_query_job_result_pages"] = "get_query_job_result_pages"
    job_id: SalesforceRecordId = Field(...)
    max_records: Optional[int] = Field(
        None, serialization_alias="maxRecords", json_schema_extra={"nw_in": "query"}
    )
