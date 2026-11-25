#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Inputs for the n8n connector.

Field names are the snake_case of the n8n public API parameter names (n8n 2.47.7, API spec 1.1.1);
the camelCase wire name is derived when a request is sent. Fields the API marks read-only are left out.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasGenerator, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class _Base(BaseModel):
    # Simplified: every wire name is the camelCase of its field name, so no per-field alias.
    model_config = ConfigDict(
        extra="forbid", alias_generator=AliasGenerator(serialization_alias=to_camel)
    )


class _Idempotent(_Base):
    idempotency_key: str | None = Field(
        None,
        min_length=1,
        max_length=128,
        pattern=r"^[\x21-\x7E]+$",
        description="Safe retry key for this POST (sent as the Idempotency-Key header).",
        serialization_alias="Idempotency-Key",
        json_schema_extra={"nw_in": "header"},
    )


# ---- Workflows -------------------------------------------------------------
class ListWorkflowsInput(_Base):
    "Retrieve all workflows"

    action: Literal["list_workflows"] = "list_workflows"
    offset: int | None = Field(
        None,
        description="The number of items to skip before starting to collect the result set.",
    )
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    active: bool | None = None
    tags: str | None = None
    name: str | None = None
    project_id: str | None = None
    exclude_pinned_data: bool | None = None
    include_parent_folder: bool | None = None


class CreateWorkflowInput(_Idempotent):
    "Create a workflow"

    action: Literal["create_workflow"] = "create_workflow"
    name: str
    nodes: list[dict[str, Any]] = Field(..., description="Nodes that make up the workflow")
    connections: dict[str, Any] = Field(
        ...,
        description="Connections between nodes, keyed by source node name",
    )
    settings: dict[str, Any]
    node_groups: list[dict[str, Any]] | None = Field(
        None,
        description="Visual groupings of nodes shown as frames on the canvas",
    )
    static_data: Any | None = Field(
        None,
        description="Data the workflow keeps between executions",
    )
    pin_data: dict[str, Any] | None = Field(
        None,
        description="Pinned sample data for nodes, keyed by node name",
    )
    project_id: str | None = Field(
        None,
        description=(
            "Target project to create the workflow in. Defaults to the user's personal project."
        ),
    )
    parent_folder_id: str | None = Field(
        None,
        description=(
            "ID of the folder to place the workflow in. Omit or null to place at the project root."
        ),
    )
    shared: list[dict[str, Any]] | None = Field(
        None,
        description=(
            "Accepted for compatibility and ignored. The owner share is derived from "
            "the target project."
        ),
    )
    description: str | None = Field(None, description="Description of the workflow")


class GetWorkflowInput(_Base):
    "Retrieve a workflow"

    action: Literal["get_workflow"] = "get_workflow"
    workflow_id: str = Field(..., description="The ID of the workflow.")
    exclude_pinned_data: bool | None = None


class UpdateWorkflowInput(_Base):
    "Update a workflow"

    action: Literal["update_workflow"] = "update_workflow"
    workflow_id: str = Field(..., description="The ID of the workflow.")
    publish_if_active: bool | None = Field(None, json_schema_extra={"nw_in": "query"})
    name: str
    nodes: list[dict[str, Any]] = Field(..., description="Nodes that make up the workflow")
    connections: dict[str, Any] = Field(
        ...,
        description="Connections between nodes, keyed by source node name",
    )
    settings: dict[str, Any]
    node_groups: list[dict[str, Any]] | None = Field(
        None,
        description="Visual groupings of nodes shown as frames on the canvas",
    )
    static_data: Any | None = Field(
        None,
        description="Data the workflow keeps between executions",
    )
    pin_data: dict[str, Any] | None = Field(
        None,
        description="Pinned sample data for nodes, keyed by node name",
    )
    parent_folder_id: str | None = Field(
        None,
        description=(
            "ID of the folder to move the workflow into. Pass null to move it to the "
            "project root; omit to leave its current folder unchanged."
        ),
    )
    shared: list[dict[str, Any]] | None = Field(
        None,
        description=(
            "Accepted for compatibility and ignored. The owner share is derived from "
            "the target project."
        ),
    )
    description: str | None = Field(None, description="Description of the workflow")


class DeleteWorkflowInput(_Base):
    "Delete a workflow"

    action: Literal["delete_workflow"] = "delete_workflow"
    workflow_id: str = Field(..., description="The ID of the workflow.")


class ArchiveWorkflowInput(_Base):
    "Archive a workflow"

    action: Literal["archive_workflow"] = "archive_workflow"
    workflow_id: str = Field(..., description="The ID of the workflow.")


class UnarchiveWorkflowInput(_Base):
    "Unarchive a workflow"

    action: Literal["unarchive_workflow"] = "unarchive_workflow"
    workflow_id: str = Field(..., description="The ID of the workflow.")


class TransferWorkflowInput(_Base):
    "Transfer a workflow to another project"

    action: Literal["transfer_workflow"] = "transfer_workflow"
    workflow_id: str = Field(..., description="The ID of the workflow.")
    destination_project_id: str


class PublishWorkflowInput(_Base):
    "Publish a workflow"

    action: Literal["publish_workflow"] = "publish_workflow"
    workflow_id: str = Field(..., description="The ID of the workflow.")
    version_id: str | None = Field(
        None,
        description=(
            "The specific version ID to publish. If not provided, the latest version is used."
        ),
    )
    name: str | None = Field(
        None,
        description="Optional name for the workflow version during publication.",
    )
    description: str | None = Field(
        None,
        description="Optional description for the workflow version during publication.",
    )


class UnpublishWorkflowInput(_Base):
    "Unpublish a workflow"

    action: Literal["unpublish_workflow"] = "unpublish_workflow"
    workflow_id: str = Field(..., description="The ID of the workflow.")


class ListWorkflowHistoryInput(_Base):
    "Retrieve workflow version history"

    action: Literal["list_workflow_history"] = "list_workflow_history"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    workflow_id: str = Field(..., description="The ID of the workflow.")


class GetWorkflowVersionInput(_Base):
    "Retrieve a workflow version"

    action: Literal["get_workflow_version"] = "get_workflow_version"
    workflow_id: str = Field(..., description="The ID of the workflow.")
    workflow_version_id: str = Field(..., description="The ID of the workflow version.")


class ListWorkflowTagsInput(_Base):
    "Get workflow tags"

    action: Literal["list_workflow_tags"] = "list_workflow_tags"
    workflow_id: str = Field(..., description="The ID of the workflow.")


class UpdateWorkflowTagsInput(_Base):
    "Update tags of a workflow"

    action: Literal["update_workflow_tags"] = "update_workflow_tags"
    workflow_id: str = Field(..., description="The ID of the workflow.")
    tags: list[dict[str, str]] = Field(
        ...,
        description='The request body: a list of {"id": ...} objects.',
        json_schema_extra={"nw_in": "body"},
    )


class ListWorkflowDependenciesInput(_Base):
    "List workflow dependencies"

    action: Literal["list_workflow_dependencies"] = "list_workflow_dependencies"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    workflow_id: str = Field(..., description="The ID of the workflow.")


class TriggerWorkflowInput(_Base):
    "Call a workflow through its webhook URL"

    action: Literal["trigger_workflow"] = "trigger_workflow"
    webhook_url: str = Field(
        ..., description="Full webhook URL. It must be on the configured n8n host."
    )
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    payload: Any = Field(None, description="Query parameters for GET, JSON body otherwise.")
    auth_type: Literal["none", "header", "basic"] = "none"
    auth_header_name: str = ""
    auth_header_value: str = Field("", repr=False)
    username: str = ""
    password: str = Field("", repr=False)

    @field_validator("webhook_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("webhook_url must be a valid http(s) URL")
        return value.strip()

    @field_validator("method", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _auth_fields(self) -> "TriggerWorkflowInput":
        if self.auth_type == "header" and not (self.auth_header_name and self.auth_header_value):
            raise ValueError("auth_type 'header' needs auth_header_name and auth_header_value")
        if self.auth_type == "basic" and not self.username:
            raise ValueError("auth_type 'basic' needs username")
        return self


# ---- Executions ------------------------------------------------------------
class ListExecutionsInput(_Base):
    "Retrieve all executions"

    action: Literal["list_executions"] = "list_executions"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    include_data: bool | None = None
    ignore_data_size_limit: bool | None = None
    redact_execution_data: bool | None = None
    status: (
        Literal["canceled", "crashed", "error", "new", "running", "success", "unknown", "waiting"]
        | None
    ) = None
    workflow_id: str | None = None
    project_id: str | None = None
    started_after: str | None = None
    started_before: str | None = None


class GetExecutionInput(_Base):
    "Retrieve an execution"

    action: Literal["get_execution"] = "get_execution"
    execution_id: int = Field(..., description="The ID of the execution.")
    include_data: bool | None = None
    ignore_data_size_limit: bool | None = None
    redact_execution_data: bool | None = None


class DeleteExecutionInput(_Base):
    "Delete an execution"

    action: Literal["delete_execution"] = "delete_execution"
    execution_id: int = Field(..., description="The ID of the execution.")


class ListExecutionTagsInput(_Base):
    "Get execution tags"

    action: Literal["list_execution_tags"] = "list_execution_tags"
    execution_id: int = Field(..., description="The ID of the execution.")


class UpdateExecutionTagsInput(_Base):
    "Update tags of an execution"

    action: Literal["update_execution_tags"] = "update_execution_tags"
    execution_id: int = Field(..., description="The ID of the execution.")
    tags: list[dict[str, str]] = Field(
        ...,
        description='The request body: a list of {"id": ...} objects.',
        json_schema_extra={"nw_in": "body"},
    )


class StopManyExecutionsInput(_Idempotent):
    "Stop multiple executions"

    action: Literal["stop_many_executions"] = "stop_many_executions"
    status: list[Literal["queued", "running", "waiting"]] = Field(
        ...,
        description="Array of execution statuses to stop. Must include at least one status.",
    )
    workflow_id: str | None = Field(
        None,
        description=(
            "Optional workflow ID to filter executions. If not provided, will stop "
            "executions across all accessible workflows. The literal value `all` has "
            "the same effect as omitting it."
        ),
    )
    started_after: str | None = Field(
        None,
        description="Only stop executions that started after this time.",
    )
    started_before: str | None = Field(
        None,
        description="Only stop executions that started before this time.",
    )


class StopExecutionInput(_Idempotent):
    "Stop an execution"

    action: Literal["stop_execution"] = "stop_execution"
    execution_id: int = Field(..., description="The ID of the execution.")


class RetryExecutionInput(_Idempotent):
    "Retry an execution"

    action: Literal["retry_execution"] = "retry_execution"
    execution_id: int = Field(..., description="The ID of the execution.")
    load_workflow: bool | None = Field(
        None,
        description=(
            "Whether to load the currently saved workflow to execute instead of the "
            "one saved at the time of the execution. If set to true, it will retry "
            "with the latest version of the workflow."
        ),
    )


# ---- Tags ------------------------------------------------------------------
class ListTagsInput(_Base):
    "Retrieve all tags"

    action: Literal["list_tags"] = "list_tags"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )


class CreateTagInput(_Idempotent):
    "Create a tag"

    action: Literal["create_tag"] = "create_tag"
    name: str


class GetTagInput(_Base):
    "Retrieves a tag"

    action: Literal["get_tag"] = "get_tag"
    tag_id: str = Field(..., description="The ID of the tag.")


class UpdateTagInput(_Base):
    "Update a tag"

    action: Literal["update_tag"] = "update_tag"
    tag_id: str = Field(..., description="The ID of the tag.")
    name: str


class DeleteTagInput(_Base):
    "Delete a tag"

    action: Literal["delete_tag"] = "delete_tag"
    tag_id: str = Field(..., description="The ID of the tag.")


# ---- Variables -------------------------------------------------------------
class ListVariablesInput(_Base):
    "Retrieve variables"

    action: Literal["list_variables"] = "list_variables"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    project_id: str | None = None
    state: Literal["empty"] | None = None


class CreateVariableInput(_Idempotent):
    "Create a variable"

    action: Literal["create_variable"] = "create_variable"
    key: str
    value: str
    project_id: str | None = None


class UpdateVariableInput(_Base):
    "Update a variable"

    action: Literal["update_variable"] = "update_variable"
    id: str = Field(..., description="The ID of the variable.")
    key: str
    value: str
    project_id: str | None = None


class DeleteVariableInput(_Base):
    "Delete a variable"

    action: Literal["delete_variable"] = "delete_variable"
    id: str = Field(..., description="The ID of the variable.")


# ---- Credentials -----------------------------------------------------------
class ListCredentialsInput(_Base):
    "List credentials"

    action: Literal["list_credentials"] = "list_credentials"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )


class CreateCredentialInput(_Idempotent):
    "Create a credential"

    action: Literal["create_credential"] = "create_credential"
    id: str | None = Field(
        None,
        description=(
            "An unused credential ID of 1–16 letters, digits, underscores, or "
            "hyphens. The supplied ID is preserved exactly. Omit to generate an ID."
        ),
    )
    name: str
    description: str | None = Field(
        None,
        description=(
            "Plain text, up to 512 characters. A blank value is saved as null. "
            "Ignored when credential descriptions are disabled."
        ),
    )
    type: str
    data: dict[str, Any] = Field(
        ...,
        description=(
            "The credential data. Fields also accept n8n expressions, which are "
            'strings that start with "=".'
        ),
    )
    is_resolvable: bool | None = Field(
        None,
        description="Whether this credential has resolvable fields",
    )
    project_id: str | None = Field(
        None,
        description=(
            "Project to create the credential in. Defaults to the user's personal project."
        ),
    )


class GetCredentialInput(_Base):
    "Get credential by ID"

    action: Literal["get_credential"] = "get_credential"
    credential_id: str = Field(..., description="The ID of the credential.")


class UpdateCredentialInput(_Base):
    "Update credential by ID"

    action: Literal["update_credential"] = "update_credential"
    credential_id: str = Field(..., description="The ID of the credential.")
    name: str | None = Field(None, description="The name of the credential")
    description: str | None = Field(
        None,
        description=(
            "Plain text, up to 512 characters. Send null or a blank value to clear "
            "it. Omit it to keep the stored value. Ignored when credential "
            "descriptions are disabled."
        ),
    )
    type: str | None = Field(
        None,
        description="The credential type. If changing type, data must also be provided.",
    )
    data: dict[str, Any] | None = Field(
        None,
        description=(
            "The credential data. Required when changing credential type. Fields also "
            'accept n8n expressions, which are strings that start with "=".'
        ),
    )
    is_global: bool | None = Field(
        None,
        description="Whether this credential is available globally",
    )
    is_resolvable: bool | None = Field(
        None,
        description="Whether this credential has resolvable fields",
    )
    is_partial_data: bool | None = Field(
        None,
        description=(
            "If true, unredacts and merges existing credential data with the provided "
            "data. If false, replaces the entire data object."
        ),
    )


class DeleteCredentialInput(_Base):
    "Delete credential by ID"

    action: Literal["delete_credential"] = "delete_credential"
    credential_id: str = Field(..., description="The ID of the credential.")


class TransferCredentialInput(_Base):
    "Transfer a credential to another project"

    action: Literal["transfer_credential"] = "transfer_credential"
    credential_id: str = Field(..., description="The ID of the credential.")
    destination_project_id: str = Field(
        ...,
        description="The ID of the project to transfer the credential to.",
    )


class TestCredentialInput(_Base):
    "Test credential by ID"

    action: Literal["test_credential"] = "test_credential"
    credential_id: str = Field(..., description="The ID of the credential.")


class GetCredentialTypeInput(_Base):
    "Show credential data schema"

    action: Literal["get_credential_type"] = "get_credential_type"
    credential_type_name: str = Field(
        ...,
        description="The credential type name that you want to get the schema for",
    )


class ListCredentialDependenciesInput(_Base):
    "List credential dependencies"

    action: Literal["list_credential_dependencies"] = "list_credential_dependencies"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    credential_id: str = Field(..., description="The ID of the credential.")


# ---- Folders ---------------------------------------------------------------
class ListFoldersInput(_Base):
    "Retrieve folders"

    action: Literal["list_folders"] = "list_folders"
    project_id: str = Field(..., description="The ID of the project.")
    filter: str | None = None
    select: str | None = None
    sort_by: (
        Literal[
            "name:asc",
            "name:desc",
            "createdAt:asc",
            "createdAt:desc",
            "updatedAt:asc",
            "updatedAt:desc",
        ]
        | None
    ) = None
    skip: str | None = None
    take: str | None = None


class CreateFolderInput(_Idempotent):
    "Create a folder"

    action: Literal["create_folder"] = "create_folder"
    project_id: str = Field(
        ...,
        description=(
            "The ID of the project, or `personal` to create the folder in the calling "
            "user's own personal project."
        ),
    )
    name: str
    parent_folder_id: str | None = None


class GetFolderInput(_Base):
    "Get folder details"

    action: Literal["get_folder"] = "get_folder"
    project_id: str = Field(..., description="The ID of the project.")
    folder_id: str = Field(..., description="The ID of the folder.")


class UpdateFolderInput(_Base):
    "Update a folder"

    action: Literal["update_folder"] = "update_folder"
    project_id: str = Field(..., description="The ID of the project.")
    folder_id: str = Field(..., description="The ID of the folder.")
    name: str | None = None
    parent_folder_id: str | None = Field(
        None,
        description=(
            'ID of the parent folder to move this folder into. Set to "0" to move the '
            "folder to the project root."
        ),
    )


class DeleteFolderInput(_Base):
    "Delete a folder"

    action: Literal["delete_folder"] = "delete_folder"
    project_id: str = Field(..., description="The ID of the project.")
    folder_id: str = Field(..., description="The ID of the folder.")
    transfer_to_folder_id: str | None = None


# ---- Data tables -----------------------------------------------------------
class ListDataTablesInput(_Base):
    "List all data tables"

    action: Literal["list_data_tables"] = "list_data_tables"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    filter: str | None = Field(None, description="JSON string of filter conditions")
    sort_by: (
        Literal[
            "name:asc",
            "name:desc",
            "createdAt:asc",
            "createdAt:desc",
            "updatedAt:asc",
            "updatedAt:desc",
            "size:asc",
            "size:desc",
        ]
        | None
    ) = Field(None, description="Sort format: field:asc or field:desc")


class CreateDataTableInput(_Idempotent):
    "Create a new data table"

    action: Literal["create_data_table"] = "create_data_table"
    name: str = Field(..., description="Name of the data table")
    columns: list[dict[str, Any]] = Field(..., description="Column definitions for the table")
    project_id: str | None = Field(
        None,
        description=(
            "ID of the project to create the table in. When omitted, the table is "
            "created in the user's personal project."
        ),
    )
    file_id: str | None = Field(
        None,
        description=(
            "ID of a CSV file that you uploaded in the n8n editor. If you set it, n8n "
            "fills the new table with the rows from that file. Only a session- "
            "authenticated caller can upload a file, so an API-key caller cannot use "
            "this field."
        ),
    )
    has_headers: bool | None = Field(
        None,
        description=(
            "Set to true if the first row of the CSV file holds the column names. "
            "Applies only when you set `fileId`. The default is true."
        ),
    )


class GetDataTableInput(_Base):
    "Get a data table"

    action: Literal["get_data_table"] = "get_data_table"
    data_table_id: str = Field(..., description="The ID of the data table")


class UpdateDataTableInput(_Base):
    "Update a data table"

    action: Literal["update_data_table"] = "update_data_table"
    data_table_id: str = Field(..., description="The ID of the data table")
    name: str = Field(..., description="New name for the data table")


class DeleteDataTableInput(_Base):
    "Delete a data table"

    action: Literal["delete_data_table"] = "delete_data_table"
    data_table_id: str = Field(..., description="The ID of the data table")


class ListDataTableDependenciesInput(_Base):
    "List data table dependencies"

    action: Literal["list_data_table_dependencies"] = "list_data_table_dependencies"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    data_table_id: str = Field(..., description="The ID of the data table")


class ListDataTableColumnsInput(_Base):
    "List columns of a data table"

    action: Literal["list_data_table_columns"] = "list_data_table_columns"
    data_table_id: str = Field(..., description="The ID of the data table")


class CreateDataTableColumnInput(_Idempotent):
    "Add a column to a data table"

    action: Literal["create_data_table_column"] = "create_data_table_column"
    data_table_id: str = Field(..., description="The ID of the data table")
    name: str = Field(
        ...,
        description=(
            "Column name. Must start with a letter; only letters, digits, and "
            "underscores after that; maximum 63 characters."
        ),
    )
    type: Literal["string", "number", "boolean", "date"] = Field(
        ..., description="Column data type"
    )
    index: int | None = Field(
        None,
        description="Column position (optional, appended to end if omitted)",
    )


class UpdateDataTableColumnInput(_Base):
    "Update a column"

    action: Literal["update_data_table_column"] = "update_data_table_column"
    data_table_id: str = Field(..., description="The ID of the data table")
    column_id: str = Field(..., description="The ID of the column")
    name: str | None = Field(
        None,
        description=(
            "Column name. Must start with a letter; only letters, digits, and "
            "underscores after that; maximum 63 characters."
        ),
    )
    index: int | None = Field(None, description="New zero-based position for the column")


class DeleteDataTableColumnInput(_Base):
    "Delete a column"

    action: Literal["delete_data_table_column"] = "delete_data_table_column"
    data_table_id: str = Field(..., description="The ID of the data table")
    column_id: str = Field(..., description="The ID of the column")


class ListDataTableRowsInput(_Base):
    "List rows of a data table"

    action: Literal["list_data_table_rows"] = "list_data_table_rows"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    data_table_id: str = Field(..., description="The ID of the data table")
    filter: str | None = Field(None, description="JSON string of filter conditions")
    sort_by: str | None = Field(
        None,
        description="Sort format: columnName:asc or columnName:desc",
    )
    search: str | None = Field(None, description="Search text across all string columns")


class CreateDataTableRowsInput(_Idempotent):
    "Insert rows into a data table"

    action: Literal["create_data_table_rows"] = "create_data_table_rows"
    data_table_id: str = Field(..., description="The ID of the data table")
    data: list[dict[str, Any]] = Field(
        ...,
        description=("Array of rows to insert. Each row is an object with column names as keys."),
    )
    return_type: Literal["all", "count", "id"] | None = Field(
        None,
        description=(
            "count: return only the number of rows inserted. id: return an array of "
            "objects with the id of each inserted row. all: return the full row data "
            "for all inserted rows."
        ),
    )


class UpsertDataTableRowInput(_Idempotent):
    "Upsert a row in a data table"

    action: Literal["upsert_data_table_row"] = "upsert_data_table_row"
    data_table_id: str = Field(..., description="The ID of the data table")
    filter: dict[str, Any] = Field(
        ...,
        description=(
            "Filter conditions to match an existing row. If no row matches, n8n inserts a new row."
        ),
    )
    data: dict[str, Any] = Field(..., description="Column values for the row")
    return_data: bool | None = Field(
        None,
        description=(
            "Set to true to return the upserted row(s): the matching rows if updated, "
            "or the new row if one was inserted. Set to false to return true on "
            "success."
        ),
    )
    dry_run: bool | None = Field(
        None,
        description="Set to true to preview the change without saving it.",
    )


class UpdateDataTableRowsInput(_Base):
    "Update rows in a data table"

    action: Literal["update_data_table_rows"] = "update_data_table_rows"
    data_table_id: str = Field(..., description="The ID of the data table")
    filter: dict[str, Any] = Field(
        ...,
        description="Filter conditions to match rows for update.",
    )
    data: dict[str, Any] = Field(..., description="Column values to update")
    return_data: bool | None = Field(
        None,
        description=(
            "Set to true to return the updated row(s). Set to false to return true on success."
        ),
    )
    dry_run: bool | None = Field(
        None,
        description="Set to true to preview the change without saving it.",
    )


class ClearDataTableRowsInput(_Base):
    "Clear all rows from a data table"

    action: Literal["clear_data_table_rows"] = "clear_data_table_rows"
    data_table_id: str = Field(..., description="The ID of the data table")


class DeleteDataTableRowsInput(_Base):
    "Delete rows from a data table"

    action: Literal["delete_data_table_rows"] = "delete_data_table_rows"
    data_table_id: str = Field(..., description="The ID of the data table")
    filter: str = Field(
        ...,
        description=(
            "JSON string of filter conditions. Required to prevent accidental deletion of all data."
        ),
    )
    return_data: bool | None = Field(
        None,
        description="If true, return the deleted rows. If false, return true on success.",
    )
    dry_run: bool | None = Field(
        None,
        description=(
            "If true, preview which rows would be deleted without actually deleting them."
        ),
    )


# ---- Evaluations -----------------------------------------------------------
class ListTestRunsInput(_Base):
    "Retrieve test runs"

    action: Literal["list_test_runs"] = "list_test_runs"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    workflow_id: str = Field(..., description="The ID of the workflow.")
    status: Literal["new", "running", "completed", "error", "cancelled"] | None = None


class CreateTestRunInput(_Idempotent):
    "Trigger a test run"

    action: Literal["create_test_run"] = "create_test_run"
    workflow_id: str = Field(..., description="The ID of the workflow.")


class GetTestRunInput(_Base):
    "Retrieve a test run"

    action: Literal["get_test_run"] = "get_test_run"
    workflow_id: str = Field(..., description="The ID of the workflow.")
    run_id: str = Field(..., description="The ID of the test run.")


class ListTestCasesInput(_Base):
    "Retrieve test run cases"

    action: Literal["list_test_cases"] = "list_test_cases"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    workflow_id: str = Field(..., description="The ID of the workflow.")
    run_id: str = Field(..., description="The ID of the test run.")


class CancelTestRunInput(_Idempotent):
    "Cancel a test run"

    action: Literal["cancel_test_run"] = "cancel_test_run"
    workflow_id: str = Field(..., description="The ID of the workflow.")
    run_id: str = Field(..., description="The ID of the test run.")


# ---- Projects --------------------------------------------------------------
class ListProjectsInput(_Base):
    "Retrieve projects"

    action: Literal["list_projects"] = "list_projects"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )


class CreateProjectInput(_Idempotent):
    "Create a project"

    action: Literal["create_project"] = "create_project"
    name: str
    id: str | None = Field(
        None,
        description=(
            "Project ID. The server generates an ID when omitted. An ID that is in "
            "use returns HTTP 409."
        ),
    )
    icon: dict[str, Any] | None = None
    description: str | None = None
    custom_telemetry_tags: list[dict[str, Any]] | None = None


class UpdateProjectInput(_Base):
    "Update a project"

    action: Literal["update_project"] = "update_project"
    project_id: str = Field(..., description="The ID of the project.")
    name: str


class DeleteProjectInput(_Base):
    "Delete a project"

    action: Literal["delete_project"] = "delete_project"
    project_id: str = Field(..., description="The ID of the project.")


class ListProjectUsersInput(_Base):
    "List project members"

    action: Literal["list_project_users"] = "list_project_users"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    project_id: str = Field(..., description="The ID of the project.")


class AddUsersToProjectInput(_Idempotent):
    "Add one or more users to a project"

    action: Literal["add_users_to_project"] = "add_users_to_project"
    project_id: str = Field(..., description="The ID of the project.")
    relations: list[dict[str, Any]] = Field(
        ...,
        description="A list of userIds and roles to add to the project.",
    )


class ChangeUserRoleInProjectInput(_Base):
    "Change a user's role in a project"

    action: Literal["change_user_role_in_project"] = "change_user_role_in_project"
    project_id: str = Field(..., description="The ID of the project.")
    user_id: str = Field(..., description="The ID of the user.")
    role: str = Field(
        ...,
        description="The role assigned to the user in the project.",
    )


class DeleteUserFromProjectInput(_Base):
    "Delete a user from a project"

    action: Literal["delete_user_from_project"] = "delete_user_from_project"
    project_id: str = Field(..., description="The ID of the project.")
    user_id: str = Field(..., description="The ID of the user.")


# ---- Roles -----------------------------------------------------------------
class ListRolesInput(_Base):
    "Retrieve all roles"

    action: Literal["list_roles"] = "list_roles"
    with_usage_count: bool | None = None


class CreateRoleInput(_Idempotent):
    "Create a custom role"

    action: Literal["create_role"] = "create_role"
    display_name: str
    description: str | None = None
    role_type: Literal["project", "global"]
    scopes: list[str]


class GetRoleInput(_Base):
    "Retrieve a role"

    action: Literal["get_role"] = "get_role"
    role_slug: str = Field(..., description="The slug of the role.")
    with_usage_count: bool | None = None


class UpdateRoleInput(_Base):
    "Update a custom role"

    action: Literal["update_role"] = "update_role"
    role_slug: str = Field(..., description="The slug of the role.")
    display_name: str
    description: str
    scopes: list[str]


class DeleteRoleInput(_Base):
    "Delete a custom role"

    action: Literal["delete_role"] = "delete_role"
    role_slug: str = Field(..., description="The slug of the role.")
    reassign_role_slug: Any | None = None


# ---- Role mapping rules ----------------------------------------------------
class ListRoleMappingRulesInput(_Base):
    "Retrieve role-mapping rules"

    action: Literal["list_role_mapping_rules"] = "list_role_mapping_rules"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    type: Literal["instance", "project"] | None = None


class CreateRoleMappingRuleInput(_Idempotent):
    "Create a role-mapping rule"

    action: Literal["create_role_mapping_rule"] = "create_role_mapping_rule"
    expression: str
    role: str
    type: Literal["instance", "project"]
    order: int | None = None
    project_ids: list[str] | None = None


class MoveRoleMappingRuleInput(_Idempotent):
    "Move a role-mapping rule"

    action: Literal["move_role_mapping_rule"] = "move_role_mapping_rule"
    role_mapping_rule_id: str = Field(..., description="The ID of the role mapping rule.")
    target_index: int


class UpdateRoleMappingRuleInput(_Base):
    "Update a role-mapping rule"

    action: Literal["update_role_mapping_rule"] = "update_role_mapping_rule"
    role_mapping_rule_id: str = Field(..., description="The ID of the role mapping rule.")
    expression: str | None = None
    role: str | None = None
    project_ids: list[str] | None = None


class DeleteRoleMappingRuleInput(_Base):
    "Delete a role-mapping rule"

    action: Literal["delete_role_mapping_rule"] = "delete_role_mapping_rule"
    role_mapping_rule_id: str = Field(..., description="The ID of the role mapping rule.")


# ---- Users -----------------------------------------------------------------
class ListUsersInput(_Base):
    "Retrieve all users"

    action: Literal["list_users"] = "list_users"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    include_role: bool | None = None
    project_id: str | None = None


class CreateUserInput(_Idempotent):
    "Create multiple users"

    action: Literal["create_user"] = "create_user"
    users: list[dict[str, Any]] = Field(
        ...,
        description="The users to create: a list of objects as the API defines them.",
        json_schema_extra={"nw_in": "body"},
    )


class GetUserInput(_Base):
    "Get user by ID/Email"

    action: Literal["get_user"] = "get_user"
    user_id: str = Field(..., description="The ID or email of the user.")
    include_role: bool | None = None


class DeleteUserInput(_Base):
    "Delete user"

    action: Literal["delete_user"] = "delete_user"
    user_id: str = Field(..., description="The ID of the user.")
    transfer_id: str | None = None


class ChangeRoleInput(_Base):
    "Change a user's global role"

    action: Literal["change_role"] = "change_role"
    user_id: str = Field(..., description="The ID of the user.")
    new_role_name: Any


# ---- Audit -----------------------------------------------------------------
class GenerateAuditInput(_Idempotent):
    "Generate an audit"

    action: Literal["generate_audit"] = "generate_audit"
    additional_options: dict[str, Any] | None = None


# ---- Discover --------------------------------------------------------------
class GetDiscoverInput(_Base):
    "Discover available API capabilities"

    action: Literal["get_discover"] = "get_discover"
    include: Literal["schemas"] | None = None
    resource: str | None = None
    operation: str | None = None


# ---- Insights --------------------------------------------------------------
class GetInsightsSummaryInput(_Base):
    "Retrieve insights summary"

    action: Literal["get_insights_summary"] = "get_insights_summary"
    start_date: str | None = None
    end_date: str | None = None
    project_id: str | None = None


# ---- Community packages ----------------------------------------------------
class ListCommunityPackagesInput(_Base):
    "List installed community packages"

    action: Literal["list_community_packages"] = "list_community_packages"


class InstallCommunityPackageInput(_Idempotent):
    "Install a community package"

    action: Literal["install_community_package"] = "install_community_package"
    name: str = Field(
        ...,
        description=(
            "npm package name. Unscoped names start with n8n-nodes-. Scoped names use "
            "the @[author]/n8n-nodes- form."
        ),
    )
    version: str | None = Field(
        None,
        description=(
            "Semver version or npm dist-tag, such as latest or beta. When unverified "
            "packages are disabled, only versions verified by n8n can be installed."
        ),
    )


class UpdateCommunityPackageInput(_Base):
    "Update a community package"

    action: Literal["update_community_package"] = "update_community_package"
    name: str = Field(..., description="npm package name")
    version: str | None = Field(
        None,
        description=(
            "Semver version or npm dist-tag to update to, such as latest or beta. "
            "When unverified packages are disabled, only versions verified by n8n can "
            "be installed."
        ),
    )


class UninstallCommunityPackageInput(_Base):
    "Uninstall a community package"

    action: Literal["uninstall_community_package"] = "uninstall_community_package"
    name: str = Field(..., description="npm package name")


# ---- Source control --------------------------------------------------------
class GetSourceControlStatusInput(_Base):
    "Preview pending source control changes"

    action: Literal["get_source_control_status"] = "get_source_control_status"
    direction: Literal["push", "pull"]


class PushSourceControlInput(_Idempotent):
    "Push local source control changes"

    action: Literal["push_source_control"] = "push_source_control"
    commit_message: str
    file_names: list[dict[str, Any]]
    force: bool | None = None


class PullSourceControlInput(_Idempotent):
    "Pull changes from the remote repository"

    action: Literal["pull_source_control"] = "pull_source_control"
    force: bool | None = Field(
        None,
        description="Discard local changes and force the pull to complete.",
    )
    auto_publish: Literal["none", "all", "published"] | None = Field(
        None,
        description=(
            "Controls automatic workflow publishing after import: - `none`: Keep "
            "workflows in their local published state (default) - `all`: Publish all "
            "imported workflows - `published`: Publish only workflows that were "
            "published locally before import"
        ),
    )


# ---- LDAP ------------------------------------------------------------------
class GetLdapConfigurationInput(_Base):
    "Retrieve the LDAP configuration"

    action: Literal["get_ldap_configuration"] = "get_ldap_configuration"


class UpdateLdapConfigurationInput(_Base):
    "Set the LDAP configuration"

    action: Literal["update_ldap_configuration"] = "update_ldap_configuration"
    login_enabled: bool = Field(
        ...,
        description=(
            "Whether LDAP login is enabled. Setting this to false is destructive — it "
            "deletes all stored LDAP user identities and disables synchronization."
        ),
    )
    login_label: str = Field(..., description="Label shown on the LDAP login button.")
    connection_url: str = Field(..., description="LDAP server URL.")
    allow_unauthorized_certs: bool = Field(
        ...,
        description="Whether to allow unauthorized (self-signed) certificates.",
    )
    connection_security: Literal["none", "tls", "startTls"] = Field(
        ...,
        description="TLS/SSL security mode for the LDAP connection.",
    )
    connection_port: int = Field(..., description="LDAP server port.")
    base_dn: str = Field(..., description="Base DN for LDAP search queries.")
    binding_admin_dn: str = Field(..., description="DN of the LDAP admin user for binding.")
    binding_admin_password: str = Field(
        ...,
        description=(
            "Password for the LDAP admin user. To keep an existing password "
            "unchanged, submit the blanking placeholder from a prior GET response. "
            "Use an empty string to clear the password."
        ),
    )
    first_name_attribute: str = Field(
        ...,
        description="LDAP attribute mapped to the user's first name.",
    )
    last_name_attribute: str = Field(
        ...,
        description="LDAP attribute mapped to the user's last name.",
    )
    email_attribute: str = Field(..., description="LDAP attribute mapped to the user's email.")
    login_id_attribute: str = Field(
        ...,
        description="LDAP attribute used for login (usually the same as emailAttribute).",
    )
    ldap_id_attribute: str = Field(
        ...,
        description="LDAP attribute that uniquely identifies a user.",
    )
    user_filter: str = Field(
        ...,
        description=(
            "Additional LDAP filter to apply when searching for users. Use an empty "
            "string for no additional filter."
        ),
    )
    synchronization_enabled: bool = Field(
        ...,
        description="Whether automatic LDAP synchronization is enabled.",
    )
    synchronization_interval: int = Field(
        ...,
        description=(
            "Interval in minutes between automatic synchronizations. Ignored if "
            "synchronizationEnabled is false."
        ),
    )
    search_page_size: int = Field(
        ...,
        description="Number of LDAP entries to fetch per search page.",
    )
    search_timeout: int = Field(..., description="LDAP search timeout in seconds.")
    enforce_email_uniqueness: bool = Field(
        ...,
        description=(
            "Whether to enforce that email addresses are unique across LDAP users. "
            "When true, if two users have the same email, only the first will be "
            "imported."
        ),
    )
    link_existing_accounts_by_email: bool | None = Field(
        None,
        description=(
            "Whether an LDAP user may be linked to an existing n8n account that has "
            "the same email. When false, login and synchronization skip such users. "
            "Optional on PUT; the stored value is kept when omitted."
        ),
    )


class GetLdapSyncInput(_Base):
    "Retrieve LDAP synchronization history"

    action: Literal["get_ldap_sync"] = "get_ldap_sync"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )


class RunLdapSyncInput(_Idempotent):
    "Trigger an LDAP synchronization"

    action: Literal["run_ldap_sync"] = "run_ldap_sync"
    type: Literal["live", "dry"] = Field(
        ...,
        description=(
            "Type of synchronization. 'live' applies changes to the database. 'dry' "
            "performs a test run and does not persist changes."
        ),
    )


# ---- SAML ------------------------------------------------------------------
class GetSamlConfigurationInput(_Base):
    "Retrieve the SAML SSO configuration"

    action: Literal["get_saml_configuration"] = "get_saml_configuration"


class UpdateSamlConfigurationInput(_Base):
    "Set the SAML SSO configuration"

    action: Literal["update_saml_configuration"] = "update_saml_configuration"
    mapping: dict[str, Any] = Field(
        ...,
        description=(
            "Mapping of SAML attributes to n8n user fields. Use empty strings / empty "
            "arrays for unused attributes."
        ),
    )
    metadata: str = Field(
        ...,
        description=(
            "Identity provider metadata in XML format. Use an empty string to clear "
            "stored metadata (also clears metadataUrl when no URL is provided). Use "
            "the redaction placeholder from a prior GET to leave an existing value "
            "unchanged."
        ),
    )
    metadata_url: str = Field(
        ...,
        description=(
            "URL to fetch identity provider metadata from. Use an empty string to "
            "clear a stored URL."
        ),
    )
    ignore_ssl: bool = Field(
        ...,
        serialization_alias="ignoreSSL",
        description=("Whether to ignore SSL certificate errors when fetching metadata from a URL."),
    )
    login_binding: Literal["redirect", "post"] = Field(
        ..., description="SAML login request binding."
    )
    login_enabled: bool = Field(..., description="Whether SAML login is enabled.")
    login_label: str = Field(..., description="Label shown on the SAML login button.")
    authn_requests_signed: bool = Field(
        ...,
        description="Whether authentication requests are signed.",
    )
    want_assertions_signed: bool = Field(..., description="Whether signed assertions are required.")
    want_message_signed: bool = Field(..., description="Whether signed SAML messages are required.")
    email_verified_required: bool = Field(
        ...,
        description=(
            "Whether the identity provider must assert that the user's email address "
            "is verified before a login is linked to an existing user by email. When "
            "disabled, only an explicit negative assertion is rejected."
        ),
    )
    signing_private_key: str = Field(
        ...,
        description=(
            "PEM-encoded private key for signing SAML AuthnRequests. Use an empty "
            "string to clear an existing key, or the redaction placeholder from a "
            "prior GET to leave it unchanged."
        ),
    )
    signing_certificate: str = Field(
        ...,
        description=(
            "PEM-encoded certificate containing the public key matching the signing "
            "private key. Use an empty string when unused or to clear an existing "
            "certificate."
        ),
    )
    acs_binding: Literal["redirect", "post"] = Field(
        ..., description="Assertion Consumer Service binding."
    )
    signature_config: dict[str, Any] = Field(
        ...,
        description="Configuration for the signature in SAML requests and responses.",
    )
    relay_state: str = Field(
        ...,
        description=(
            "Default relay state value for SAML requests. Use an empty string when unused."
        ),
    )
    entity_id: str | None = Field(
        None,
        serialization_alias="entityID",
        description=(
            "Service provider entity ID. Returned by GET for convenience; ignored on "
            "write so a GET response can be sent back as a PUT body."
        ),
    )
    return_url: str | None = Field(
        None,
        description=(
            "Assertion Consumer Service return URL. Returned by GET for convenience; "
            "ignored on write so a GET response can be sent back as a PUT body."
        ),
    )


# ---- OIDC ------------------------------------------------------------------
class GetOidcConfigurationInput(_Base):
    "Retrieve the OIDC SSO configuration"

    action: Literal["get_oidc_configuration"] = "get_oidc_configuration"


class SetOidcConfigurationInput(_Base):
    "Set the OIDC SSO configuration"

    action: Literal["set_oidc_configuration"] = "set_oidc_configuration"
    client_id: str = Field(
        ...,
        description="The client ID issued when registering n8n with the OIDC provider.",
    )
    client_secret: str = Field(
        ...,
        description=(
            "The client secret issued when registering n8n with the OIDC provider. "
            "Submit the redacted sentinel value returned on read to keep the stored "
            "secret unchanged."
        ),
    )
    discovery_endpoint: str = Field(
        ...,
        description="The OIDC provider's well-known discovery endpoint.",
    )
    login_enabled: bool = Field(..., description="Whether OIDC single sign-on is enabled.")
    prompt: Literal["none", "login", "consent", "select_account", "create"] = Field(
        ...,
        description="The prompt parameter to use when authenticating.",
    )
    authentication_context_class_reference: list[str] = Field(
        ...,
        description=(
            "ACR values to include in the authorization request (acr_values "
            "parameter), in order of preference. Use an empty array when unused."
        ),
    )
    additional_scopes: str = Field(
        ...,
        description=(
            "Additional scopes to request, space separated. n8n always requests "
            "`openid`, `profile` and `email`. Use an empty string when unused."
        ),
    )
    email_verified_required: bool = Field(
        ...,
        description=(
            "Whether the identity provider must assert that the user's email address "
            "is verified before the login is accepted. When disabled, only an "
            "explicit negative assertion is rejected."
        ),
    )
    rp_initiated_logout_enabled: bool = Field(
        ...,
        description=(
            "Whether signing out of n8n also ends the session at the OIDC provider "
            "via RP-Initiated Logout. When disabled, sign-out is local to n8n only."
        ),
    )


# ---- OTel ------------------------------------------------------------------
class GetOtelSettingsInput(_Base):
    "Retrieve the OpenTelemetry configuration"

    action: Literal["get_otel_settings"] = "get_otel_settings"


class UpdateOtelSettingsInput(_Base):
    "Set the OpenTelemetry configuration"

    action: Literal["update_otel_settings"] = "update_otel_settings"
    enabled: bool = Field(..., description="Whether OpenTelemetry tracing is enabled.")
    exporter_protocol: Literal["http/protobuf", "grpc"] | None = Field(
        None,
        description=(
            "The wire protocol used to export spans. Collectors conventionally serve "
            "OTLP/HTTP on port 4318 and OTLP/gRPC on port 4317. The endpoint scheme "
            "(`http://` or `https://`) controls TLS for both protocols. Optional: a "
            "write that omits it sel..."
        ),
    )
    exporter_endpoint: str = Field(
        ...,
        description=(
            "The base URL of the OTLP collector to export traces to. The value must "
            "be an `http://` or `https://` URL, because the scheme selects TLS — for "
            "gRPC, `https://` uses SSL and `http://` is plaintext."
        ),
    )
    exporter_tracing_path: str = Field(
        ...,
        description=(
            "The path appended to the endpoint for the OTLP traces signal. Ignored "
            "when `exporterProtocol` is `grpc`, which takes no URL path."
        ),
    )
    exporter_service_name: str = Field(
        ...,
        description="The `service.name` resource attribute reported on every span.",
    )
    exporter_headers: str = Field(
        ...,
        description=(
            "Additional headers sent to the OTLP collector, as a single string of "
            "comma-separated `key=value` pairs (e.g. `authorization=Bearer my- "
            "token,x-tenant-id=acme`). Whitespace around each key and value is "
            "trimmed; a value may contain spaces b..."
        ),
    )
    traces_sample_rate: int | float = Field(
        ...,
        description="The ratio of traces to sample, between 0 (none) and 1 (all).",
    )
    startup_connectivity_timeout_ms: int = Field(
        ...,
        description=(
            "How long, in milliseconds, to wait when checking the collector is "
            "reachable. Also used as the timeout for the test-trace endpoint."
        ),
    )
    include_node_spans: bool = Field(
        ...,
        description=(
            "Whether to emit a span for each node execution in addition to the workflow span."
        ),
    )
    emit_workflow_start_span: bool | None = Field(
        None,
        description=(
            "Whether to emit a `workflow.execute.started` span with a duration of 0 "
            "ms when each execution or resumed segment starts. Optional: a write that "
            "omits it selects `false`, but an instance that sets this field to `true` "
            "with an environment..."
        ),
    )
    emit_node_start_span: bool | None = Field(
        None,
        description=(
            "Whether to emit a `node.execute.started` span with a duration of 0 ms "
            "when each node span starts. This doubles the number of node spans. Has "
            "no effect when `includeNodeSpans` is false. Optional: a write that omits "
            "it selects `false`, but..."
        ),
    )
    inject_outbound: bool = Field(
        ...,
        description=(
            "Whether to inject trace context headers into outbound HTTP requests made by nodes."
        ),
    )
    production_executions_only: bool = Field(
        ...,
        description=(
            "When true, only production executions of published (active) workflows "
            "are traced, not manual/test runs."
        ),
    )


class TestOtelTraceInput(_Base):
    "Test the connection to an OTLP collector"

    action: Literal["test_otel_trace"] = "test_otel_trace"
    exporter_protocol: Literal["http/protobuf", "grpc"] | None = Field(
        None,
        description=(
            "The wire protocol used to send the test span. Collectors conventionally "
            "serve OTLP/HTTP on port 4318 and OTLP/gRPC on port 4317. The endpoint "
            "scheme (`http://` or `https://`) controls TLS for both protocols. "
            "Optional: omitting it selects..."
        ),
    )
    exporter_endpoint: str = Field(
        ...,
        description=(
            "The base URL of the OTLP collector to export traces to. The value must "
            "be an `http://` or `https://` URL, because the scheme selects TLS — for "
            "gRPC, `https://` uses SSL and `http://` is plaintext."
        ),
    )
    exporter_tracing_path: str = Field(
        ...,
        description=(
            "The path appended to the endpoint for the OTLP traces signal. Ignored "
            "when `exporterProtocol` is `grpc`, which takes no URL path."
        ),
    )
    exporter_service_name: str = Field(
        ...,
        description="The `service.name` resource attribute reported on the test span.",
    )
    exporter_headers: str = Field(
        ...,
        description=(
            "Additional headers sent to the OTLP collector, as a single string of "
            "comma-separated `key=value` pairs (e.g. `authorization=Bearer my- "
            "token,x-tenant-id=acme`). Whitespace around each key and value is "
            "trimmed; a value may contain spaces b..."
        ),
    )
    startup_connectivity_timeout_ms: int = Field(
        ...,
        description="How long, in milliseconds, to wait for the collector to respond.",
    )


# ---- Rate limits -----------------------------------------------------------
class GetRateLimitSettingsInput(_Base):
    "Retrieve the Public API rate-limit settings"

    action: Literal["get_rate_limit_settings"] = "get_rate_limit_settings"


class UpdateRateLimitSettingsInput(_Base):
    "Set the Public API rate-limit settings"

    action: Literal["update_rate_limit_settings"] = "update_rate_limit_settings"
    enabled: bool = Field(
        ...,
        description="Whether Public API rate limiting is enabled.",
    )
    global_read_limit: int = Field(
        ...,
        description=(
            "Read requests allowed for one API key across all endpoints. Set 0 for no limit."
        ),
    )
    global_read_window_seconds: int = Field(
        ...,
        description="The window for the global read limit, in seconds.",
    )
    global_write_limit: int = Field(
        ...,
        description=(
            "Write requests allowed for one API key across all endpoints. Set 0 for no limit."
        ),
    )
    global_write_window_seconds: int = Field(
        ...,
        description="The window for the global write limit, in seconds.",
    )
    endpoint_read_limit: int = Field(
        ...,
        description=("Read requests allowed for one API key on one endpoint. Set 0 for no limit."),
    )
    endpoint_read_window_seconds: int = Field(
        ...,
        description="The window for the endpoint read limit, in seconds.",
    )
    endpoint_write_limit: int = Field(
        ...,
        description=("Write requests allowed for one API key on one endpoint. Set 0 for no limit."),
    )
    endpoint_write_window_seconds: int = Field(
        ...,
        description="The window for the endpoint write limit, in seconds.",
    )


# ---- Security policy -------------------------------------------------------
class GetSecurityPolicyInput(_Base):
    "Retrieve the security policy"

    action: Literal["get_security_policy"] = "get_security_policy"


class UpdateSecurityPolicyInput(_Base):
    "Set the security policy"

    action: Literal["update_security_policy"] = "update_security_policy"
    personal_space_publishing: bool = Field(
        ...,
        description=("Whether members may publish workflows and agents from their personal space."),
    )
    personal_space_sharing: bool = Field(
        ...,
        description=(
            "Whether members may share workflows and credentials from their personal space."
        ),
    )
    redaction_enforcement: dict[str, Any]
    published_personal_workflows_count: int | None = Field(
        None,
        description=(
            "Read-only usage count returned by GET. Ignored on write so a GET "
            "response can be sent back as a PUT body."
        ),
    )
    shared_personal_workflows_count: int | None = Field(
        None,
        description=(
            "Read-only usage count returned by GET. Ignored on write so a GET "
            "response can be sent back as a PUT body."
        ),
    )
    shared_personal_credentials_count: int | None = Field(
        None,
        description=(
            "Read-only usage count returned by GET. Ignored on write so a GET "
            "response can be sent back as a PUT body."
        ),
    )


# ---- Log streaming ---------------------------------------------------------
class ListLogStreamingEventTypesInput(_Base):
    "List streamable event types"

    action: Literal["list_log_streaming_event_types"] = "list_log_streaming_event_types"


class ListLogStreamingDestinationsInput(_Base):
    "List log streaming destinations"

    action: Literal["list_log_streaming_destinations"] = "list_log_streaming_destinations"


class CreateLogStreamingDestinationInput(_Idempotent):
    "Create a log streaming destination"

    action: Literal["create_log_streaming_destination"] = "create_log_streaming_destination"
    destination: dict[str, Any] = Field(
        ...,
        description="The destination definition (its shape depends on its type).",
        json_schema_extra={"nw_in": "body"},
    )


class GetLogStreamingDestinationInput(_Base):
    "Retrieve a log streaming destination"

    action: Literal["get_log_streaming_destination"] = "get_log_streaming_destination"
    destination_id: str = Field(..., description="The id of the log streaming destination.")


class UpdateLogStreamingDestinationInput(_Base):
    "Update a log streaming destination"

    action: Literal["update_log_streaming_destination"] = "update_log_streaming_destination"
    destination_id: str = Field(..., description="The id of the log streaming destination.")
    destination: dict[str, Any] = Field(
        ...,
        description="The destination definition (its shape depends on its type).",
        json_schema_extra={"nw_in": "body"},
    )


class DeleteLogStreamingDestinationInput(_Base):
    "Delete a log streaming destination"

    action: Literal["delete_log_streaming_destination"] = "delete_log_streaming_destination"
    destination_id: str = Field(..., description="The id of the log streaming destination.")


class TestLogStreamingDestinationInput(_Base):
    "Send a test message to a log streaming destination"

    action: Literal["test_log_streaming_destination"] = "test_log_streaming_destination"
    destination_id: str = Field(..., description="The id of the log streaming destination.")


# ---- Credential type policy ------------------------------------------------
class GetCredentialTypeInstancePolicyInput(_Base):
    "Preview: Retrieve the instance credential type policy"

    action: Literal["get_credential_type_instance_policy"] = "get_credential_type_instance_policy"


class UpdateCredentialTypeInstancePolicyInput(_Base):
    "Preview: Replace the instance credential type policy"

    action: Literal["update_credential_type_instance_policy"] = (
        "update_credential_type_instance_policy"
    )
    rules: list[dict[str, Any]]
    default_action: Literal["allow", "deny", "delegate"]
    version: int


class GetCredentialTypeProjectPolicyInput(_Base):
    "Preview: Retrieve a project's credential type policy"

    action: Literal["get_credential_type_project_policy"] = "get_credential_type_project_policy"
    project_id: str = Field(..., description="The ID of the project.")


class UpdateCredentialTypeProjectPolicyInput(_Base):
    "Preview: Replace a project's credential type policy"

    action: Literal["update_credential_type_project_policy"] = (
        "update_credential_type_project_policy"
    )
    project_id: str = Field(..., description="The ID of the project.")
    rules: list[dict[str, Any]]
    default_action: Literal["allow", "deny"]
    version: int


class ListCredentialTypePolicyDocumentsInput(_Base):
    "Preview: List credential type policy documents"

    action: Literal["list_credential_type_policy_documents"] = (
        "list_credential_type_policy_documents"
    )
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )


class CreateCredentialTypePolicyDocumentInput(_Idempotent):
    "Preview: Create a credential type policy document"

    action: Literal["create_credential_type_policy_document"] = (
        "create_credential_type_policy_document"
    )
    rules: list[dict[str, Any]]


class GetCredentialTypePolicyDocumentInput(_Base):
    "Preview: Retrieve a credential type policy document"

    action: Literal["get_credential_type_policy_document"] = "get_credential_type_policy_document"
    policy_id: str = Field(
        ...,
        description="The ID of the credential type policy document.",
    )


class UpdateCredentialTypePolicyDocumentInput(_Base):
    "Preview: Replace the rules of a credential type policy document"

    action: Literal["update_credential_type_policy_document"] = (
        "update_credential_type_policy_document"
    )
    policy_id: str = Field(
        ...,
        description="The ID of the credential type policy document.",
    )
    rules: list[dict[str, Any]]
    version: int


class DeleteCredentialTypePolicyDocumentInput(_Base):
    "Preview: Delete a credential type policy document"

    action: Literal["delete_credential_type_policy_document"] = (
        "delete_credential_type_policy_document"
    )
    policy_id: str = Field(
        ...,
        description="The ID of the credential type policy document.",
    )


class ListCredentialTypePolicyAttachmentsInput(_Base):
    "Preview: Retrieve a scope's attached policy documents"

    action: Literal["list_credential_type_policy_attachments"] = (
        "list_credential_type_policy_attachments"
    )
    scope_id: str = Field(
        ...,
        description="The ID of the credential type policy scope.",
    )


class ReplaceCredentialTypePolicyAttachmentsInput(_Base):
    "Preview: Replace a scope's attached policy documents"

    action: Literal["replace_credential_type_policy_attachments"] = (
        "replace_credential_type_policy_attachments"
    )
    scope_id: str = Field(
        ...,
        description="The ID of the credential type policy scope.",
    )
    attachments: list[dict[str, Any]]
    version: int


# ---- Node type policy ------------------------------------------------------
class GetNodeTypeInstancePolicyInput(_Base):
    "Preview: Retrieve the instance node type policy"

    action: Literal["get_node_type_instance_policy"] = "get_node_type_instance_policy"


class UpdateNodeTypeInstancePolicyInput(_Base):
    "Preview: Replace the instance node type policy"

    action: Literal["update_node_type_instance_policy"] = "update_node_type_instance_policy"
    rules: list[dict[str, Any]]
    default_action: Literal["allow", "deny", "delegate"]
    version: int


class GetNodeTypeProjectPolicyInput(_Base):
    "Preview: Retrieve a project's node type policy"

    action: Literal["get_node_type_project_policy"] = "get_node_type_project_policy"
    project_id: str = Field(..., description="The ID of the project.")


class UpdateNodeTypeProjectPolicyInput(_Base):
    "Preview: Replace a project's node type policy"

    action: Literal["update_node_type_project_policy"] = "update_node_type_project_policy"
    project_id: str = Field(..., description="The ID of the project.")
    rules: list[dict[str, Any]]
    default_action: Literal["allow", "deny"]
    version: int


class ListNodeTypePolicyDocumentsInput(_Base):
    "Preview: List node type policy documents"

    action: Literal["list_node_type_policy_documents"] = "list_node_type_policy_documents"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )


class CreateNodeTypePolicyDocumentInput(_Idempotent):
    "Preview: Create a node type policy document"

    action: Literal["create_node_type_policy_document"] = "create_node_type_policy_document"
    rules: list[dict[str, Any]]


class GetNodeTypePolicyDocumentInput(_Base):
    "Preview: Retrieve a node type policy document"

    action: Literal["get_node_type_policy_document"] = "get_node_type_policy_document"
    policy_id: str = Field(..., description="The ID of the node type policy document.")


class UpdateNodeTypePolicyDocumentInput(_Base):
    "Preview: Replace the rules of a node type policy document"

    action: Literal["update_node_type_policy_document"] = "update_node_type_policy_document"
    policy_id: str = Field(..., description="The ID of the node type policy document.")
    rules: list[dict[str, Any]]
    version: int


class DeleteNodeTypePolicyDocumentInput(_Base):
    "Preview: Delete a node type policy document"

    action: Literal["delete_node_type_policy_document"] = "delete_node_type_policy_document"
    policy_id: str = Field(..., description="The ID of the node type policy document.")


class ListNodeTypePolicyAttachmentsInput(_Base):
    "Preview: Retrieve a scope's attached policy documents"

    action: Literal["list_node_type_policy_attachments"] = "list_node_type_policy_attachments"
    scope_id: str = Field(..., description="The ID of the node type policy scope.")


class ReplaceNodeTypePolicyAttachmentsInput(_Base):
    "Preview: Replace a scope's attached policy documents"

    action: Literal["replace_node_type_policy_attachments"] = "replace_node_type_policy_attachments"
    scope_id: str = Field(..., description="The ID of the node type policy scope.")
    attachments: list[dict[str, Any]]
    version: int


# ---- Promotions ------------------------------------------------------------
class CreatePromotionProviderInput(_Idempotent):
    "Create a promotion provider"

    action: Literal["create_promotion_provider"] = "create_promotion_provider"
    name: str
    type: Literal["git", "gitlab"]
    auth: Any
    config: dict[str, Any] | None = None


class ListPromotionProvidersInput(_Base):
    "List promotion providers"

    action: Literal["list_promotion_providers"] = "list_promotion_providers"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )


class GetPromotionProviderInput(_Base):
    "Retrieve a promotion provider"

    action: Literal["get_promotion_provider"] = "get_promotion_provider"
    promotion_provider_id: str = Field(..., description="The ID of the promotion provider.")


class UpdatePromotionProviderInput(_Base):
    "Update a promotion provider"

    action: Literal["update_promotion_provider"] = "update_promotion_provider"
    promotion_provider_id: str = Field(..., description="The ID of the promotion provider.")
    name: str | None = None
    auth: Any | None = None
    config: dict[str, Any] | None = None


class DeletePromotionProviderInput(_Base):
    "Delete a promotion provider"

    action: Literal["delete_promotion_provider"] = "delete_promotion_provider"
    promotion_provider_id: str = Field(..., description="The ID of the promotion provider.")


class ListPromotionProviderRepositoriesInput(_Base):
    "List repositories of a promotion provider"

    action: Literal["list_promotion_provider_repositories"] = "list_promotion_provider_repositories"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    promotion_provider_id: str = Field(..., description="The ID of the promotion provider.")
    search: str | None = None


class ListPromotionProviderRepositoryBranchesInput(_Base):
    "List branches of a promotion repository"

    action: Literal["list_promotion_provider_repository_branches"] = (
        "list_promotion_provider_repository_branches"
    )
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    promotion_provider_id: str = Field(..., description="The ID of the promotion provider.")
    repository_id: str = Field(
        ...,
        description=(
            "The URL-encoded fullPath of the repository. A GitLab project ID is also accepted."
        ),
    )
    search: str | None = None


class CreatePromotionEnvironmentInput(_Idempotent):
    "Create a promotion environment"

    action: Literal["create_promotion_environment"] = "create_promotion_environment"
    environment: dict[str, Any] = Field(
        ...,
        description="The environment definition (its shape depends on its type).",
        json_schema_extra={"nw_in": "body"},
    )


class ListPromotionEnvironmentsInput(_Base):
    "List promotion environments"

    action: Literal["list_promotion_environments"] = "list_promotion_environments"
    limit: int | None = Field(
        None,
        ge=1,
        le=250,
        description="The maximum number of items to return.",
    )
    cursor: str | None = Field(
        None,
        description=(
            "Paginate by setting the cursor parameter to the nextCursor attribute "
            "returned by the previous request's response. Default value fetches the "
            'first "page" of the collection. See pagination for more detail.'
        ),
    )
    scope: Literal["instance", "projects"] | None = None
    direction: Literal["apply", "promote"] | None = None
    provider_id: str | None = None


class GetPromotionEnvironmentInput(_Base):
    "Retrieve a promotion environment"

    action: Literal["get_promotion_environment"] = "get_promotion_environment"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")


class UpdatePromotionEnvironmentInput(_Base):
    "Update a promotion environment"

    action: Literal["update_promotion_environment"] = "update_promotion_environment"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")
    name: str | None = None
    provider_id: str | None = None
    remote: dict[str, Any] | None = None
    settings: Any | None = None


class DeletePromotionEnvironmentInput(_Base):
    "Delete a promotion environment"

    action: Literal["delete_promotion_environment"] = "delete_promotion_environment"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")


class ConnectPromotionCheckoutInput(_Idempotent):
    "Connect an environment"

    action: Literal["connect_promotion_checkout"] = "connect_promotion_checkout"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")


class DisconnectPromotionCheckoutInput(_Base):
    "Remove an environment's local checkout"

    action: Literal["disconnect_promotion_checkout"] = "disconnect_promotion_checkout"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")


class ListPromotionEnvironmentProjectsInput(_Base):
    "List projects linked to a promotion environment"

    action: Literal["list_promotion_environment_projects"] = "list_promotion_environment_projects"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")


class ListPromotionEnvironmentMissingProjectsInput(_Base):
    "List package projects that are missing on this instance"

    action: Literal["list_promotion_environment_missing_projects"] = (
        "list_promotion_environment_missing_projects"
    )
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")


class AddProjectToPromotionEnvironmentInput(_Idempotent):
    "Link a project to a promotion environment"

    action: Literal["add_project_to_promotion_environment"] = "add_project_to_promotion_environment"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")
    project_id: str = Field(..., description="The ID of the project.")


class RemoveProjectFromPromotionEnvironmentInput(_Base):
    "Unlink a project from a promotion environment"

    action: Literal["remove_project_from_promotion_environment"] = (
        "remove_project_from_promotion_environment"
    )
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")
    project_id: str = Field(..., description="The ID of the project.")


class PromotePackageInput(_Idempotent):
    "Promote all team projects"

    action: Literal["promote_package"] = "promote_package"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")
    commit_message: str | None = Field(
        None,
        description=(
            "Message for the promotion commit. If you omit it, the commit uses a generated message."
        ),
    )
    force: bool | None = None


class ApplyPackageInput(_Idempotent):
    "Apply a package to the instance"

    action: Literal["apply_package"] = "apply_package"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")
    expected_source: dict[str, Any] | None = None


class ContinueApplyPackageInput(_Idempotent):
    "Continue Apply after binding setup"

    action: Literal["continue_apply_package"] = "continue_apply_package"
    promotion_environment_id: str = Field(..., description="The ID of the promotion environment.")
    expected_source: dict[str, Any]
    confirm_destructive_changes: bool | None = Field(
        None,
        description=(
            "Also applies destructive data table changes, which a `destructive- "
            "change` conflict reports: removed or retyped columns. The values in "
            "those columns are removed. Rows and other columns are kept. On a full "
            "apply, also deletes the team proj..."
        ),
    )


class GetPromotionChangesInput(_Base):
    "List the changes of a project in one direction"

    action: Literal["get_promotion_changes"] = "get_promotion_changes"
    project_id: str = Field(..., description="The ID of the project.")
    direction: Literal["apply", "promote"] = Field(
        ...,
        description="The direction of the promotion: apply or promote.",
    )
    search: str | None = None
    sort: Literal["name", "updatedAt", "status"] | None = None
    order: Literal["asc", "desc"] | None = None


class PromoteProjectSelectionInput(_Idempotent):
    "Promote a selection of a project's workflows"

    action: Literal["promote_project_selection"] = "promote_project_selection"
    project_id: str = Field(..., description="The ID of the project.")
    workflow_ids: list[str]
    commit_message: str | None = Field(
        None,
        description=(
            "Message for the promotion commit. If you omit it, the commit uses a generated message."
        ),
    )


class ApplyProjectSelectionInput(_Idempotent):
    "Apply a selection of a project's workflows"

    action: Literal["apply_project_selection"] = "apply_project_selection"
    project_id: str = Field(..., description="The ID of the project.")
    workflow_ids: list[str]
    expected_source: dict[str, Any] | None = None
    expected_preview_revision: str | None = Field(
        None,
        description=(
            "The `revision` of the reviewed Apply preview. Apply builds the preview "
            "again and returns status `preview-changed` without writing when it "
            "differs, for example after a local edit."
        ),
    )


class ContinueApplyProjectSelectionInput(_Idempotent):
    "Continue applying a selection after binding setup"

    action: Literal["continue_apply_project_selection"] = "continue_apply_project_selection"
    project_id: str = Field(..., description="The ID of the project.")
    workflow_ids: list[str]
    expected_source: dict[str, Any]
    expected_preview_revision: str | None = Field(
        None,
        description=(
            "The `revision` of the reviewed Apply preview. Apply builds the preview "
            "again and returns status `preview-changed` without writing when it "
            "differs, for example after a local edit."
        ),
    )
    confirm_destructive_changes: bool | None = Field(
        None,
        description=(
            "Also applies destructive data table changes, which a `destructive- "
            "change` conflict reports: removed or retyped columns. The values in "
            "those columns are removed. Rows and other columns are kept. On a full "
            "apply, also deletes the team proj..."
        ),
    )


# ---- n8n packages ----------------------------------------------------------
class ImportPackageInput(_Idempotent):
    "Beta: Import an n8n package into a project"

    action: Literal["import_package"] = "import_package"
    package: str = Field(
        ...,
        description=(
            "Base64 of the file (the .n8np archive). Gzip-compressed tar package (`.n8np`)."
        ),
        json_schema_extra={"nw_in": "file"},
    )
    project_id: str | None = Field(
        None,
        description=(
            "Target project id. Omit or send empty to import into the caller's personal project."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    folder_id: str | None = Field(
        None,
        description=(
            "Optional folder within the target project. Omit or send empty for project root."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    credential_matching_mode: Literal["id-only", "name-and-type", "type-only"] | None = Field(
        None,
        description=(
            "How credential references in `requirements.credentials` are matched on "
            "the target instance. `id-only` (default) matches by id. `name-and-type` "
            "matches credentials with the exact same name and type. `type-only` "
            "matches any credential of t..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    credential_missing_mode: (
        Literal["must-preexist", "create-stub", "create-with-values"] | None
    ) = Field(
        None,
        description=(
            "What to do when a credential reference cannot be resolved. `create-stub` "
            "(default) creates empty credential placeholders in the target project "
            "for missing references. `create-with-values` also creates them, seeded "
            "from the expression dat..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    credential_conflict_policy: Literal["keep-existing", "overwrite", "fail"] | None = Field(
        None,
        description=(
            "What to do when a credential reference matches an existing credential "
            "and the package has different values for it. `keep-existing` (default) "
            "never changes a matched credential. `overwrite` writes the package name "
            "and the expression value..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    bindings: str | None = Field(
        None,
        description=(
            "Optional JSON object of explicit source→target id bindings, keyed by "
            "entity type. Only `credentials` is supported today: send "
            '`{"credentials":{"<packageCredentialId>":"<targetCredentialId>"}}` to '
            "map credential ids from the package to cr..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    conflict_policy: Literal["new-version", "fail", "skip"] = Field(
        ...,
        description=(
            "Applies to Agents, workflows and the skills of imported Agents. `new- "
            "version` replaces matched definitions and saves a changed skill as a new "
            "version. `fail` rejects the import when an Agent or workflow matches, or "
            "when a matched skill h..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    publishing_policy: (
        Literal["preserve-published-state", "match-source", "publish-all", "unpublish-all"] | None
    ) = Field(
        None,
        description=(
            "Controls publication of imported Agents and workflows after definitions "
            "are saved. `preserve-published-state` (default) keeps new entities "
            "unpublished. It republishes an updated entity only if it was published "
            "and the package contains th..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    id_policy: Literal["source"] | None = Field(
        None,
        description=(
            "Controls IDs for newly created Agents and workflows. Only `source` is "
            "supported. It uses the package ID. An ID already used by an unmatched "
            "Agent or workflow, in any project, blocks the import. Matched entities "
            "keep their destination ID...."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    missing_node_type_mode: Literal["fail", "import-anyway"] | None = Field(
        None,
        description=(
            "Controls missing node types and versions in workflow nodes and Agent "
            "node tools. `fail` (default) rejects the import before writes. The "
            "issues identify the affected consumers. `import-anyway` saves the "
            "definitions but blocks their public..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    project_conflict_policy: Literal["merge", "fail", "overwrite"] | None = Field(
        None,
        description=(
            "What to do when a project the package defines already exists here "
            "(matched by id), and — unless `folderConflictPolicy` overrides it — how "
            "its contents are treated. `merge` (default) leaves the existing "
            "project's name, description, icon a..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    folder_conflict_policy: Literal["merge", "fail", "overwrite"] | None = Field(
        None,
        description=(
            "What to do when a package folder already exists at the same position in "
            "the target project. Defaults to whatever `projectConflictPolicy` is, so "
            "the intent is stated once; a workflow package defines no projects, so it "
            "defaults to `merge`..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    overwrite_deletion_policy: Literal["archive", "hard-delete"] | None = Field(
        None,
        description=(
            "How `folderConflictPolicy=overwrite` removes a workflow the package does "
            "not contain. `archive` (default) archives it, so it stays recoverable "
            "along with its execution history. `hard-delete` archives it — the step "
            "that unpublishes it, wh..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    data_table_matching_mode: Literal["by-id"] | None = Field(
        None,
        description=(
            "How data tables referenced by the package's workflows are matched "
            "against the target project. `by-id` matches the target-project table "
            "with the same id (imported tables keep their source id) and never falls "
            "back to name matching, so a ma..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    data_table_missing_mode: Literal["create", "must-preexist", "do-nothing"] | None = Field(
        None,
        description=(
            "Controls what happens when a referenced data table has no match in the "
            "target project. `create` (default) creates it from the package schema, "
            "keeping the source id, with no rows; requires the `dataTable:create` "
            "scope. `must-preexist` rej..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    data_table_schema_conflict_policy: (
        Literal["keep-existing", "fail", "overwrite", "overwrite-non-destructive"] | None
    ) = Field(
        None,
        description=(
            "How strictly a matched data table's schema is compared. `keep-existing` "
            "(default) accepts a target table that has every package column with the "
            "same name and type. The target table can have extra columns. `fail` "
            "rejects any difference, i..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    variable_missing_mode: (
        Literal["do-nothing", "must-preexist", "create-stub", "create-with-value"] | None
    ) = Field(
        None,
        description=(
            "Controls what happens when a variable referenced by the package's "
            "workflows is absent from the target project and the global scope (lookup "
            "order: project, then global). `create-with-value` (default) creates the "
            "variable with its package..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    variable_conflict_policy: Literal["keep-existing", "overwrite", "fail"] | None = Field(
        None,
        description=(
            "What to do when a referenced variable resolves in the target project or "
            "global scope but the package bundles a *different* value for it. `keep- "
            "existing` (default) leaves the target value untouched and reports the "
            "name under `variables.ma..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    variable_parent_policy: Literal["project", "global"] | None = Field(
        None,
        description=(
            "Where `create-with-value` and `create-stub` create missing variables for "
            "workflow/folder packages. `project` — also the behaviour when the field "
            "is omitted — creates them in the import target project (`projectId`, "
            "else the caller's perso..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    tag_missing_mode: Literal["create", "do-nothing"] | None = Field(
        None,
        description=(
            "What to do when a tag referenced by the package's workflows has no tag "
            "with the same id on the target instance (tags are matched by source id, "
            "never by name). `create` (default) creates the tag globally with its "
            "package (source) id and n..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    tag_conflict_policy: Literal["skip", "fail", "rename"] | None = Field(
        None,
        description=(
            "What to do when a referenced tag conflicts on the target instance — "
            "either the same-id target tag carries a different name (rename drift), "
            "or the tag's name is already held by a different tag (name collision). "
            "`skip` (default) drops the..."
        ),
        json_schema_extra={"nw_in": "form"},
    )


class ImportPackageSelectionInput(_Idempotent):
    "Beta: Import a selected subset of workflows from an n8n package"

    action: Literal["import_package_selection"] = "import_package_selection"
    package: str = Field(
        ...,
        description=(
            "Base64 of the file (the .n8np archive). Gzip-compressed tar package (`.n8np`)."
        ),
        json_schema_extra={"nw_in": "file"},
    )
    selected_project_id: str = Field(
        ...,
        description=(
            "Id of the single source project the selection is scoped to, as it "
            "appears in the package."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    selected_workflow_ids: str = Field(
        ...,
        description=(
            "JSON-encoded array of source workflow ids to import from "
            '`selectedProjectId`, e.g. `["wf-1","wf-2"]`. An empty array (`[]`) is '
            "allowed — useful for a delete-only request. Ids outside the scoped "
            "project are dropped."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    deleted_workflow_ids: str | None = Field(
        None,
        description=(
            "Optional JSON-encoded array of destination workflow ids to remove from "
            'the scoped project, e.g. `["wf-3"]`. Removal happens even under the '
            "additive profile, and the manner of removal follows "
            "`overwriteDeletionPolicy` (archive by default)..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    conflict_policy: Literal["new-version", "fail", "skip"] | None = Field(
        None,
        description=(
            "What happens when a selected workflow matches an existing workflow by "
            "source id in the target project. `new-version` (default) updates it and "
            "creates a new version; `fail` rejects the import when any matching "
            "workflow exists; `skip` leav..."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    id_policy: Literal["source"] | None = Field(
        None,
        description=(
            "Controls IDs for newly created workflows. Only `source` is supported. It "
            "uses the package ID. An ID already used by an unmatched workflow, in any "
            "project, blocks the import. Matched workflows keep their destination ID."
        ),
        json_schema_extra={"nw_in": "form"},
    )
    overwrite_deletion_policy: Literal["archive", "hard-delete"] | None = Field(
        None,
        description=(
            "How the ids in `deletedWorkflowIds` are removed. `archive` (default) "
            "archives each one, so it stays recoverable along with its execution "
            "history. `hard-delete` archives it — the step that unpublishes it, which "
            "a delete will not do on its..."
        ),
        json_schema_extra={"nw_in": "form"},
    )


class ExportPackageInput(_Idempotent):
    "Beta: Export agents, workflows, folders, or projects as an n8n package"

    action: Literal["export_package"] = "export_package"
    agent_ids: list[str] | None = Field(
        None,
        description="IDs of the agents to include. Requires the agents module.",
    )
    workflow_ids: list[str] | None = Field(
        None,
        description="IDs of the workflows to include in the exported package.",
    )
    folder_ids: list[str] | None = Field(
        None,
        description=(
            "IDs of the folders to include in the exported package. Each folder is "
            "exported with its nested folders."
        ),
    )
    project_ids: list[str] | None = Field(
        None,
        description=(
            "IDs of the projects to include in the exported package. Includes their "
            "agents when the agents module is enabled."
        ),
    )
    include_variable_values: bool | None = Field(
        None,
        description=(
            "Whether values of variables referenced by the exported entities are "
            "bundled into the package. When `false`, variables still travel as "
            "name/type files and are listed in the package requirements, but no "
            "values travel with the package."
        ),
    )
    include_tags: bool | None = Field(
        None,
        description=(
            "Whether tags assigned to the exported workflows are bundled into the "
            "package. When `false`, no tag files, tag references, or tag requirements "
            "travel with the package."
        ),
    )
    dependency_policy: Literal["fail", "reference-only", "include-in-package"] | None = Field(
        None,
        description=(
            "Policy for workflow and agent dependencies outside the selected package "
            "contents. Includes static workflow references and disabled agent "
            "references. `fail` aborts when a required workflow or agent is absent "
            "from the package. `include-in-..."
        ),
    )
    version_policy: (
        Literal["published-strict", "prefer-published", "ignore-unpublished", "latest"] | None
    ) = Field(
        None,
        description=(
            "Which version of each workflow and agent travels in the package. "
            "`latest` exports the current draft. `published-strict` requires a "
            "published version. `prefer-published` uses the published version when "
            "available and the draft otherwise. `..."
        ),
    )
    credential_export_policy: Literal["expression-values-only", "no-values"] | None = Field(
        None,
        description=(
            "Whether expression values from credential data are bundled into the "
            "package. `expression-values-only` includes credential fields whose value "
            "is an n8n expression (for example `={{ $secrets.apiKey }}`); literal "
            "values never travel either..."
        ),
    )
    include_archived_workflows: bool | None = Field(
        None,
        description=(
            "Whether folder and project exports include their archived workflows. "
            "When `false` (default) they are left out. When `true` they travel with "
            "`isArchived: true` and are archived on import. Workflows listed in "
            "`workflowIds` always export, a..."
        ),
    )
