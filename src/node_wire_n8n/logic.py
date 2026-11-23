#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
# Version details
#   n8n: public API v1 (spec 1.1.1), written against n8n 2.47.7. Older n8n may lack some operations.
#   Auth: the API key is sent as X-N8N-API-KEY, read from connectors.yaml `auth` (static_token).
#   instance_url: connectors.yaml only (the n8n instance root). It is never an action input.
#     (Not named base_url: the config store accepts that key only for RestConnector classes.)
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, ClassVar, Dict, Tuple, Type
from urllib.parse import quote, urlsplit

import httpx

from node_wire_runtime import BaseConnector, RestResponseOutput, nw_action
from node_wire_runtime.models import ErrorCategory
from node_wire_runtime.rest import _shared_client
from node_wire_runtime.tenant_persistence import declare_secret_shape

from .schema import (
    AddProjectToPromotionEnvironmentInput,
    AddUsersToProjectInput,
    ApplyPackageInput,
    ApplyProjectSelectionInput,
    ArchiveWorkflowInput,
    CancelTestRunInput,
    ChangeRoleInput,
    ChangeUserRoleInProjectInput,
    ClearDataTableRowsInput,
    ConnectPromotionCheckoutInput,
    ContinueApplyPackageInput,
    ContinueApplyProjectSelectionInput,
    CreateCredentialInput,
    CreateCredentialTypePolicyDocumentInput,
    CreateDataTableColumnInput,
    CreateDataTableInput,
    CreateDataTableRowsInput,
    CreateFolderInput,
    CreateLogStreamingDestinationInput,
    CreateNodeTypePolicyDocumentInput,
    CreateProjectInput,
    CreatePromotionEnvironmentInput,
    CreatePromotionProviderInput,
    CreateRoleInput,
    CreateRoleMappingRuleInput,
    CreateTagInput,
    CreateTestRunInput,
    CreateUserInput,
    CreateVariableInput,
    CreateWorkflowInput,
    DeleteCredentialInput,
    DeleteCredentialTypePolicyDocumentInput,
    DeleteDataTableColumnInput,
    DeleteDataTableInput,
    DeleteDataTableRowsInput,
    DeleteExecutionInput,
    DeleteFolderInput,
    DeleteLogStreamingDestinationInput,
    DeleteNodeTypePolicyDocumentInput,
    DeleteProjectInput,
    DeletePromotionEnvironmentInput,
    DeletePromotionProviderInput,
    DeleteRoleInput,
    DeleteRoleMappingRuleInput,
    DeleteTagInput,
    DeleteUserFromProjectInput,
    DeleteUserInput,
    DeleteVariableInput,
    DeleteWorkflowInput,
    DisconnectPromotionCheckoutInput,
    ExportPackageInput,
    GenerateAuditInput,
    GetCredentialInput,
    GetCredentialTypeInput,
    GetCredentialTypeInstancePolicyInput,
    GetCredentialTypePolicyDocumentInput,
    GetCredentialTypeProjectPolicyInput,
    GetDataTableInput,
    GetDiscoverInput,
    GetExecutionInput,
    GetFolderInput,
    GetInsightsSummaryInput,
    GetLdapConfigurationInput,
    GetLdapSyncInput,
    GetLogStreamingDestinationInput,
    GetNodeTypeInstancePolicyInput,
    GetNodeTypePolicyDocumentInput,
    GetNodeTypeProjectPolicyInput,
    GetOidcConfigurationInput,
    GetOtelSettingsInput,
    GetPromotionChangesInput,
    GetPromotionEnvironmentInput,
    GetPromotionProviderInput,
    GetRateLimitSettingsInput,
    GetRoleInput,
    GetSamlConfigurationInput,
    GetSecurityPolicyInput,
    GetSourceControlStatusInput,
    GetTagInput,
    GetTestRunInput,
    GetUserInput,
    GetWorkflowInput,
    GetWorkflowVersionInput,
    ImportPackageInput,
    ImportPackageSelectionInput,
    InstallCommunityPackageInput,
    ListCommunityPackagesInput,
    ListCredentialDependenciesInput,
    ListCredentialTypePolicyAttachmentsInput,
    ListCredentialTypePolicyDocumentsInput,
    ListCredentialsInput,
    ListDataTableColumnsInput,
    ListDataTableDependenciesInput,
    ListDataTableRowsInput,
    ListDataTablesInput,
    ListExecutionTagsInput,
    ListExecutionsInput,
    ListFoldersInput,
    ListLogStreamingDestinationsInput,
    ListLogStreamingEventTypesInput,
    ListNodeTypePolicyAttachmentsInput,
    ListNodeTypePolicyDocumentsInput,
    ListProjectUsersInput,
    ListProjectsInput,
    ListPromotionEnvironmentMissingProjectsInput,
    ListPromotionEnvironmentProjectsInput,
    ListPromotionEnvironmentsInput,
    ListPromotionProviderRepositoriesInput,
    ListPromotionProviderRepositoryBranchesInput,
    ListPromotionProvidersInput,
    ListRoleMappingRulesInput,
    ListRolesInput,
    ListTagsInput,
    ListTestCasesInput,
    ListTestRunsInput,
    ListUsersInput,
    ListVariablesInput,
    ListWorkflowDependenciesInput,
    ListWorkflowHistoryInput,
    ListWorkflowTagsInput,
    ListWorkflowsInput,
    MoveRoleMappingRuleInput,
    PromotePackageInput,
    PromoteProjectSelectionInput,
    PublishWorkflowInput,
    PullSourceControlInput,
    PushSourceControlInput,
    RemoveProjectFromPromotionEnvironmentInput,
    ReplaceCredentialTypePolicyAttachmentsInput,
    ReplaceNodeTypePolicyAttachmentsInput,
    RetryExecutionInput,
    RunLdapSyncInput,
    SetOidcConfigurationInput,
    StopExecutionInput,
    StopManyExecutionsInput,
    TestCredentialInput,
    TestLogStreamingDestinationInput,
    TestOtelTraceInput,
    TransferCredentialInput,
    TransferWorkflowInput,
    TriggerWorkflowInput,
    UnarchiveWorkflowInput,
    UninstallCommunityPackageInput,
    UnpublishWorkflowInput,
    UpdateCommunityPackageInput,
    UpdateCredentialInput,
    UpdateCredentialTypeInstancePolicyInput,
    UpdateCredentialTypePolicyDocumentInput,
    UpdateCredentialTypeProjectPolicyInput,
    UpdateDataTableColumnInput,
    UpdateDataTableInput,
    UpdateDataTableRowsInput,
    UpdateExecutionTagsInput,
    UpdateFolderInput,
    UpdateLdapConfigurationInput,
    UpdateLogStreamingDestinationInput,
    UpdateNodeTypeInstancePolicyInput,
    UpdateNodeTypePolicyDocumentInput,
    UpdateNodeTypeProjectPolicyInput,
    UpdateOtelSettingsInput,
    UpdateProjectInput,
    UpdatePromotionEnvironmentInput,
    UpdatePromotionProviderInput,
    UpdateRateLimitSettingsInput,
    UpdateRoleInput,
    UpdateRoleMappingRuleInput,
    UpdateSamlConfigurationInput,
    UpdateSecurityPolicyInput,
    UpdateTagInput,
    UpdateVariableInput,
    UpdateWorkflowInput,
    UpdateWorkflowTagsInput,
    UpsertDataTableRowInput,
)

logger = logging.getLogger("connectors.n8n")


# The error map works per exception class, so each kind of answer that needs its own category
# gets a small subclass; `_send` picks it by status. The runtime retries RETRYABLE errors.
class _RateLimited(httpx.HTTPStatusError):
    """429: n8n refused the call, so a retry is safe."""


class _Unavailable(httpx.HTTPStatusError):
    """502/503/504 on GET or DELETE (safe to repeat; a gateway error on a POST may have run it)."""


class _WriteTimedOut(httpx.TimeoutException):
    """A write (or webhook call) timed out after it was sent, with no Idempotency-Key: n8n may
    have run it already, so the runtime must not repeat it."""


class _AuthRejected(httpx.HTTPStatusError):
    """401, or 403 for a key that lacks the scope (a licence 403 stays a plain status error)."""


class N8nConnector(BaseConnector):
    """n8n public API (and workflow webhooks) as @nw_action methods."""

    connector_id = "n8n"
    output_model = RestResponseOutput

    # Simplified: other 5xx (and 502/503/504 on writes) are not retried, nor is a timeout of a write
    # sent without an Idempotency-Key.
    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {
        httpx.TimeoutException: (ErrorCategory.RETRYABLE, "N8N_TIMEOUT"),
        httpx.ConnectError: (ErrorCategory.RETRYABLE, "N8N_CONNECT_ERROR"),
        _WriteTimedOut: (ErrorCategory.FATAL, "N8N_TIMEOUT_OUTCOME_UNKNOWN"),
        httpx.RequestError: (ErrorCategory.FATAL, "N8N_REQUEST_ERROR"),
        httpx.HTTPStatusError: (ErrorCategory.BUSINESS, "N8N_HTTP_STATUS_ERROR"),
        _RateLimited: (ErrorCategory.RETRYABLE, "N8N_RATE_LIMITED"),
        _Unavailable: (ErrorCategory.RETRYABLE, "N8N_UNAVAILABLE"),
        _AuthRejected: (ErrorCategory.AUTH, "N8N_AUTH_ERROR"),
        ValueError: (ErrorCategory.BUSINESS, "N8N_INVALID_REQUEST"),
    }

    def _instance_url(self) -> str:
        base = str(self.config.get("instance_url") or "").strip().rstrip("/")
        if not base:
            raise ValueError("n8n instance_url is not configured (connectors.yaml instance_url)")
        return base

    async def _send(
        self,
        method: str,
        url: str,
        trace_id: str,
        *,
        headers: Dict[str, str],
        query: Dict[str, Any] | None = None,
        body: Any = None,
        form: Dict[str, str] | None = None,
        files: Dict[str, Any] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> RestResponseOutput:
        # Only the method and the URL path are logged, never headers, query values or bodies.
        logger.info(
            "Calling n8n",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "method": method,
                "path": urlsplit(url).path,
            },
        )
        # Simplified: no private-address guard. The runtime's pooled client has no timeout of its own.
        try:
            response = await _shared_client(False).request(
                method,
                url,
                headers=headers,
                params=query or None,
                json=body,
                data=form or None,
                files=files or None,
                auth=auth,
                timeout=float(os.getenv("NW_TIMEOUT", "30.0")),
            )
        except httpx.TimeoutException as exc:
            # A connect or pool timeout means nothing was sent, so a retry is always safe. After that,
            # only reads, deletes and writes carrying an Idempotency-Key may be repeated.
            sent = not isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout))
            if sent and method not in ("GET", "DELETE") and "Idempotency-Key" not in headers:
                raise _WriteTimedOut(str(exc) or "timed out after the request was sent") from exc
            raise
        if response.is_error:
            status = response.status_code
            error: type[httpx.HTTPStatusError] = httpx.HTTPStatusError
            if status == 429:
                error = _RateLimited
            elif status in (502, 503, 504) and method in ("GET", "DELETE"):
                error = _Unavailable
            elif status == 401 or (status == 403 and "licen" not in response.text.lower()):
                error = _AuthRejected
            # n8n's own explanation goes in the message.
            raise error(
                f"n8n answered {response.status_code}: {response.text[:300]}",
                request=response.request,
                response=response,
            )
        content: Any = None
        ctype = response.headers.get("content-type", "")
        if response.content and ("gzip" in ctype or "octet-stream" in ctype):
            # Simplified: a binary answer (the package export) comes back as base64 text.
            content = base64.b64encode(response.content).decode()
        elif response.content:
            try:
                content = response.json()
            except ValueError:
                content = response.text
        return RestResponseOutput(
            status_code=response.status_code, headers=dict(response.headers), body=content
        )

    async def _call(
        self, method: str, path: str, params: Any, trace_id: str, *, empty_body: bool = False
    ) -> RestResponseOutput:
        """Send one API call. Fields named in ``path`` go in the path; the rest go to the query
        (GET/DELETE) or the JSON body (other methods), unless a field says ``nw_in``
        (``header``, ``query``, ``body`` for the whole body, ``file`` or ``form`` for a multipart upload).
        ``empty_body``: the API requires a JSON body, so send ``{}`` when every field was left out."""
        query: Dict[str, Any] = {}
        headers: Dict[str, str] = {"Accept": "application/json"}
        body: Any = None
        form: Dict[str, str] = {}
        files: Dict[str, Any] = {}
        path_values: Dict[str, str] = {}
        fields = type(params).model_fields
        values = params.model_dump(mode="json", exclude_unset=True, exclude={"action"})
        for name, value in values.items():
            wire = fields[name].serialization_alias or name
            extra = fields[name].json_schema_extra
            where = extra.get("nw_in") if isinstance(extra, dict) else None
            if "{" + name + "}" in path:
                path_values[name] = quote(str(value), safe="")
            elif where == "header":
                headers[wire] = str(value)
            elif where == "body":
                body = value
            elif where == "file":
                files[wire] = (
                    "package.n8np",
                    base64.b64decode(value, validate=True),
                    "application/gzip",
                )
            elif where == "form":
                form[wire] = value if isinstance(value, str) else json.dumps(value)
            elif where == "query" or method in ("GET", "DELETE"):
                query[wire] = value
            else:
                body = body or {}
                body[wire] = value
        headers.update(await self.get_auth_headers())
        if empty_body and body is None:
            body = {}
        url = self._instance_url() + "/api/v1" + path.format_map(path_values)
        return await self._send(
            method, url, trace_id, headers=headers, query=query, body=body, form=form, files=files
        )

    # ---- Workflows -----------------------------------------------------
    @nw_action("list_workflows", http_method="GET", http_path="/workflows")
    async def list_workflows(
        self, params: ListWorkflowsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/workflows", params, trace_id)

    @nw_action("create_workflow", http_method="POST", http_path="/workflows")
    async def create_workflow(
        self, params: CreateWorkflowInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/workflows", params, trace_id)

    @nw_action("get_workflow", http_method="GET", http_path="/workflows/{workflowId}")
    async def get_workflow(self, params: GetWorkflowInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/workflows/{workflow_id}", params, trace_id)

    @nw_action("update_workflow", http_method="PUT", http_path="/workflows/{workflowId}")
    async def update_workflow(
        self, params: UpdateWorkflowInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/workflows/{workflow_id}", params, trace_id)

    @nw_action("delete_workflow", http_method="DELETE", http_path="/workflows/{workflowId}")
    async def delete_workflow(
        self, params: DeleteWorkflowInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("DELETE", "/workflows/{workflow_id}", params, trace_id)

    @nw_action("archive_workflow", http_method="POST", http_path="/workflows/{workflowId}/archive")
    async def archive_workflow(
        self, params: ArchiveWorkflowInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/workflows/{workflow_id}/archive", params, trace_id)

    @nw_action(
        "unarchive_workflow", http_method="POST", http_path="/workflows/{workflowId}/unarchive"
    )
    async def unarchive_workflow(
        self, params: UnarchiveWorkflowInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/workflows/{workflow_id}/unarchive", params, trace_id)

    @nw_action("transfer_workflow", http_method="PUT", http_path="/workflows/{workflowId}/transfer")
    async def transfer_workflow(
        self, params: TransferWorkflowInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/workflows/{workflow_id}/transfer", params, trace_id)

    @nw_action("publish_workflow", http_method="POST", http_path="/workflows/{workflowId}/publish")
    async def publish_workflow(
        self, params: PublishWorkflowInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/workflows/{workflow_id}/publish", params, trace_id)

    @nw_action(
        "unpublish_workflow", http_method="POST", http_path="/workflows/{workflowId}/unpublish"
    )
    async def unpublish_workflow(
        self, params: UnpublishWorkflowInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/workflows/{workflow_id}/unpublish", params, trace_id)

    @nw_action(
        "list_workflow_history", http_method="GET", http_path="/workflows/{workflowId}/history"
    )
    async def list_workflow_history(
        self, params: ListWorkflowHistoryInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/workflows/{workflow_id}/history", params, trace_id)

    @nw_action(
        "get_workflow_version",
        http_method="GET",
        http_path="/workflows/{workflowId}/versions/{workflowVersionId}",
    )
    async def get_workflow_version(
        self, params: GetWorkflowVersionInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/workflows/{workflow_id}/versions/{workflow_version_id}", params, trace_id
        )

    @nw_action("list_workflow_tags", http_method="GET", http_path="/workflows/{workflowId}/tags")
    async def list_workflow_tags(
        self, params: ListWorkflowTagsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/workflows/{workflow_id}/tags", params, trace_id)

    @nw_action("update_workflow_tags", http_method="PUT", http_path="/workflows/{workflowId}/tags")
    async def update_workflow_tags(
        self, params: UpdateWorkflowTagsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/workflows/{workflow_id}/tags", params, trace_id)

    @nw_action(
        "list_workflow_dependencies",
        http_method="GET",
        http_path="/workflows/{workflowId}/dependencies",
    )
    async def list_workflow_dependencies(
        self, params: ListWorkflowDependenciesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/workflows/{workflow_id}/dependencies", params, trace_id)

    @nw_action("trigger_workflow")
    async def trigger_workflow(
        self, params: TriggerWorkflowInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Call a workflow's webhook. The n8n API key is not sent."""
        root = urlsplit(self._instance_url())
        target = urlsplit(params.webhook_url)
        if (target.scheme, target.netloc) != (root.scheme, root.netloc):
            raise ValueError("webhook_url must be on the configured n8n host")
        headers: Dict[str, str] = {}
        if params.auth_type == "header":
            headers[params.auth_header_name] = params.auth_header_value
        basic = (params.username, params.password) if params.auth_type == "basic" else None
        is_get = params.method == "GET"
        return await self._send(
            params.method,
            params.webhook_url,
            trace_id,
            headers=headers,
            query=params.payload if is_get else None,
            body=None if is_get else params.payload,
            auth=basic,
        )

    # ---- Executions ----------------------------------------------------
    @nw_action("list_executions", http_method="GET", http_path="/executions")
    async def list_executions(
        self, params: ListExecutionsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/executions", params, trace_id)

    @nw_action("get_execution", http_method="GET", http_path="/executions/{executionId}")
    async def get_execution(
        self, params: GetExecutionInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/executions/{execution_id}", params, trace_id)

    @nw_action("delete_execution", http_method="DELETE", http_path="/executions/{executionId}")
    async def delete_execution(
        self, params: DeleteExecutionInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("DELETE", "/executions/{execution_id}", params, trace_id)

    @nw_action("list_execution_tags", http_method="GET", http_path="/executions/{executionId}/tags")
    async def list_execution_tags(
        self, params: ListExecutionTagsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/executions/{execution_id}/tags", params, trace_id)

    @nw_action(
        "update_execution_tags", http_method="PUT", http_path="/executions/{executionId}/tags"
    )
    async def update_execution_tags(
        self, params: UpdateExecutionTagsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/executions/{execution_id}/tags", params, trace_id)

    @nw_action("stop_many_executions", http_method="POST", http_path="/executions/stop")
    async def stop_many_executions(
        self, params: StopManyExecutionsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/executions/stop", params, trace_id)

    @nw_action("stop_execution", http_method="POST", http_path="/executions/{executionId}/stop")
    async def stop_execution(
        self, params: StopExecutionInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/executions/{execution_id}/stop", params, trace_id)

    @nw_action("retry_execution", http_method="POST", http_path="/executions/{executionId}/retry")
    async def retry_execution(
        self, params: RetryExecutionInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/executions/{execution_id}/retry", params, trace_id)

    # ---- Tags ----------------------------------------------------------
    @nw_action("list_tags", http_method="GET", http_path="/tags")
    async def list_tags(self, params: ListTagsInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/tags", params, trace_id)

    @nw_action("create_tag", http_method="POST", http_path="/tags")
    async def create_tag(self, params: CreateTagInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("POST", "/tags", params, trace_id)

    @nw_action("get_tag", http_method="GET", http_path="/tags/{tagId}")
    async def get_tag(self, params: GetTagInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/tags/{tag_id}", params, trace_id)

    @nw_action("update_tag", http_method="PUT", http_path="/tags/{tagId}")
    async def update_tag(self, params: UpdateTagInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("PUT", "/tags/{tag_id}", params, trace_id)

    @nw_action("delete_tag", http_method="DELETE", http_path="/tags/{tagId}")
    async def delete_tag(self, params: DeleteTagInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("DELETE", "/tags/{tag_id}", params, trace_id)

    # ---- Variables -----------------------------------------------------
    @nw_action("list_variables", http_method="GET", http_path="/variables")
    async def list_variables(
        self, params: ListVariablesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/variables", params, trace_id)

    @nw_action("create_variable", http_method="POST", http_path="/variables")
    async def create_variable(
        self, params: CreateVariableInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/variables", params, trace_id)

    @nw_action("update_variable", http_method="PUT", http_path="/variables/{id}")
    async def update_variable(
        self, params: UpdateVariableInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/variables/{id}", params, trace_id)

    @nw_action("delete_variable", http_method="DELETE", http_path="/variables/{id}")
    async def delete_variable(
        self, params: DeleteVariableInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("DELETE", "/variables/{id}", params, trace_id)

    # ---- Credentials ---------------------------------------------------
    @nw_action("list_credentials", http_method="GET", http_path="/credentials")
    async def list_credentials(
        self, params: ListCredentialsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/credentials", params, trace_id)

    @nw_action("create_credential", http_method="POST", http_path="/credentials")
    async def create_credential(
        self, params: CreateCredentialInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/credentials", params, trace_id)

    @nw_action("get_credential", http_method="GET", http_path="/credentials/{credentialId}")
    async def get_credential(
        self, params: GetCredentialInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/credentials/{credential_id}", params, trace_id)

    @nw_action("update_credential", http_method="PATCH", http_path="/credentials/{credentialId}")
    async def update_credential(
        self, params: UpdateCredentialInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PATCH", "/credentials/{credential_id}", params, trace_id, empty_body=True
        )

    @nw_action("delete_credential", http_method="DELETE", http_path="/credentials/{credentialId}")
    async def delete_credential(
        self, params: DeleteCredentialInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("DELETE", "/credentials/{credential_id}", params, trace_id)

    @nw_action(
        "transfer_credential", http_method="PUT", http_path="/credentials/{credentialId}/transfer"
    )
    async def transfer_credential(
        self, params: TransferCredentialInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/credentials/{credential_id}/transfer", params, trace_id)

    @nw_action("test_credential", http_method="POST", http_path="/credentials/{credentialId}/test")
    async def test_credential(
        self, params: TestCredentialInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/credentials/{credential_id}/test", params, trace_id)

    @nw_action(
        "get_credential_type",
        http_method="GET",
        http_path="/credentials/schema/{credentialTypeName}",
    )
    async def get_credential_type(
        self, params: GetCredentialTypeInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/credentials/schema/{credential_type_name}", params, trace_id
        )

    @nw_action(
        "list_credential_dependencies",
        http_method="GET",
        http_path="/credentials/{credentialId}/dependencies",
    )
    async def list_credential_dependencies(
        self, params: ListCredentialDependenciesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/credentials/{credential_id}/dependencies", params, trace_id
        )

    # ---- Folders -------------------------------------------------------
    @nw_action("list_folders", http_method="GET", http_path="/projects/{projectId}/folders")
    async def list_folders(self, params: ListFoldersInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/projects/{project_id}/folders", params, trace_id)

    @nw_action("create_folder", http_method="POST", http_path="/projects/{projectId}/folders")
    async def create_folder(
        self, params: CreateFolderInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/projects/{project_id}/folders", params, trace_id)

    @nw_action(
        "get_folder", http_method="GET", http_path="/projects/{projectId}/folders/{folderId}"
    )
    async def get_folder(self, params: GetFolderInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call(
            "GET", "/projects/{project_id}/folders/{folder_id}", params, trace_id
        )

    @nw_action(
        "update_folder", http_method="PATCH", http_path="/projects/{projectId}/folders/{folderId}"
    )
    async def update_folder(
        self, params: UpdateFolderInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PATCH", "/projects/{project_id}/folders/{folder_id}", params, trace_id, empty_body=True
        )

    @nw_action(
        "delete_folder", http_method="DELETE", http_path="/projects/{projectId}/folders/{folderId}"
    )
    async def delete_folder(
        self, params: DeleteFolderInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/projects/{project_id}/folders/{folder_id}", params, trace_id
        )

    # ---- Data tables ---------------------------------------------------
    @nw_action("list_data_tables", http_method="GET", http_path="/data-tables")
    async def list_data_tables(
        self, params: ListDataTablesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/data-tables", params, trace_id)

    @nw_action("create_data_table", http_method="POST", http_path="/data-tables")
    async def create_data_table(
        self, params: CreateDataTableInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/data-tables", params, trace_id)

    @nw_action("get_data_table", http_method="GET", http_path="/data-tables/{dataTableId}")
    async def get_data_table(
        self, params: GetDataTableInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/data-tables/{data_table_id}", params, trace_id)

    @nw_action("update_data_table", http_method="PATCH", http_path="/data-tables/{dataTableId}")
    async def update_data_table(
        self, params: UpdateDataTableInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PATCH", "/data-tables/{data_table_id}", params, trace_id)

    @nw_action("delete_data_table", http_method="DELETE", http_path="/data-tables/{dataTableId}")
    async def delete_data_table(
        self, params: DeleteDataTableInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("DELETE", "/data-tables/{data_table_id}", params, trace_id)

    @nw_action(
        "list_data_table_dependencies",
        http_method="GET",
        http_path="/data-tables/{dataTableId}/dependencies",
    )
    async def list_data_table_dependencies(
        self, params: ListDataTableDependenciesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/data-tables/{data_table_id}/dependencies", params, trace_id
        )

    @nw_action(
        "list_data_table_columns", http_method="GET", http_path="/data-tables/{dataTableId}/columns"
    )
    async def list_data_table_columns(
        self, params: ListDataTableColumnsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/data-tables/{data_table_id}/columns", params, trace_id)

    @nw_action(
        "create_data_table_column",
        http_method="POST",
        http_path="/data-tables/{dataTableId}/columns",
    )
    async def create_data_table_column(
        self, params: CreateDataTableColumnInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/data-tables/{data_table_id}/columns", params, trace_id)

    @nw_action(
        "update_data_table_column",
        http_method="PATCH",
        http_path="/data-tables/{dataTableId}/columns/{columnId}",
    )
    async def update_data_table_column(
        self, params: UpdateDataTableColumnInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PATCH",
            "/data-tables/{data_table_id}/columns/{column_id}",
            params,
            trace_id,
            empty_body=True,
        )

    @nw_action(
        "delete_data_table_column",
        http_method="DELETE",
        http_path="/data-tables/{dataTableId}/columns/{columnId}",
    )
    async def delete_data_table_column(
        self, params: DeleteDataTableColumnInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/data-tables/{data_table_id}/columns/{column_id}", params, trace_id
        )

    @nw_action(
        "list_data_table_rows", http_method="GET", http_path="/data-tables/{dataTableId}/rows"
    )
    async def list_data_table_rows(
        self, params: ListDataTableRowsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/data-tables/{data_table_id}/rows", params, trace_id)

    @nw_action(
        "create_data_table_rows", http_method="POST", http_path="/data-tables/{dataTableId}/rows"
    )
    async def create_data_table_rows(
        self, params: CreateDataTableRowsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/data-tables/{data_table_id}/rows", params, trace_id)

    @nw_action(
        "upsert_data_table_row",
        http_method="POST",
        http_path="/data-tables/{dataTableId}/rows/upsert",
    )
    async def upsert_data_table_row(
        self, params: UpsertDataTableRowInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST", "/data-tables/{data_table_id}/rows/upsert", params, trace_id
        )

    @nw_action(
        "update_data_table_rows",
        http_method="PATCH",
        http_path="/data-tables/{dataTableId}/rows/update",
    )
    async def update_data_table_rows(
        self, params: UpdateDataTableRowsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PATCH", "/data-tables/{data_table_id}/rows/update", params, trace_id
        )

    @nw_action(
        "clear_data_table_rows",
        http_method="DELETE",
        http_path="/data-tables/{dataTableId}/rows/clear",
    )
    async def clear_data_table_rows(
        self, params: ClearDataTableRowsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/data-tables/{data_table_id}/rows/clear", params, trace_id
        )

    @nw_action(
        "delete_data_table_rows",
        http_method="DELETE",
        http_path="/data-tables/{dataTableId}/rows/delete",
    )
    async def delete_data_table_rows(
        self, params: DeleteDataTableRowsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/data-tables/{data_table_id}/rows/delete", params, trace_id
        )

    # ---- Evaluations ---------------------------------------------------
    @nw_action("list_test_runs", http_method="GET", http_path="/workflows/{workflowId}/test-runs")
    async def list_test_runs(
        self, params: ListTestRunsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/workflows/{workflow_id}/test-runs", params, trace_id)

    @nw_action("create_test_run", http_method="POST", http_path="/workflows/{workflowId}/test-runs")
    async def create_test_run(
        self, params: CreateTestRunInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/workflows/{workflow_id}/test-runs", params, trace_id)

    @nw_action(
        "get_test_run", http_method="GET", http_path="/workflows/{workflowId}/test-runs/{runId}"
    )
    async def get_test_run(self, params: GetTestRunInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call(
            "GET", "/workflows/{workflow_id}/test-runs/{run_id}", params, trace_id
        )

    @nw_action(
        "list_test_cases",
        http_method="GET",
        http_path="/workflows/{workflowId}/test-runs/{runId}/test-cases",
    )
    async def list_test_cases(
        self, params: ListTestCasesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/workflows/{workflow_id}/test-runs/{run_id}/test-cases", params, trace_id
        )

    @nw_action(
        "cancel_test_run",
        http_method="POST",
        http_path="/workflows/{workflowId}/test-runs/{runId}/cancel",
    )
    async def cancel_test_run(
        self, params: CancelTestRunInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST", "/workflows/{workflow_id}/test-runs/{run_id}/cancel", params, trace_id
        )

    # ---- Projects ------------------------------------------------------
    @nw_action("list_projects", http_method="GET", http_path="/projects")
    async def list_projects(
        self, params: ListProjectsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/projects", params, trace_id)

    @nw_action("create_project", http_method="POST", http_path="/projects")
    async def create_project(
        self, params: CreateProjectInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/projects", params, trace_id)

    @nw_action("update_project", http_method="PUT", http_path="/projects/{projectId}")
    async def update_project(
        self, params: UpdateProjectInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/projects/{project_id}", params, trace_id)

    @nw_action("delete_project", http_method="DELETE", http_path="/projects/{projectId}")
    async def delete_project(
        self, params: DeleteProjectInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("DELETE", "/projects/{project_id}", params, trace_id)

    @nw_action("list_project_users", http_method="GET", http_path="/projects/{projectId}/users")
    async def list_project_users(
        self, params: ListProjectUsersInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/projects/{project_id}/users", params, trace_id)

    @nw_action("add_users_to_project", http_method="POST", http_path="/projects/{projectId}/users")
    async def add_users_to_project(
        self, params: AddUsersToProjectInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/projects/{project_id}/users", params, trace_id)

    @nw_action(
        "change_user_role_in_project",
        http_method="PATCH",
        http_path="/projects/{projectId}/users/{userId}",
    )
    async def change_user_role_in_project(
        self, params: ChangeUserRoleInProjectInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PATCH", "/projects/{project_id}/users/{user_id}", params, trace_id)

    @nw_action(
        "delete_user_from_project",
        http_method="DELETE",
        http_path="/projects/{projectId}/users/{userId}",
    )
    async def delete_user_from_project(
        self, params: DeleteUserFromProjectInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/projects/{project_id}/users/{user_id}", params, trace_id
        )

    # ---- Roles ---------------------------------------------------------
    @nw_action("list_roles", http_method="GET", http_path="/roles")
    async def list_roles(self, params: ListRolesInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/roles", params, trace_id)

    @nw_action("create_role", http_method="POST", http_path="/roles")
    async def create_role(self, params: CreateRoleInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("POST", "/roles", params, trace_id)

    @nw_action("get_role", http_method="GET", http_path="/roles/{roleSlug}")
    async def get_role(self, params: GetRoleInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/roles/{role_slug}", params, trace_id)

    @nw_action("update_role", http_method="PUT", http_path="/roles/{roleSlug}")
    async def update_role(self, params: UpdateRoleInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("PUT", "/roles/{role_slug}", params, trace_id)

    @nw_action("delete_role", http_method="DELETE", http_path="/roles/{roleSlug}")
    async def delete_role(self, params: DeleteRoleInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("DELETE", "/roles/{role_slug}", params, trace_id)

    # ---- Role mapping rules --------------------------------------------
    @nw_action("list_role_mapping_rules", http_method="GET", http_path="/role-mapping-rules")
    async def list_role_mapping_rules(
        self, params: ListRoleMappingRulesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/role-mapping-rules", params, trace_id)

    @nw_action("create_role_mapping_rule", http_method="POST", http_path="/role-mapping-rules")
    async def create_role_mapping_rule(
        self, params: CreateRoleMappingRuleInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/role-mapping-rules", params, trace_id)

    @nw_action(
        "move_role_mapping_rule",
        http_method="POST",
        http_path="/role-mapping-rules/{roleMappingRuleId}/move",
    )
    async def move_role_mapping_rule(
        self, params: MoveRoleMappingRuleInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST", "/role-mapping-rules/{role_mapping_rule_id}/move", params, trace_id
        )

    @nw_action(
        "update_role_mapping_rule",
        http_method="PATCH",
        http_path="/role-mapping-rules/{roleMappingRuleId}",
    )
    async def update_role_mapping_rule(
        self, params: UpdateRoleMappingRuleInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PATCH", "/role-mapping-rules/{role_mapping_rule_id}", params, trace_id
        )

    @nw_action(
        "delete_role_mapping_rule",
        http_method="DELETE",
        http_path="/role-mapping-rules/{roleMappingRuleId}",
    )
    async def delete_role_mapping_rule(
        self, params: DeleteRoleMappingRuleInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/role-mapping-rules/{role_mapping_rule_id}", params, trace_id
        )

    # ---- Users ---------------------------------------------------------
    @nw_action("list_users", http_method="GET", http_path="/users")
    async def list_users(self, params: ListUsersInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/users", params, trace_id)

    @nw_action("create_user", http_method="POST", http_path="/users")
    async def create_user(self, params: CreateUserInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("POST", "/users", params, trace_id)

    @nw_action("get_user", http_method="GET", http_path="/users/{userId}")
    async def get_user(self, params: GetUserInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/users/{user_id}", params, trace_id)

    @nw_action("delete_user", http_method="DELETE", http_path="/users/{userId}")
    async def delete_user(self, params: DeleteUserInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("DELETE", "/users/{user_id}", params, trace_id)

    @nw_action("change_role", http_method="PATCH", http_path="/users/{userId}/role")
    async def change_role(self, params: ChangeRoleInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("PATCH", "/users/{user_id}/role", params, trace_id)

    # ---- Audit ---------------------------------------------------------
    @nw_action("generate_audit", http_method="POST", http_path="/audit")
    async def generate_audit(
        self, params: GenerateAuditInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/audit", params, trace_id)

    # ---- Discover ------------------------------------------------------
    @nw_action("get_discover", http_method="GET", http_path="/discover")
    async def get_discover(self, params: GetDiscoverInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/discover", params, trace_id)

    # ---- Insights ------------------------------------------------------
    @nw_action("get_insights_summary", http_method="GET", http_path="/insights/summary")
    async def get_insights_summary(
        self, params: GetInsightsSummaryInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/insights/summary", params, trace_id)

    # ---- Community packages --------------------------------------------
    @nw_action("list_community_packages", http_method="GET", http_path="/community-packages")
    async def list_community_packages(
        self, params: ListCommunityPackagesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/community-packages", params, trace_id)

    @nw_action("install_community_package", http_method="POST", http_path="/community-packages")
    async def install_community_package(
        self, params: InstallCommunityPackageInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/community-packages", params, trace_id)

    @nw_action(
        "update_community_package", http_method="PATCH", http_path="/community-packages/{name}"
    )
    async def update_community_package(
        self, params: UpdateCommunityPackageInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PATCH", "/community-packages/{name}", params, trace_id)

    @nw_action(
        "uninstall_community_package", http_method="DELETE", http_path="/community-packages/{name}"
    )
    async def uninstall_community_package(
        self, params: UninstallCommunityPackageInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("DELETE", "/community-packages/{name}", params, trace_id)

    # ---- Source control ------------------------------------------------
    @nw_action("get_source_control_status", http_method="GET", http_path="/source-control/status")
    async def get_source_control_status(
        self, params: GetSourceControlStatusInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/source-control/status", params, trace_id)

    @nw_action("push_source_control", http_method="POST", http_path="/source-control/push")
    async def push_source_control(
        self, params: PushSourceControlInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/source-control/push", params, trace_id)

    @nw_action("pull_source_control", http_method="POST", http_path="/source-control/pull")
    async def pull_source_control(
        self, params: PullSourceControlInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/source-control/pull", params, trace_id, empty_body=True)

    # ---- LDAP ----------------------------------------------------------
    @nw_action("get_ldap_configuration", http_method="GET", http_path="/settings/ldap")
    async def get_ldap_configuration(
        self, params: GetLdapConfigurationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/settings/ldap", params, trace_id)

    @nw_action("update_ldap_configuration", http_method="PUT", http_path="/settings/ldap")
    async def update_ldap_configuration(
        self, params: UpdateLdapConfigurationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/settings/ldap", params, trace_id)

    @nw_action("get_ldap_sync", http_method="GET", http_path="/settings/ldap/sync")
    async def get_ldap_sync(self, params: GetLdapSyncInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("GET", "/settings/ldap/sync", params, trace_id)

    @nw_action("run_ldap_sync", http_method="POST", http_path="/settings/ldap/sync")
    async def run_ldap_sync(self, params: RunLdapSyncInput, *, trace_id: str) -> RestResponseOutput:
        return await self._call("POST", "/settings/ldap/sync", params, trace_id)

    # ---- SAML ----------------------------------------------------------
    @nw_action("get_saml_configuration", http_method="GET", http_path="/settings/sso/saml")
    async def get_saml_configuration(
        self, params: GetSamlConfigurationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/settings/sso/saml", params, trace_id)

    @nw_action("update_saml_configuration", http_method="PUT", http_path="/settings/sso/saml")
    async def update_saml_configuration(
        self, params: UpdateSamlConfigurationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/settings/sso/saml", params, trace_id)

    # ---- OIDC ----------------------------------------------------------
    @nw_action("get_oidc_configuration", http_method="GET", http_path="/settings/sso/oidc")
    async def get_oidc_configuration(
        self, params: GetOidcConfigurationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/settings/sso/oidc", params, trace_id)

    @nw_action("set_oidc_configuration", http_method="PUT", http_path="/settings/sso/oidc")
    async def set_oidc_configuration(
        self, params: SetOidcConfigurationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/settings/sso/oidc", params, trace_id)

    # ---- OTel ----------------------------------------------------------
    @nw_action("get_otel_settings", http_method="GET", http_path="/settings/otel")
    async def get_otel_settings(
        self, params: GetOtelSettingsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/settings/otel", params, trace_id)

    @nw_action("update_otel_settings", http_method="PUT", http_path="/settings/otel")
    async def update_otel_settings(
        self, params: UpdateOtelSettingsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/settings/otel", params, trace_id)

    @nw_action("test_otel_trace", http_method="POST", http_path="/settings/otel/test-trace")
    async def test_otel_trace(
        self, params: TestOtelTraceInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/settings/otel/test-trace", params, trace_id)

    # ---- Rate limits ---------------------------------------------------
    @nw_action("get_rate_limit_settings", http_method="GET", http_path="/settings/rate-limits")
    async def get_rate_limit_settings(
        self, params: GetRateLimitSettingsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/settings/rate-limits", params, trace_id)

    @nw_action("update_rate_limit_settings", http_method="PUT", http_path="/settings/rate-limits")
    async def update_rate_limit_settings(
        self, params: UpdateRateLimitSettingsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/settings/rate-limits", params, trace_id)

    # ---- Security policy -----------------------------------------------
    @nw_action("get_security_policy", http_method="GET", http_path="/settings/security-policy")
    async def get_security_policy(
        self, params: GetSecurityPolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/settings/security-policy", params, trace_id)

    @nw_action("update_security_policy", http_method="PUT", http_path="/settings/security-policy")
    async def update_security_policy(
        self, params: UpdateSecurityPolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/settings/security-policy", params, trace_id)

    # ---- Log streaming -------------------------------------------------
    @nw_action(
        "list_log_streaming_event_types",
        http_method="GET",
        http_path="/settings/log-streaming/event-types",
    )
    async def list_log_streaming_event_types(
        self, params: ListLogStreamingEventTypesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/settings/log-streaming/event-types", params, trace_id)

    @nw_action(
        "list_log_streaming_destinations",
        http_method="GET",
        http_path="/settings/log-streaming/destinations",
    )
    async def list_log_streaming_destinations(
        self, params: ListLogStreamingDestinationsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/settings/log-streaming/destinations", params, trace_id)

    @nw_action(
        "create_log_streaming_destination",
        http_method="POST",
        http_path="/settings/log-streaming/destinations",
    )
    async def create_log_streaming_destination(
        self, params: CreateLogStreamingDestinationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/settings/log-streaming/destinations", params, trace_id)

    @nw_action(
        "get_log_streaming_destination",
        http_method="GET",
        http_path="/settings/log-streaming/destinations/{destinationId}",
    )
    async def get_log_streaming_destination(
        self, params: GetLogStreamingDestinationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/settings/log-streaming/destinations/{destination_id}", params, trace_id
        )

    @nw_action(
        "update_log_streaming_destination",
        http_method="PUT",
        http_path="/settings/log-streaming/destinations/{destinationId}",
    )
    async def update_log_streaming_destination(
        self, params: UpdateLogStreamingDestinationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PUT", "/settings/log-streaming/destinations/{destination_id}", params, trace_id
        )

    @nw_action(
        "delete_log_streaming_destination",
        http_method="DELETE",
        http_path="/settings/log-streaming/destinations/{destinationId}",
    )
    async def delete_log_streaming_destination(
        self, params: DeleteLogStreamingDestinationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/settings/log-streaming/destinations/{destination_id}", params, trace_id
        )

    @nw_action(
        "test_log_streaming_destination",
        http_method="POST",
        http_path="/settings/log-streaming/destinations/{destinationId}/test",
    )
    async def test_log_streaming_destination(
        self, params: TestLogStreamingDestinationInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST", "/settings/log-streaming/destinations/{destination_id}/test", params, trace_id
        )

    # ---- Credential type policy ----------------------------------------
    @nw_action(
        "get_credential_type_instance_policy",
        http_method="GET",
        http_path="/credential-type-policies/instance",
    )
    async def get_credential_type_instance_policy(
        self, params: GetCredentialTypeInstancePolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/credential-type-policies/instance", params, trace_id)

    @nw_action(
        "update_credential_type_instance_policy",
        http_method="PUT",
        http_path="/credential-type-policies/instance",
    )
    async def update_credential_type_instance_policy(
        self, params: UpdateCredentialTypeInstancePolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/credential-type-policies/instance", params, trace_id)

    @nw_action(
        "get_credential_type_project_policy",
        http_method="GET",
        http_path="/credential-type-policies/projects/{projectId}",
    )
    async def get_credential_type_project_policy(
        self, params: GetCredentialTypeProjectPolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/credential-type-policies/projects/{project_id}", params, trace_id
        )

    @nw_action(
        "update_credential_type_project_policy",
        http_method="PUT",
        http_path="/credential-type-policies/projects/{projectId}",
    )
    async def update_credential_type_project_policy(
        self, params: UpdateCredentialTypeProjectPolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PUT", "/credential-type-policies/projects/{project_id}", params, trace_id
        )

    @nw_action(
        "list_credential_type_policy_documents",
        http_method="GET",
        http_path="/credential-type-policies/policies",
    )
    async def list_credential_type_policy_documents(
        self, params: ListCredentialTypePolicyDocumentsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/credential-type-policies/policies", params, trace_id)

    @nw_action(
        "create_credential_type_policy_document",
        http_method="POST",
        http_path="/credential-type-policies/policies",
    )
    async def create_credential_type_policy_document(
        self, params: CreateCredentialTypePolicyDocumentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/credential-type-policies/policies", params, trace_id)

    @nw_action(
        "get_credential_type_policy_document",
        http_method="GET",
        http_path="/credential-type-policies/policies/{policyId}",
    )
    async def get_credential_type_policy_document(
        self, params: GetCredentialTypePolicyDocumentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/credential-type-policies/policies/{policy_id}", params, trace_id
        )

    @nw_action(
        "update_credential_type_policy_document",
        http_method="PUT",
        http_path="/credential-type-policies/policies/{policyId}",
    )
    async def update_credential_type_policy_document(
        self, params: UpdateCredentialTypePolicyDocumentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PUT", "/credential-type-policies/policies/{policy_id}", params, trace_id
        )

    @nw_action(
        "delete_credential_type_policy_document",
        http_method="DELETE",
        http_path="/credential-type-policies/policies/{policyId}",
    )
    async def delete_credential_type_policy_document(
        self, params: DeleteCredentialTypePolicyDocumentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/credential-type-policies/policies/{policy_id}", params, trace_id
        )

    @nw_action(
        "list_credential_type_policy_attachments",
        http_method="GET",
        http_path="/credential-type-policies/scopes/{scopeId}/attachments",
    )
    async def list_credential_type_policy_attachments(
        self, params: ListCredentialTypePolicyAttachmentsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/credential-type-policies/scopes/{scope_id}/attachments", params, trace_id
        )

    @nw_action(
        "replace_credential_type_policy_attachments",
        http_method="PUT",
        http_path="/credential-type-policies/scopes/{scopeId}/attachments",
    )
    async def replace_credential_type_policy_attachments(
        self, params: ReplaceCredentialTypePolicyAttachmentsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PUT", "/credential-type-policies/scopes/{scope_id}/attachments", params, trace_id
        )

    # ---- Node type policy ----------------------------------------------
    @nw_action(
        "get_node_type_instance_policy", http_method="GET", http_path="/node-type-policies/instance"
    )
    async def get_node_type_instance_policy(
        self, params: GetNodeTypeInstancePolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/node-type-policies/instance", params, trace_id)

    @nw_action(
        "update_node_type_instance_policy",
        http_method="PUT",
        http_path="/node-type-policies/instance",
    )
    async def update_node_type_instance_policy(
        self, params: UpdateNodeTypeInstancePolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/node-type-policies/instance", params, trace_id)

    @nw_action(
        "get_node_type_project_policy",
        http_method="GET",
        http_path="/node-type-policies/projects/{projectId}",
    )
    async def get_node_type_project_policy(
        self, params: GetNodeTypeProjectPolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/node-type-policies/projects/{project_id}", params, trace_id
        )

    @nw_action(
        "update_node_type_project_policy",
        http_method="PUT",
        http_path="/node-type-policies/projects/{projectId}",
    )
    async def update_node_type_project_policy(
        self, params: UpdateNodeTypeProjectPolicyInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PUT", "/node-type-policies/projects/{project_id}", params, trace_id
        )

    @nw_action(
        "list_node_type_policy_documents",
        http_method="GET",
        http_path="/node-type-policies/policies",
    )
    async def list_node_type_policy_documents(
        self, params: ListNodeTypePolicyDocumentsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/node-type-policies/policies", params, trace_id)

    @nw_action(
        "create_node_type_policy_document",
        http_method="POST",
        http_path="/node-type-policies/policies",
    )
    async def create_node_type_policy_document(
        self, params: CreateNodeTypePolicyDocumentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/node-type-policies/policies", params, trace_id)

    @nw_action(
        "get_node_type_policy_document",
        http_method="GET",
        http_path="/node-type-policies/policies/{policyId}",
    )
    async def get_node_type_policy_document(
        self, params: GetNodeTypePolicyDocumentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/node-type-policies/policies/{policy_id}", params, trace_id)

    @nw_action(
        "update_node_type_policy_document",
        http_method="PUT",
        http_path="/node-type-policies/policies/{policyId}",
    )
    async def update_node_type_policy_document(
        self, params: UpdateNodeTypePolicyDocumentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("PUT", "/node-type-policies/policies/{policy_id}", params, trace_id)

    @nw_action(
        "delete_node_type_policy_document",
        http_method="DELETE",
        http_path="/node-type-policies/policies/{policyId}",
    )
    async def delete_node_type_policy_document(
        self, params: DeleteNodeTypePolicyDocumentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/node-type-policies/policies/{policy_id}", params, trace_id
        )

    @nw_action(
        "list_node_type_policy_attachments",
        http_method="GET",
        http_path="/node-type-policies/scopes/{scopeId}/attachments",
    )
    async def list_node_type_policy_attachments(
        self, params: ListNodeTypePolicyAttachmentsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/node-type-policies/scopes/{scope_id}/attachments", params, trace_id
        )

    @nw_action(
        "replace_node_type_policy_attachments",
        http_method="PUT",
        http_path="/node-type-policies/scopes/{scopeId}/attachments",
    )
    async def replace_node_type_policy_attachments(
        self, params: ReplaceNodeTypePolicyAttachmentsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PUT", "/node-type-policies/scopes/{scope_id}/attachments", params, trace_id
        )

    # ---- Promotions ----------------------------------------------------
    @nw_action("create_promotion_provider", http_method="POST", http_path="/promotions/providers")
    async def create_promotion_provider(
        self, params: CreatePromotionProviderInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/promotions/providers", params, trace_id)

    @nw_action("list_promotion_providers", http_method="GET", http_path="/promotions/providers")
    async def list_promotion_providers(
        self, params: ListPromotionProvidersInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/promotions/providers", params, trace_id)

    @nw_action(
        "get_promotion_provider",
        http_method="GET",
        http_path="/promotions/providers/{promotionProviderId}",
    )
    async def get_promotion_provider(
        self, params: GetPromotionProviderInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/promotions/providers/{promotion_provider_id}", params, trace_id
        )

    @nw_action(
        "update_promotion_provider",
        http_method="PUT",
        http_path="/promotions/providers/{promotionProviderId}",
    )
    async def update_promotion_provider(
        self, params: UpdatePromotionProviderInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PUT",
            "/promotions/providers/{promotion_provider_id}",
            params,
            trace_id,
            empty_body=True,
        )

    @nw_action(
        "delete_promotion_provider",
        http_method="DELETE",
        http_path="/promotions/providers/{promotionProviderId}",
    )
    async def delete_promotion_provider(
        self, params: DeletePromotionProviderInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/promotions/providers/{promotion_provider_id}", params, trace_id
        )

    @nw_action(
        "list_promotion_provider_repositories",
        http_method="GET",
        http_path="/promotions/providers/{promotionProviderId}/repositories",
    )
    async def list_promotion_provider_repositories(
        self, params: ListPromotionProviderRepositoriesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/promotions/providers/{promotion_provider_id}/repositories", params, trace_id
        )

    @nw_action(
        "list_promotion_provider_repository_branches",
        http_method="GET",
        http_path="/promotions/providers/{promotionProviderId}/repositories/{repositoryId}/branches",
    )
    async def list_promotion_provider_repository_branches(
        self, params: ListPromotionProviderRepositoryBranchesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET",
            "/promotions/providers/{promotion_provider_id}/repositories/{repository_id}/branches",
            params,
            trace_id,
        )

    @nw_action(
        "create_promotion_environment", http_method="POST", http_path="/promotions/environments"
    )
    async def create_promotion_environment(
        self, params: CreatePromotionEnvironmentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/promotions/environments", params, trace_id)

    @nw_action(
        "list_promotion_environments", http_method="GET", http_path="/promotions/environments"
    )
    async def list_promotion_environments(
        self, params: ListPromotionEnvironmentsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("GET", "/promotions/environments", params, trace_id)

    @nw_action(
        "get_promotion_environment",
        http_method="GET",
        http_path="/promotions/environments/{promotionEnvironmentId}",
    )
    async def get_promotion_environment(
        self, params: GetPromotionEnvironmentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/promotions/environments/{promotion_environment_id}", params, trace_id
        )

    @nw_action(
        "update_promotion_environment",
        http_method="PUT",
        http_path="/promotions/environments/{promotionEnvironmentId}",
    )
    async def update_promotion_environment(
        self, params: UpdatePromotionEnvironmentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "PUT",
            "/promotions/environments/{promotion_environment_id}",
            params,
            trace_id,
            empty_body=True,
        )

    @nw_action(
        "delete_promotion_environment",
        http_method="DELETE",
        http_path="/promotions/environments/{promotionEnvironmentId}",
    )
    async def delete_promotion_environment(
        self, params: DeletePromotionEnvironmentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE", "/promotions/environments/{promotion_environment_id}", params, trace_id
        )

    @nw_action(
        "connect_promotion_checkout",
        http_method="POST",
        http_path="/promotions/environments/{promotionEnvironmentId}/connect",
    )
    async def connect_promotion_checkout(
        self, params: ConnectPromotionCheckoutInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST", "/promotions/environments/{promotion_environment_id}/connect", params, trace_id
        )

    @nw_action(
        "disconnect_promotion_checkout",
        http_method="POST",
        http_path="/promotions/environments/{promotionEnvironmentId}/disconnect",
    )
    async def disconnect_promotion_checkout(
        self, params: DisconnectPromotionCheckoutInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST",
            "/promotions/environments/{promotion_environment_id}/disconnect",
            params,
            trace_id,
        )

    @nw_action(
        "list_promotion_environment_projects",
        http_method="GET",
        http_path="/promotions/environments/{promotionEnvironmentId}/projects",
    )
    async def list_promotion_environment_projects(
        self, params: ListPromotionEnvironmentProjectsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/promotions/environments/{promotion_environment_id}/projects", params, trace_id
        )

    @nw_action(
        "list_promotion_environment_missing_projects",
        http_method="GET",
        http_path="/promotions/environments/{promotionEnvironmentId}/missing-projects",
    )
    async def list_promotion_environment_missing_projects(
        self, params: ListPromotionEnvironmentMissingProjectsInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET",
            "/promotions/environments/{promotion_environment_id}/missing-projects",
            params,
            trace_id,
        )

    @nw_action(
        "add_project_to_promotion_environment",
        http_method="POST",
        http_path="/promotions/environments/{promotionEnvironmentId}/projects/{projectId}",
    )
    async def add_project_to_promotion_environment(
        self, params: AddProjectToPromotionEnvironmentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST",
            "/promotions/environments/{promotion_environment_id}/projects/{project_id}",
            params,
            trace_id,
        )

    @nw_action(
        "remove_project_from_promotion_environment",
        http_method="DELETE",
        http_path="/promotions/environments/{promotionEnvironmentId}/projects/{projectId}",
    )
    async def remove_project_from_promotion_environment(
        self, params: RemoveProjectFromPromotionEnvironmentInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "DELETE",
            "/promotions/environments/{promotion_environment_id}/projects/{project_id}",
            params,
            trace_id,
        )

    @nw_action(
        "promote_package",
        http_method="POST",
        http_path="/promotions/environments/{promotionEnvironmentId}/promote",
    )
    async def promote_package(
        self, params: PromotePackageInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST", "/promotions/environments/{promotion_environment_id}/promote", params, trace_id
        )

    @nw_action(
        "apply_package",
        http_method="POST",
        http_path="/promotions/environments/{promotionEnvironmentId}/apply",
    )
    async def apply_package(
        self, params: ApplyPackageInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST", "/promotions/environments/{promotion_environment_id}/apply", params, trace_id
        )

    @nw_action(
        "continue_apply_package",
        http_method="POST",
        http_path="/promotions/environments/{promotionEnvironmentId}/apply/continue",
    )
    async def continue_apply_package(
        self, params: ContinueApplyPackageInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST",
            "/promotions/environments/{promotion_environment_id}/apply/continue",
            params,
            trace_id,
        )

    @nw_action(
        "get_promotion_changes",
        http_method="GET",
        http_path="/promotions/projects/{projectId}/changes/{direction}",
    )
    async def get_promotion_changes(
        self, params: GetPromotionChangesInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "GET", "/promotions/projects/{project_id}/changes/{direction}", params, trace_id
        )

    @nw_action(
        "promote_project_selection",
        http_method="POST",
        http_path="/promotions/projects/{projectId}/promote",
    )
    async def promote_project_selection(
        self, params: PromoteProjectSelectionInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST", "/promotions/projects/{project_id}/promote", params, trace_id
        )

    @nw_action(
        "apply_project_selection",
        http_method="POST",
        http_path="/promotions/projects/{projectId}/apply",
    )
    async def apply_project_selection(
        self, params: ApplyProjectSelectionInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/promotions/projects/{project_id}/apply", params, trace_id)

    @nw_action(
        "continue_apply_project_selection",
        http_method="POST",
        http_path="/promotions/projects/{projectId}/apply/continue",
    )
    async def continue_apply_project_selection(
        self, params: ContinueApplyProjectSelectionInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call(
            "POST", "/promotions/projects/{project_id}/apply/continue", params, trace_id
        )

    # ---- n8n packages --------------------------------------------------
    @nw_action("import_package", http_method="POST", http_path="/n8n-packages/import")
    async def import_package(
        self, params: ImportPackageInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/n8n-packages/import", params, trace_id)

    @nw_action(
        "import_package_selection", http_method="POST", http_path="/n8n-packages/import-selection"
    )
    async def import_package_selection(
        self, params: ImportPackageSelectionInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/n8n-packages/import-selection", params, trace_id)

    @nw_action("export_package", http_method="POST", http_path="/n8n-packages/export")
    async def export_package(
        self, params: ExportPackageInput, *, trace_id: str
    ) -> RestResponseOutput:
        return await self._call("POST", "/n8n-packages/export", params, trace_id, empty_body=True)


# Tenant-secret contract for the config store: one API key per n8n config.
declare_secret_shape(
    "n8n",
    required=["N8N_API_KEY"],
    formats={"N8N_API_KEY": "opaque_secret"},  # nosec B105
)
