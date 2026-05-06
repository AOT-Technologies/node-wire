from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, ConfigDict
import re

SALESFORCE_ID_REGEX = re.compile(r"^[a-zA-Z0-9]{15,18}$")

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
            raise ValueError("Invalid Salesforce AccountId format (must be 15 or 18 alphanumeric characters)")
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


# Read/Delete Models
class SalesforceResourceInput(BaseModel):
    action: Literal["read_lead", "delete_lead", "read_contact", "delete_contact"]
    record_id: str

    @field_validator("record_id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not SALESFORCE_ID_REGEX.match(v):
            raise ValueError("Invalid Salesforce record_id format (must be 15 or 18 alphanumeric characters)")
        return v

class ReadLeadInput(SalesforceResourceInput):
    action: Literal["read_lead"] = "read_lead"

class DeleteLeadInput(SalesforceResourceInput):
    action: Literal["delete_lead"] = "delete_lead"

class ReadContactInput(SalesforceResourceInput):
    action: Literal["read_contact"] = "read_contact"

class DeleteContactInput(SalesforceResourceInput):
    action: Literal["delete_contact"] = "delete_contact"

# Update Models
class UpdateLeadInput(BaseModel):
    action: Literal["update_lead"] = "update_lead"
    record_id: str
    fields: Dict[str, Any]

    @field_validator("record_id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not SALESFORCE_ID_REGEX.match(v):
            raise ValueError("Invalid Salesforce record_id format (must be 15 or 18 alphanumeric characters)")
        return v

class UpdateContactInput(BaseModel):
    action: Literal["update_contact"] = "update_contact"
    record_id: str
    fields: Dict[str, Any]

    @field_validator("record_id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not SALESFORCE_ID_REGEX.match(v):
            raise ValueError("Invalid Salesforce record_id format (must be 15 or 18 alphanumeric characters)")
        return v

SalesforceInput = Union[
    CreateLeadInput, 
    CreateContactInput, 
    ReadLeadInput, 
    UpdateLeadInput, 
    DeleteLeadInput,
    ReadContactInput,
    UpdateContactInput,
    DeleteContactInput
]
