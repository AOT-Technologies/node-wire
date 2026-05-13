from __future__ import annotations

from typing import Annotated, Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator


class BaseDriveOperation(BaseModel):
    """Base config to strictly forbid unexpected payload fields."""

    model_config = ConfigDict(extra="forbid")


class FilesCreateOperation(BaseDriveOperation):
    action: Literal["files.create"]
    name: str = Field(..., description="The name of the file.")
    mime_type: Optional[str] = Field(None, description="The MIME type of the file.")
    parents: Optional[list[str]] = Field(None, description="List of parent folder IDs.")


class FilesListOperation(BaseDriveOperation):
    action: Literal["files.list"]
    page_size: Optional[int] = Field(10, ge=1, le=100, description="Do not send null; omit if unsure.")

    @field_validator("page_size", mode="before")
    @classmethod
    def _default_page_size(cls, v: Any) -> int:
        return 10 if v is None else int(v)
    query: Optional[str] = Field(None, description="Search query string.")
    fields: Optional[str] = Field(
        None,
        description=(
            "Optional fields mask for the list response. If omitted, the connector "
            "uses a performant default: nextPageToken, files(id, name, mimeType, webViewLink)."
        ),
    )
    page_token: Optional[str] = Field(
        None,
        description="Token for the next page of results from a previous files.list response.",
    )


class PermissionsCreateOperation(BaseDriveOperation):
    action: Literal["permissions.create"]
    file_id: str
    role: Literal["reader", "commenter", "writer", "owner"]
    email_address: Optional[str] = None
    type: Literal["user", "group", "domain", "anyone"]
    domain: Optional[str] = Field(None, description="G Suite domain when type is domain.")

    @field_validator("email_address", "domain", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def require_fields_for_perm_type(self) -> "PermissionsCreateOperation":
        if self.type in ("user", "group"):
            if not (self.email_address or "").strip():
                raise ValueError("email_address is required for user and group permission types")
        elif self.type == "domain":
            if not (self.domain or "").strip():
                raise ValueError("domain is required for domain permission type")
        return self


class FilesGetOperation(BaseDriveOperation):
    action: Literal["files.get"]
    file_id: str
    fields: Optional[str] = Field(
        None,
        description=("Optional fields mask; if omitted, a safe default is used by the connector."),
    )


class FilesUpdateOperation(BaseDriveOperation):
    action: Literal["files.update"]
    file_id: str
    name: Optional[str] = Field(None, description="New file name.")
    mime_type: Optional[str] = Field(None, description="New MIME type.")
    add_parents: Optional[list[str]] = Field(
        None, description="Parent IDs to add (see Drive API addParents)."
    )
    remove_parents: Optional[list[str]] = Field(
        None, description="Parent IDs to remove (see Drive API removeParents)."
    )


class FilesUploadOperation(BaseDriveOperation):
    action: Literal["files.upload"]
    name: str = Field(..., description="The name of the file.")
    mime_type: str = Field(..., description="The MIME type of the file content.")
    parents: Optional[list[str]] = Field(None, description="List of parent folder IDs.")
    content: Optional[str] = Field(None, description="UTF-8 text content to upload.")
    content_base64: Optional[str] = Field(
        None, description="Base64 encoded binary content to upload."
    )

    @model_validator(mode="after")
    def exactly_one_of_content_or_base64(self) -> "FilesUploadOperation":
        """Match Drive upload semantics: exactly one body source (aligned with action_spec)."""
        has_text = self.content is not None
        has_b64 = self.content_base64 is not None
        if not has_text and not has_b64:
            raise ValueError("Provide exactly one of 'content' or 'content_base64'.")
        if has_text and has_b64:
            raise ValueError("Provide exactly one of 'content' or 'content_base64', not both.")
        return self


class FilesDeleteOperation(BaseDriveOperation):
    action: Literal["files.delete"]
    file_id: str


_GoogleDriveOperationUnion = Annotated[
    Union[
        FilesCreateOperation,
        FilesListOperation,
        PermissionsCreateOperation,
        FilesGetOperation,
        FilesUpdateOperation,
        FilesUploadOperation,
        FilesDeleteOperation,
    ],
    Field(discriminator="action"),
]

# Discriminated union for tests/agents; must stay aligned with GoogleDriveConnector @nw_action set.
GoogleDriveOperationInput = RootModel[_GoogleDriveOperationUnion]


class GoogleDriveOperationOutput(BaseModel):
    raw: Dict[str, Any]
    description: str
