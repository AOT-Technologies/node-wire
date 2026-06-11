from typing import Any, Dict, List, Literal, Optional, Union
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
