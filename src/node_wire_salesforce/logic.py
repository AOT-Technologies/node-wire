#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
# Version details
#   Salesforce: REST API and Bulk API 2.0, API version v68.0 (Winter '27), set with `api_version` in
#     connectors.yaml. Source: the REST API Developer Guide and the Bulk API 2.0 guide, v68.0.
#   Auth: the OAuth 2.0 refresh-token provider (`oauth2`, refresh_token) supplies the access token.
#   Instance URL: the `salesforce_instance_url` secret. It is never an action input.
from __future__ import annotations

import base64
import contextlib
from collections.abc import AsyncIterator
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple, Type, ClassVar
from urllib.parse import quote, urlsplit
import httpx

from node_wire_runtime import BaseConnector, RestResponseOutput, nw_action, sdk_action
from node_wire_runtime.models import ErrorCategory
from node_wire_runtime.rest import _shared_client
from .normalizers import (
    normalize_salesforce_read_delete_contact,
    normalize_salesforce_read_delete_lead,
    normalize_salesforce_update_contact,
    normalize_salesforce_update_lead,
)
from .schema import (
    AbortIngestJobInput,
    AbortQueryJobInput,
    CompileConsentInput,
    CompileMultiConsentInput,
    CompleteIngestJobInput,
    CompositeBatchInput,
    CompositeGraphInput,
    CompositeInput,
    CreateContactInput,
    CreateIngestJobInput,
    CreateLeadInput,
    CreateProductSchedulesInput,
    CreateQueryJobInput,
    CreateRecordInput,
    CreateRecordTreeInput,
    CreateRecordWithQuickActionInput,
    CreateRecordsInput,
    DeleteContactInput,
    DeleteIngestJobInput,
    DeleteLeadInput,
    DeleteProductSchedulesInput,
    DeleteQueryJobInput,
    DeleteRecordByExternalIdInput,
    DeleteRecordInput,
    DeleteRecordsInput,
    DeleteRelatedRecordInput,
    DeleteSurveyFieldTranslationInput,
    DeleteSurveyFieldTranslationsInput,
    DeployDataKitComponentsInput,
    DescribeGlobalInput,
    DescribeListViewInput,
    DescribeSobjectInput,
    DescribeSobjectQuickActionInput,
    GetAppMenuItemsInput,
    GetAppMenuTypesInput,
    GetAppointmentCandidatesInput,
    GetAppointmentSlotsInput,
    GetApprovalLayoutInput,
    GetApprovalLayoutsInput,
    GetBlobFieldInput,
    GetCompactLayoutsForObjectsInput,
    GetCompactLayoutsInput,
    GetCompositeResourcesInput,
    GetDataCategoryDetailInput,
    GetDeletedRecordsInput,
    GetEmbeddedServiceConfigInput,
    GetEventSchemaByIdInput,
    GetEventSchemaByNameInput,
    GetGlobalPublisherLayoutsInput,
    GetIngestFailedResultsInput,
    GetIngestJobInput,
    GetIngestSuccessfulResultsInput,
    GetIngestUnprocessedRecordsInput,
    GetKnowledgeArticleInput,
    GetKnowledgeLanguageSettingsInput,
    GetLayoutsForRecordTypeInput,
    GetLayoutsInput,
    GetLightningExitByPageInput,
    GetLightningToggleMetricsInput,
    GetLightningUsageByAppTypeInput,
    GetLightningUsageByBrowserInput,
    GetLightningUsageByFlexipageInput,
    GetLightningUsageByPageInput,
    GetLimitsInput,
    GetListViewInput,
    GetListViewResultsInput,
    GetMobileAppMenuItemsInput,
    GetNamedLayoutInput,
    GetPlatformActionInput,
    GetPortabilityStatusInput,
    GetProcessRuleInput,
    GetProductSchedulesInput,
    GetQueryJobInput,
    GetQueryJobResultPagesInput,
    GetQueryJobResultsInput,
    GetQuickActionDefaultValuesForRecordInput,
    GetQuickActionDefaultValuesInput,
    GetRecentItemsInput,
    GetRecentListViewsInput,
    GetRecordByExternalIdInput,
    GetRecordCountsInput,
    GetRecordInput,
    GetRecordsInput,
    GetRecordsWithBodyInput,
    GetRelatedRecordsInput,
    GetRelevantItemsInput,
    GetRichTextImageInput,
    GetSchedulingResourcesInput,
    GetSearchResultLayoutsInput,
    GetSearchScopeOrderInput,
    GetSelfServiceUserPasswordStatusInput,
    GetSobjectInfoInput,
    GetSobjectQuickActionInput,
    GetSuggestedArticlesForRecordInput,
    GetSuggestedArticlesInput,
    GetSupportResourcesInput,
    GetSurveyFieldTranslationInput,
    GetSurveyFieldTranslationsInput,
    GetTabsInput,
    GetThemeInput,
    GetUpdatedRecordsInput,
    GetUserPasswordStatusInput,
    InvokeSobjectQuickActionInput,
    ListApiVersionsInput,
    ListCustomInvocableActionsInput,
    ListDataCategoryGroupsInput,
    ListIngestJobsInput,
    ListInvocableActionsInput,
    ListKnowledgeArticlesInput,
    ListListViewsInput,
    ListProcessApprovalsInput,
    ListProcessRulesForSobjectInput,
    ListProcessRulesInput,
    ListQueryJobsInput,
    ListQuickActionsInput,
    ListResourcesInput,
    ListSobjectQuickActionsInput,
    ListStandardInvocableActionsInput,
    ParameterizedSearchGetInput,
    ParameterizedSearchPostInput,
    QueryAllInput,
    QueryAllMoreInput,
    QueryInput,
    QueryMoreInput,
    ReadContactInput,
    ReadLeadInput,
    RequestPortabilityDataInput,
    ResetSelfServiceUserPasswordInput,
    ResetUserPasswordInput,
    RunNamedQueryInput,
    SalesforceOperationOutput,
    SearchInput,
    SearchSuggestedQueriesInput,
    SearchSuggestionsInput,
    SearchTitleMatchesInput,
    SetSelfServiceUserPasswordInput,
    SetSurveyFieldTranslationInput,
    SetSurveyFieldTranslationsInput,
    SetUserPasswordInput,
    SubmitProcessApprovalInput,
    TriggerProcessRuleInput,
    TriggerProcessRulesInput,
    UpdateContactInput,
    UpdateLeadInput,
    UpdateRecordInput,
    UpdateRecordsInput,
    UpdateRelatedRecordInput,
    UploadIngestJobDataInput,
    UpsertRecordByExternalIdInput,
    UpsertRecordsInput,
    WriteConsentInput,
)


@contextlib.asynccontextmanager
async def _http_client(*, trust_env: bool = True) -> AsyncIterator[httpx.AsyncClient]:
    """The runtime's pooled keep-alive client; pass ``timeout=`` on every request (it has none)."""
    yield _shared_client(trust_env)


logger = logging.getLogger("connectors.salesforce")


class SalesforceTransientError(httpx.HTTPStatusError):
    """Exception for transient Salesforce errors that should be retried."""

    pass


# The error map works per exception class, so the answers that need their own category get a small
# subclass; `_send` picks it by status.
class SalesforceAuthError(httpx.HTTPStatusError):
    """401: the access token was rejected."""


class SalesforceRateLimited(httpx.HTTPStatusError):
    """429: Salesforce refused the call, so a retry is safe."""


DEFAULT_API_VERSION = "v68.0"


def _error_detail(response: httpx.Response) -> str:
    """Salesforce's own errorCode and message, at most 3 of them and 200 characters each.

    The raw body is never copied: it can echo field values (duplicate-record details, emails).
    """
    try:
        errors = response.json()
    except ValueError:
        return ""
    if isinstance(errors, dict):
        errors = [errors]
    if not isinstance(errors, list):
        return ""
    return "; ".join(
        f"{e.get('errorCode')}: {str(e.get('message', ''))[:200]}"
        for e in errors[:3]
        if isinstance(e, dict) and e.get("errorCode")
    )


def _query_value(value: Any) -> str:
    """A query-string value: lists are comma separated, booleans lower case."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value)


class SalesforceConnector(BaseConnector):
    """Salesforce connector: leads and contacts, plus the REST API and Bulk API 2.0."""

    connector_id = "salesforce"
    action = "execute"  # Multi-action dispatcher
    output_model = SalesforceOperationOutput

    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {
        httpx.ConnectError: (ErrorCategory.RETRYABLE, "SALESFORCE_CONNECT_ERROR"),
        httpx.TimeoutException: (ErrorCategory.RETRYABLE, "SALESFORCE_TIMEOUT"),
        SalesforceTransientError: (ErrorCategory.RETRYABLE, "SALESFORCE_TRANSIENT_ERROR"),
        SalesforceAuthError: (ErrorCategory.AUTH, "SALESFORCE_AUTH_ERROR"),
        SalesforceRateLimited: (ErrorCategory.RETRYABLE, "SALESFORCE_RATE_LIMITED"),
        httpx.HTTPStatusError: (ErrorCategory.BUSINESS, "SALESFORCE_API_ERROR"),
    }

    def _get_base_url(self) -> str:
        return self.secret_provider.get_secret("salesforce_instance_url").rstrip("/")

    def _get_api_version(self) -> str:
        version = str(self.config.get("api_version") or DEFAULT_API_VERSION).strip()
        if not re.fullmatch(r"v\d{2}\.0", version):
            raise ValueError(f"Salesforce api_version must look like v68.0, got {version!r}")
        return version

    @nw_action("create_lead", http_method="POST", http_path="/sobjects/Lead")
    async def create_lead(
        self, params: CreateLeadInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._record(
            "POST", "Lead", params.model_dump(by_alias=True, exclude={"action"}), trace_id
        )

    @sdk_action(
        "read_lead",
        http_method="GET",
        http_path="/sobjects/Lead/{record_id}",
        alias_tolerant=True,
        mcp_normalize=normalize_salesforce_read_delete_lead,
    )
    async def read_lead(self, params: ReadLeadInput, *, trace_id: str) -> SalesforceOperationOutput:
        return await self._record("GET", f"Lead/{params.record_id}", None, trace_id)

    # PATCH sets the given fields to the given values: repeating it changes nothing more, so a
    # timeout is retried. create_* (POST) is not: see OUTCOME_UNKNOWN in docs/errors.md.
    @sdk_action(
        "update_lead",
        http_method="PATCH",
        http_path="/sobjects/Lead/{record_id}",
        alias_tolerant=True,
        mcp_normalize=normalize_salesforce_update_lead,
        idempotent=True,
    )
    async def update_lead(
        self, params: UpdateLeadInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._record("PATCH", f"Lead/{params.record_id}", params.fields, trace_id)

    @sdk_action(
        "delete_lead",
        http_method="DELETE",
        http_path="/sobjects/Lead/{record_id}",
        alias_tolerant=True,
        mcp_normalize=normalize_salesforce_read_delete_lead,
    )
    async def delete_lead(
        self, params: DeleteLeadInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._record("DELETE", f"Lead/{params.record_id}", None, trace_id)

    @nw_action("create_contact", http_method="POST", http_path="/sobjects/Contact")
    async def create_contact(
        self, params: CreateContactInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._record(
            "POST", "Contact", params.model_dump(by_alias=True, exclude={"action"}), trace_id
        )

    @sdk_action(
        "read_contact",
        http_method="GET",
        http_path="/sobjects/Contact/{record_id}",
        alias_tolerant=True,
        mcp_normalize=normalize_salesforce_read_delete_contact,
    )
    async def read_contact(
        self, params: ReadContactInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._record("GET", f"Contact/{params.record_id}", None, trace_id)

    @sdk_action(
        "update_contact",
        http_method="PATCH",
        http_path="/sobjects/Contact/{record_id}",
        alias_tolerant=True,
        mcp_normalize=normalize_salesforce_update_contact,
        idempotent=True,
    )
    async def update_contact(
        self, params: UpdateContactInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._record("PATCH", f"Contact/{params.record_id}", params.fields, trace_id)

    @sdk_action(
        "delete_contact",
        http_method="DELETE",
        http_path="/sobjects/Contact/{record_id}",
        alias_tolerant=True,
        mcp_normalize=normalize_salesforce_read_delete_contact,
    )
    async def delete_contact(
        self, params: DeleteContactInput, *, trace_id: str
    ) -> SalesforceOperationOutput:
        return await self._record("DELETE", f"Contact/{params.record_id}", None, trace_id)

    async def _call(
        self,
        method: str,
        path: str,
        params: Any,
        trace_id: str,
        *,
        versioned: bool = True,
        body: Any = None,
        accept: str = "application/json",
    ) -> RestResponseOutput:
        """Send one API call. Fields named in ``path`` go in the path; the rest go to the query
        (GET, DELETE) or the JSON body (other methods), unless a field says ``nw_in``: ``header``,
        ``query``, ``query_map`` (a map expanded into the query), ``body`` (the whole JSON body) or
        ``raw`` (the whole body as CSV text). ``body`` is a fixed body for actions that send one."""
        query: Dict[str, str] = {}
        headers: Dict[str, str] = {"Accept": accept}
        fields = type(params).model_fields
        values = params.model_dump(mode="json", exclude_unset=True, exclude={"action"})
        body_fields: Dict[str, Any] = {}
        whole: Any = None
        raw: Optional[str] = None
        for name, value in values.items():
            wire = fields[name].serialization_alias or name
            where = (fields[name].json_schema_extra or {}).get("nw_in")
            if "{" + name + "}" in path:
                path = path.replace("{" + name + "}", quote(str(value), safe=""))
            elif where == "header":
                headers[wire] = str(value)
            elif where == "body":
                whole = value
            elif where == "raw":
                raw = value
            elif where == "query_map":
                query.update({k: _query_value(v) for k, v in value.items()})
            elif where == "query" or (where is None and method in ("GET", "DELETE")):
                query[wire] = _query_value(value)
            else:
                body_fields[wire] = value
        if whole is not None:
            body = whole
        elif body_fields:
            body = body_fields
        prefix = f"/services/data/{self._get_api_version()}" if versioned else ""
        url = f"{self._get_base_url()}{prefix}{path}"
        return await self._send(
            method, url, trace_id, headers=headers, query=query, body=body, raw=raw
        )

    async def _send(
        self,
        method: str,
        url: str,
        trace_id: str,
        *,
        headers: Dict[str, str],
        query: Dict[str, str],
        body: Any,
        raw: Optional[str],
    ) -> RestResponseOutput:
        # Only the method and the URL path are logged, never headers, query values or bodies.
        logger.info(
            "Calling Salesforce",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "method": method,
                "path": urlsplit(url).path,
            },
        )
        # The OAuth provider's default headers are FHIR media types (application/fhir+json), which
        # Salesforce refuses with a 415. Drop its Content-Type and Accept, and name the right ones.
        send_headers = {
            k: v
            for k, v in (await self.get_auth_headers()).items()
            if k.lower() not in ("content-type", "accept")
        }
        send_headers.update(headers)
        send_headers.setdefault("Accept", "application/json")
        if raw is not None:
            send_headers["Content-Type"] = "text/csv"
        elif body is not None:
            send_headers["Content-Type"] = "application/json"
        async with _http_client() as client:
            response = await client.request(
                method,
                url,
                headers=send_headers,
                params=query or None,
                json=body if raw is None else None,
                content=raw,
                timeout=float(os.getenv("NW_TIMEOUT", "30.0")),
            )
        if response.is_error:
            status = response.status_code
            error: Type[httpx.HTTPStatusError] = httpx.HTTPStatusError
            if status == 429:
                error = SalesforceRateLimited
            elif status == 401:
                error = SalesforceAuthError
            elif status in (502, 503, 504) and method in ("GET", "DELETE"):
                error = SalesforceTransientError  # safe to repeat; a gateway error on a write may have run it
            # Only Salesforce's errorCode and message go in the exception, not the raw body.
            detail = _error_detail(response)
            raise error(
                f"Salesforce answered {status}" + (f": {detail}" if detail else ""),
                request=response.request,
                response=response,
            )
        content: Any = None
        ctype = response.headers.get("content-type", "").lower()
        if response.content:
            if "json" in ctype:
                try:
                    content = response.json()
                except ValueError:
                    content = response.text
            elif any(kind in ctype for kind in ("octet-stream", "image/", "pdf", "zip")):
                # Simplified: a binary answer (a blob field, an image) comes back as base64 text.
                content = base64.b64encode(response.content).decode()
            else:
                content = response.text  # for example the CSV of a Bulk API 2.0 job
        return RestResponseOutput(
            status_code=response.status_code, headers=dict(response.headers), body=content
        )

    async def _record(
        self, method: str, path: str, payload: Optional[Dict[str, Any]], trace_id: str
    ) -> SalesforceOperationOutput:
        """The lead and contact actions: one call on ``sobjects/{path}`` through the same request
        path as every other action, answered in the original output shape."""
        url = f"{self._get_base_url()}/services/data/{self._get_api_version()}/sobjects/{path}"
        body = {k: v for k, v in (payload or {}).items() if v is not None} or None
        response = await self._send(
            method, url, trace_id, headers={}, query={}, body=body, raw=None
        )
        answer = response.body
        data: Dict[str, Any] = (
            answer if isinstance(answer, dict) else ({"text": answer} if answer else {})
        )
        resource_type, _, record_id = path.partition("/")
        resource_id = data.get("id") or data.get("Id") or record_id or None
        return SalesforceOperationOutput(
            success=True, resource_id=resource_id, resource_type=resource_type, data=data
        )

    @nw_action("list_api_versions", http_method="GET", http_path="/services/data")
    async def list_api_versions(
        self, params: ListApiVersionsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the REST API versions the org supports."""
        return await self._call("GET", "/services/data", params, trace_id, versioned=False)

    @nw_action("list_resources", http_method="GET", http_path="/")
    async def list_resources(
        self, params: ListResourcesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the REST resources available in the configured API version."""
        return await self._call("GET", "", params, trace_id)

    @nw_action("get_limits", http_method="GET", http_path="/limits")
    async def get_limits(self, params: GetLimitsInput, *, trace_id: str) -> RestResponseOutput:
        """Get the org's API and feature limits."""
        return await self._call("GET", "/limits", params, trace_id)

    @nw_action("get_record_counts", http_method="GET", http_path="/limits/recordCount")
    async def get_record_counts(
        self, params: GetRecordCountsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the approximate record counts of objects."""
        return await self._call("GET", "/limits/recordCount", params, trace_id)

    @nw_action("describe_global", http_method="GET", http_path="/sobjects")
    async def describe_global(
        self, params: DescribeGlobalInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the objects in the org with basic information."""
        return await self._call("GET", "/sobjects", params, trace_id)

    @nw_action("get_tabs", http_method="GET", http_path="/tabs")
    async def get_tabs(self, params: GetTabsInput, *, trace_id: str) -> RestResponseOutput:
        """List the tabs the user can see."""
        return await self._call("GET", "/tabs", params, trace_id)

    @nw_action("get_theme", http_method="GET", http_path="/theme")
    async def get_theme(self, params: GetThemeInput, *, trace_id: str) -> RestResponseOutput:
        """Get the org's theme (colors and icons by object)."""
        return await self._call("GET", "/theme", params, trace_id)

    @nw_action("get_app_menu_types", http_method="GET", http_path="/appMenu")
    async def get_app_menu_types(
        self, params: GetAppMenuTypesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the app menu types."""
        return await self._call("GET", "/appMenu", params, trace_id)

    @nw_action("get_app_menu_items", http_method="GET", http_path="/appMenu/AppSwitcher")
    async def get_app_menu_items(
        self, params: GetAppMenuItemsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the items of the app switcher menu."""
        return await self._call("GET", "/appMenu/AppSwitcher", params, trace_id)

    @nw_action("get_mobile_app_menu_items", http_method="GET", http_path="/appMenu/Salesforce1")
    async def get_mobile_app_menu_items(
        self, params: GetMobileAppMenuItemsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the items of the Salesforce mobile app menu."""
        return await self._call("GET", "/appMenu/Salesforce1", params, trace_id)

    @nw_action("get_sobject_info", http_method="GET", http_path="/sobjects/{sobject}")
    async def get_sobject_info(
        self, params: GetSobjectInfoInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get basic information about an object and its recent records."""
        return await self._call("GET", "/sobjects/{sobject}", params, trace_id)

    @nw_action("describe_sobject", http_method="GET", http_path="/sobjects/{sobject}/describe")
    async def describe_sobject(
        self, params: DescribeSobjectInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Describe an object: fields, record types, relationships."""
        return await self._call("GET", "/sobjects/{sobject}/describe", params, trace_id)

    @nw_action("get_deleted_records", http_method="GET", http_path="/sobjects/{sobject}/deleted")
    async def get_deleted_records(
        self, params: GetDeletedRecordsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List records of an object deleted in a time range."""
        return await self._call("GET", "/sobjects/{sobject}/deleted", params, trace_id)

    @nw_action("get_updated_records", http_method="GET", http_path="/sobjects/{sobject}/updated")
    async def get_updated_records(
        self, params: GetUpdatedRecordsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List records of an object updated in a time range."""
        return await self._call("GET", "/sobjects/{sobject}/updated", params, trace_id)

    @nw_action(
        "get_named_layout",
        http_method="GET",
        http_path="/sobjects/{sobject}/describe/namedLayouts/{layout_name}",
    )
    async def get_named_layout(
        self, params: GetNamedLayoutInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get a named layout of an object."""
        return await self._call(
            "GET", "/sobjects/{sobject}/describe/namedLayouts/{layout_name}", params, trace_id
        )

    @nw_action("get_layouts", http_method="GET", http_path="/sobjects/{sobject}/describe/layouts")
    async def get_layouts(self, params: GetLayoutsInput, *, trace_id: str) -> RestResponseOutput:
        """Get the page layouts of an object."""
        return await self._call("GET", "/sobjects/{sobject}/describe/layouts", params, trace_id)

    @nw_action(
        "get_layouts_for_record_type",
        http_method="GET",
        http_path="/sobjects/{sobject}/describe/layouts/{record_type_id}",
    )
    async def get_layouts_for_record_type(
        self, params: GetLayoutsForRecordTypeInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the layout of an object for one record type."""
        return await self._call(
            "GET", "/sobjects/{sobject}/describe/layouts/{record_type_id}", params, trace_id
        )

    @nw_action(
        "get_global_publisher_layouts",
        http_method="GET",
        http_path="/sobjects/Global/describe/layouts",
    )
    async def get_global_publisher_layouts(
        self, params: GetGlobalPublisherLayoutsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the global publisher layouts."""
        return await self._call("GET", "/sobjects/Global/describe/layouts", params, trace_id)

    @nw_action(
        "get_compact_layouts",
        http_method="GET",
        http_path="/sobjects/{sobject}/describe/compactLayouts",
    )
    async def get_compact_layouts(
        self, params: GetCompactLayoutsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the compact layouts of an object."""
        return await self._call(
            "GET", "/sobjects/{sobject}/describe/compactLayouts", params, trace_id
        )

    @nw_action("get_compact_layouts_for_objects", http_method="GET", http_path="/compactLayouts")
    async def get_compact_layouts_for_objects(
        self, params: GetCompactLayoutsForObjectsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the compact layouts of several objects."""
        return await self._call("GET", "/compactLayouts", params, trace_id)

    @nw_action(
        "get_approval_layouts",
        http_method="GET",
        http_path="/sobjects/{sobject}/describe/approvalLayouts",
    )
    async def get_approval_layouts(
        self, params: GetApprovalLayoutsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the approval layouts of an object."""
        return await self._call(
            "GET", "/sobjects/{sobject}/describe/approvalLayouts", params, trace_id
        )

    @nw_action(
        "get_approval_layout",
        http_method="GET",
        http_path="/sobjects/{sobject}/describe/approvalLayouts/{approval_process_name}",
    )
    async def get_approval_layout(
        self, params: GetApprovalLayoutInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the layout of one approval process of an object."""
        return await self._call(
            "GET",
            "/sobjects/{sobject}/describe/approvalLayouts/{approval_process_name}",
            params,
            trace_id,
        )

    @nw_action("get_platform_action", http_method="GET", http_path="/sobjects/PlatformAction")
    async def get_platform_action(
        self, params: GetPlatformActionInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the platform actions (buttons and links) the user can see."""
        return await self._call("GET", "/sobjects/PlatformAction", params, trace_id)

    @nw_action("create_record", http_method="POST", http_path="/sobjects/{sobject}")
    async def create_record(
        self, params: CreateRecordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Create a record."""
        return await self._call("POST", "/sobjects/{sobject}", params, trace_id)

    @nw_action("get_record", http_method="GET", http_path="/sobjects/{sobject}/{record_id}")
    async def get_record(self, params: GetRecordInput, *, trace_id: str) -> RestResponseOutput:
        """Get a record by Id."""
        return await self._call("GET", "/sobjects/{sobject}/{record_id}", params, trace_id)

    @nw_action("update_record", http_method="PATCH", http_path="/sobjects/{sobject}/{record_id}")
    async def update_record(
        self, params: UpdateRecordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Update a record."""
        return await self._call("PATCH", "/sobjects/{sobject}/{record_id}", params, trace_id)

    @nw_action("delete_record", http_method="DELETE", http_path="/sobjects/{sobject}/{record_id}")
    async def delete_record(
        self, params: DeleteRecordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Delete a record."""
        return await self._call("DELETE", "/sobjects/{sobject}/{record_id}", params, trace_id)

    @nw_action(
        "get_record_by_external_id",
        http_method="GET",
        http_path="/sobjects/{sobject}/{field_name}/{field_value}",
    )
    async def get_record_by_external_id(
        self, params: GetRecordByExternalIdInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get a record by an external Id field."""
        return await self._call(
            "GET", "/sobjects/{sobject}/{field_name}/{field_value}", params, trace_id
        )

    @nw_action(
        "upsert_record_by_external_id",
        http_method="PATCH",
        http_path="/sobjects/{sobject}/{field_name}/{field_value}",
    )
    async def upsert_record_by_external_id(
        self, params: UpsertRecordByExternalIdInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Create or update a record by an external Id field."""
        return await self._call(
            "PATCH", "/sobjects/{sobject}/{field_name}/{field_value}", params, trace_id
        )

    @nw_action(
        "delete_record_by_external_id",
        http_method="DELETE",
        http_path="/sobjects/{sobject}/{field_name}/{field_value}",
    )
    async def delete_record_by_external_id(
        self, params: DeleteRecordByExternalIdInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Delete a record by an external Id field."""
        return await self._call(
            "DELETE", "/sobjects/{sobject}/{field_name}/{field_value}", params, trace_id
        )

    @nw_action(
        "get_blob_field",
        http_method="GET",
        http_path="/sobjects/{sobject}/{record_id}/{blob_field}",
    )
    async def get_blob_field(
        self, params: GetBlobFieldInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the content of a blob field (an attachment or document body). Returned as base64."""
        return await self._call(
            "GET", "/sobjects/{sobject}/{record_id}/{blob_field}", params, trace_id
        )

    @nw_action(
        "get_rich_text_image",
        http_method="GET",
        http_path="/sobjects/{sobject}/{record_id}/richTextImageFields/{field_name}/{content_reference_id}",
    )
    async def get_rich_text_image(
        self, params: GetRichTextImageInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get an image from a rich text field. Returned as base64."""
        return await self._call(
            "GET",
            "/sobjects/{sobject}/{record_id}/richTextImageFields/{field_name}/{content_reference_id}",
            params,
            trace_id,
        )

    @nw_action(
        "get_related_records",
        http_method="GET",
        http_path="/sobjects/{sobject}/{record_id}/{relationship_name}",
    )
    async def get_related_records(
        self, params: GetRelatedRecordsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the records of a relationship of a record."""
        return await self._call(
            "GET", "/sobjects/{sobject}/{record_id}/{relationship_name}", params, trace_id
        )

    @nw_action(
        "update_related_record",
        http_method="PATCH",
        http_path="/sobjects/{sobject}/{record_id}/{relationship_name}",
    )
    async def update_related_record(
        self, params: UpdateRelatedRecordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Update the related record of a relationship."""
        return await self._call(
            "PATCH", "/sobjects/{sobject}/{record_id}/{relationship_name}", params, trace_id
        )

    @nw_action(
        "delete_related_record",
        http_method="DELETE",
        http_path="/sobjects/{sobject}/{record_id}/{relationship_name}",
    )
    async def delete_related_record(
        self, params: DeleteRelatedRecordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Delete the related record of a relationship."""
        return await self._call(
            "DELETE", "/sobjects/{sobject}/{record_id}/{relationship_name}", params, trace_id
        )

    @nw_action("create_records", http_method="POST", http_path="/composite/sobjects")
    async def create_records(
        self, params: CreateRecordsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Create up to 200 records in one call."""
        return await self._call("POST", "/composite/sobjects", params, trace_id)

    @nw_action("get_records", http_method="GET", http_path="/composite/sobjects/{sobject}")
    async def get_records(self, params: GetRecordsInput, *, trace_id: str) -> RestResponseOutput:
        """Get several records of one object by Id."""
        return await self._call("GET", "/composite/sobjects/{sobject}", params, trace_id)

    @nw_action(
        "get_records_with_body", http_method="POST", http_path="/composite/sobjects/{sobject}"
    )
    async def get_records_with_body(
        self, params: GetRecordsWithBodyInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get several records of one object by Id, with the Ids in the body."""
        return await self._call("POST", "/composite/sobjects/{sobject}", params, trace_id)

    @nw_action("update_records", http_method="PATCH", http_path="/composite/sobjects")
    async def update_records(
        self, params: UpdateRecordsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Update up to 200 records in one call."""
        return await self._call("PATCH", "/composite/sobjects", params, trace_id)

    @nw_action(
        "upsert_records",
        http_method="PATCH",
        http_path="/composite/sobjects/{sobject}/{external_id_field}",
    )
    async def upsert_records(
        self, params: UpsertRecordsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Create or update up to 200 records by an external Id field."""
        return await self._call(
            "PATCH", "/composite/sobjects/{sobject}/{external_id_field}", params, trace_id
        )

    @nw_action("delete_records", http_method="DELETE", http_path="/composite/sobjects")
    async def delete_records(
        self, params: DeleteRecordsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Delete up to 200 records by Id."""
        return await self._call("DELETE", "/composite/sobjects", params, trace_id)

    @nw_action("composite", http_method="POST", http_path="/composite")
    async def composite(self, params: CompositeInput, *, trace_id: str) -> RestResponseOutput:
        """Run up to 25 subrequests in one call, each able to use the results of earlier ones."""
        return await self._call("POST", "/composite", params, trace_id)

    @nw_action("get_composite_resources", http_method="GET", http_path="/composite")
    async def get_composite_resources(
        self, params: GetCompositeResourcesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the composite resources."""
        return await self._call("GET", "/composite", params, trace_id)

    @nw_action("composite_graph", http_method="POST", http_path="/composite/graph")
    async def composite_graph(
        self, params: CompositeGraphInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Run one or more graphs of subrequests."""
        return await self._call("POST", "/composite/graph", params, trace_id)

    @nw_action("composite_batch", http_method="POST", http_path="/composite/batch")
    async def composite_batch(
        self, params: CompositeBatchInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Run up to 25 independent subrequests in one call."""
        return await self._call("POST", "/composite/batch", params, trace_id)

    @nw_action("create_record_tree", http_method="POST", http_path="/composite/tree/{sobject}")
    async def create_record_tree(
        self, params: CreateRecordTreeInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Create a tree of records (parents with children) in one call."""
        return await self._call("POST", "/composite/tree/{sobject}", params, trace_id)

    @nw_action("query", http_method="GET", http_path="/query")
    async def query(self, params: QueryInput, *, trace_id: str) -> RestResponseOutput:
        """Run a SOQL query."""
        return await self._call("GET", "/query", params, trace_id)

    @nw_action("query_more", http_method="GET", http_path="/query/{locator}")
    async def query_more(self, params: QueryMoreInput, *, trace_id: str) -> RestResponseOutput:
        """Get the next page of a SOQL query."""
        return await self._call("GET", "/query/{locator}", params, trace_id)

    @nw_action("query_all", http_method="GET", http_path="/queryAll")
    async def query_all(self, params: QueryAllInput, *, trace_id: str) -> RestResponseOutput:
        """Run a SOQL query that includes deleted and archived records."""
        return await self._call("GET", "/queryAll", params, trace_id)

    @nw_action("query_all_more", http_method="GET", http_path="/queryAll/{locator}")
    async def query_all_more(
        self, params: QueryAllMoreInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the next page of a queryAll."""
        return await self._call("GET", "/queryAll/{locator}", params, trace_id)

    @nw_action("search", http_method="GET", http_path="/search")
    async def search(self, params: SearchInput, *, trace_id: str) -> RestResponseOutput:
        """Run a SOSL search."""
        return await self._call("GET", "/search", params, trace_id)

    @nw_action("parameterized_search_get", http_method="GET", http_path="/parameterizedSearch")
    async def parameterized_search_get(
        self, params: ParameterizedSearchGetInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Search with simple parameters instead of SOSL."""
        return await self._call("GET", "/parameterizedSearch", params, trace_id)

    @nw_action("parameterized_search_post", http_method="POST", http_path="/parameterizedSearch")
    async def parameterized_search_post(
        self, params: ParameterizedSearchPostInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Search with a request body for complex searches (several objects, filters, data categories)."""
        return await self._call("POST", "/parameterizedSearch", params, trace_id)

    @nw_action("get_search_scope_order", http_method="GET", http_path="/search/scopeOrder")
    async def get_search_scope_order(
        self, params: GetSearchScopeOrderInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the objects in the user's search scope and order."""
        return await self._call("GET", "/search/scopeOrder", params, trace_id)

    @nw_action("get_search_result_layouts", http_method="GET", http_path="/search/layout")
    async def get_search_result_layouts(
        self, params: GetSearchResultLayoutsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the search result layouts of objects."""
        return await self._call("GET", "/search/layout", params, trace_id)

    @nw_action("search_suggestions", http_method="GET", http_path="/search/suggestions")
    async def search_suggestions(
        self, params: SearchSuggestionsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get record suggestions for a search string (autocomplete)."""
        return await self._call("GET", "/search/suggestions", params, trace_id)

    @nw_action("search_title_matches", http_method="GET", http_path="/search/suggestTitleMatches")
    async def search_title_matches(
        self, params: SearchTitleMatchesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get knowledge article titles that match a search string."""
        return await self._call("GET", "/search/suggestTitleMatches", params, trace_id)

    @nw_action(
        "search_suggested_queries", http_method="GET", http_path="/search/suggestSearchQueries"
    )
    async def search_suggested_queries(
        self, params: SearchSuggestedQueriesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get suggested search queries for a search string."""
        return await self._call("GET", "/search/suggestSearchQueries", params, trace_id)

    @nw_action("run_named_query", http_method="GET", http_path="/named/query/{query_name}")
    async def run_named_query(
        self, params: RunNamedQueryInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Run a named query API (a SOQL query saved in Setup)."""
        return await self._call("GET", "/named/query/{query_name}", params, trace_id)

    @nw_action("list_list_views", http_method="GET", http_path="/sobjects/{sobject}/listviews")
    async def list_list_views(
        self, params: ListListViewsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the list views of an object."""
        return await self._call("GET", "/sobjects/{sobject}/listviews", params, trace_id)

    @nw_action(
        "get_list_view", http_method="GET", http_path="/sobjects/{sobject}/listviews/{list_view_id}"
    )
    async def get_list_view(self, params: GetListViewInput, *, trace_id: str) -> RestResponseOutput:
        """Get the basic information of a list view."""
        return await self._call(
            "GET", "/sobjects/{sobject}/listviews/{list_view_id}", params, trace_id
        )

    @nw_action(
        "describe_list_view",
        http_method="GET",
        http_path="/sobjects/{sobject}/listviews/{list_view_id}/describe",
    )
    async def describe_list_view(
        self, params: DescribeListViewInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Describe a list view: columns, filters, sort."""
        return await self._call(
            "GET", "/sobjects/{sobject}/listviews/{list_view_id}/describe", params, trace_id
        )

    @nw_action(
        "get_list_view_results",
        http_method="GET",
        http_path="/sobjects/{sobject}/listviews/{list_view_id}/results",
    )
    async def get_list_view_results(
        self, params: GetListViewResultsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Run a list view and return its records."""
        return await self._call(
            "GET", "/sobjects/{sobject}/listviews/{list_view_id}/results", params, trace_id
        )

    @nw_action(
        "get_recent_list_views", http_method="GET", http_path="/sobjects/{sobject}/listviews/recent"
    )
    async def get_recent_list_views(
        self, params: GetRecentListViewsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the list views the user used recently for an object."""
        return await self._call("GET", "/sobjects/{sobject}/listviews/recent", params, trace_id)

    @nw_action("get_recent_items", http_method="GET", http_path="/recent")
    async def get_recent_items(
        self, params: GetRecentItemsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the items the user viewed or referenced recently."""
        return await self._call("GET", "/recent", params, trace_id)

    @nw_action("get_relevant_items", http_method="GET", http_path="/sobjects/relevantItems")
    async def get_relevant_items(
        self, params: GetRelevantItemsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the items most relevant to the user."""
        return await self._call("GET", "/sobjects/relevantItems", params, trace_id)

    @nw_action("list_quick_actions", http_method="GET", http_path="/quickActions")
    async def list_quick_actions(
        self, params: ListQuickActionsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the global quick actions."""
        return await self._call("GET", "/quickActions", params, trace_id)

    @nw_action(
        "create_record_with_quick_action",
        http_method="POST",
        http_path="/quickActions/{action_name}",
    )
    async def create_record_with_quick_action(
        self, params: CreateRecordWithQuickActionInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Run a global quick action that creates a record."""
        return await self._call("POST", "/quickActions/{action_name}", params, trace_id)

    @nw_action(
        "list_sobject_quick_actions",
        http_method="GET",
        http_path="/sobjects/{sobject}/quickActions",
    )
    async def list_sobject_quick_actions(
        self, params: ListSobjectQuickActionsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the quick actions of an object."""
        return await self._call("GET", "/sobjects/{sobject}/quickActions", params, trace_id)

    @nw_action(
        "get_sobject_quick_action",
        http_method="GET",
        http_path="/sobjects/{sobject}/quickActions/{action_name}",
    )
    async def get_sobject_quick_action(
        self, params: GetSobjectQuickActionInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get a quick action of an object."""
        return await self._call(
            "GET", "/sobjects/{sobject}/quickActions/{action_name}", params, trace_id
        )

    @nw_action(
        "invoke_sobject_quick_action",
        http_method="POST",
        http_path="/sobjects/{sobject}/quickActions/{action_name}",
    )
    async def invoke_sobject_quick_action(
        self, params: InvokeSobjectQuickActionInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Run a quick action of an object."""
        return await self._call(
            "POST", "/sobjects/{sobject}/quickActions/{action_name}", params, trace_id
        )

    @nw_action(
        "describe_sobject_quick_action",
        http_method="GET",
        http_path="/sobjects/{sobject}/quickActions/{action_name}/describe",
    )
    async def describe_sobject_quick_action(
        self, params: DescribeSobjectQuickActionInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Describe a quick action of an object."""
        return await self._call(
            "GET", "/sobjects/{sobject}/quickActions/{action_name}/describe", params, trace_id
        )

    @nw_action(
        "get_quick_action_default_values",
        http_method="GET",
        http_path="/sobjects/{sobject}/quickActions/{action_name}/defaultValues",
    )
    async def get_quick_action_default_values(
        self, params: GetQuickActionDefaultValuesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the default values of a quick action."""
        return await self._call(
            "GET", "/sobjects/{sobject}/quickActions/{action_name}/defaultValues", params, trace_id
        )

    @nw_action(
        "get_quick_action_default_values_for_record",
        http_method="GET",
        http_path="/sobjects/{sobject}/quickActions/{action_name}/defaultValues/{context_id}",
    )
    async def get_quick_action_default_values_for_record(
        self, params: GetQuickActionDefaultValuesForRecordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the default values of a quick action for a record."""
        return await self._call(
            "GET",
            "/sobjects/{sobject}/quickActions/{action_name}/defaultValues/{context_id}",
            params,
            trace_id,
        )

    @nw_action("list_process_approvals", http_method="GET", http_path="/process/approvals")
    async def list_process_approvals(
        self, params: ListProcessApprovalsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the approval processes and pending approvals."""
        return await self._call("GET", "/process/approvals", params, trace_id)

    @nw_action("submit_process_approval", http_method="POST", http_path="/process/approvals")
    async def submit_process_approval(
        self, params: SubmitProcessApprovalInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Submit, approve or reject records in approval processes."""
        return await self._call("POST", "/process/approvals", params, trace_id)

    @nw_action("list_process_rules", http_method="GET", http_path="/process/rules")
    async def list_process_rules(
        self, params: ListProcessRulesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the workflow rules of all objects."""
        return await self._call("GET", "/process/rules", params, trace_id)

    @nw_action("trigger_process_rules", http_method="POST", http_path="/process/rules")
    async def trigger_process_rules(
        self, params: TriggerProcessRulesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Trigger the workflow rules of records."""
        return await self._call("POST", "/process/rules", params, trace_id)

    @nw_action(
        "list_process_rules_for_sobject", http_method="GET", http_path="/process/rules/{sobject}"
    )
    async def list_process_rules_for_sobject(
        self, params: ListProcessRulesForSobjectInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the workflow rules of an object."""
        return await self._call("GET", "/process/rules/{sobject}", params, trace_id)

    @nw_action(
        "get_process_rule", http_method="GET", http_path="/process/rules/{sobject}/{rule_id}"
    )
    async def get_process_rule(
        self, params: GetProcessRuleInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get a workflow rule of an object."""
        return await self._call("GET", "/process/rules/{sobject}/{rule_id}", params, trace_id)

    @nw_action(
        "trigger_process_rule", http_method="POST", http_path="/process/rules/{sobject}/{rule_id}"
    )
    async def trigger_process_rule(
        self, params: TriggerProcessRuleInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Trigger a workflow rule for a record."""
        return await self._call("POST", "/process/rules/{sobject}/{rule_id}", params, trace_id)

    @nw_action("list_invocable_actions", http_method="GET", http_path="/actions")
    async def list_invocable_actions(
        self, params: ListInvocableActionsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the types of invocable actions."""
        return await self._call("GET", "/actions", params, trace_id)

    @nw_action("list_standard_invocable_actions", http_method="GET", http_path="/actions/standard")
    async def list_standard_invocable_actions(
        self, params: ListStandardInvocableActionsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the standard invocable actions."""
        return await self._call("GET", "/actions/standard", params, trace_id)

    @nw_action("list_custom_invocable_actions", http_method="GET", http_path="/actions/custom")
    async def list_custom_invocable_actions(
        self, params: ListCustomInvocableActionsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the custom invocable actions."""
        return await self._call("GET", "/actions/custom", params, trace_id)

    @nw_action(
        "deploy_data_kit_components",
        http_method="POST",
        http_path="/actions/custom/flow/sfdatakit__DeployDataKitComponents",
    )
    async def deploy_data_kit_components(
        self, params: DeployDataKitComponentsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Deploy data kit components with the Deploy Data Kit Components flow."""
        return await self._call(
            "POST", "/actions/custom/flow/sfdatakit__DeployDataKitComponents", params, trace_id
        )

    @nw_action(
        "get_product_schedules",
        http_method="GET",
        http_path="/sobjects/OpportunityLineItem/{record_id}/OpportunityLineItemSchedules",
    )
    async def get_product_schedules(
        self, params: GetProductSchedulesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the schedules of an opportunity product."""
        return await self._call(
            "GET",
            "/sobjects/OpportunityLineItem/{record_id}/OpportunityLineItemSchedules",
            params,
            trace_id,
        )

    @nw_action(
        "create_product_schedules",
        http_method="PUT",
        http_path="/sobjects/OpportunityLineItem/{record_id}/OpportunityLineItemSchedules",
    )
    async def create_product_schedules(
        self, params: CreateProductSchedulesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Establish or re-establish the schedules of an opportunity product."""
        return await self._call(
            "PUT",
            "/sobjects/OpportunityLineItem/{record_id}/OpportunityLineItemSchedules",
            params,
            trace_id,
        )

    @nw_action(
        "delete_product_schedules",
        http_method="DELETE",
        http_path="/sobjects/OpportunityLineItem/{record_id}/OpportunityLineItemSchedules",
    )
    async def delete_product_schedules(
        self, params: DeleteProductSchedulesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Delete the schedules of an opportunity product."""
        return await self._call(
            "DELETE",
            "/sobjects/OpportunityLineItem/{record_id}/OpportunityLineItemSchedules",
            params,
            trace_id,
        )

    @nw_action("get_support_resources", http_method="GET", http_path="/support")
    async def get_support_resources(
        self, params: GetSupportResourcesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the support resources."""
        return await self._call("GET", "/support", params, trace_id)

    @nw_action(
        "list_data_category_groups", http_method="GET", http_path="/support/dataCategoryGroups"
    )
    async def list_data_category_groups(
        self, params: ListDataCategoryGroupsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the data category groups."""
        return await self._call("GET", "/support/dataCategoryGroups", params, trace_id)

    @nw_action(
        "get_data_category_detail",
        http_method="GET",
        http_path="/support/dataCategoryGroups/{group}/dataCategories/{category}",
    )
    async def get_data_category_detail(
        self, params: GetDataCategoryDetailInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get a data category and its children."""
        return await self._call(
            "GET", "/support/dataCategoryGroups/{group}/dataCategories/{category}", params, trace_id
        )

    @nw_action("list_knowledge_articles", http_method="GET", http_path="/support/knowledgeArticles")
    async def list_knowledge_articles(
        self, params: ListKnowledgeArticlesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List or search knowledge articles."""
        return await self._call("GET", "/support/knowledgeArticles", params, trace_id)

    @nw_action(
        "get_knowledge_article",
        http_method="GET",
        http_path="/support/knowledgeArticles/{article_id_or_url_name}",
    )
    async def get_knowledge_article(
        self, params: GetKnowledgeArticleInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get a knowledge article by Id or URL name."""
        return await self._call(
            "GET", "/support/knowledgeArticles/{article_id_or_url_name}", params, trace_id
        )

    @nw_action(
        "get_knowledge_language_settings",
        http_method="GET",
        http_path="/knowledgeManagement/settings",
    )
    async def get_knowledge_language_settings(
        self, params: GetKnowledgeLanguageSettingsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the knowledge language settings."""
        return await self._call("GET", "/knowledgeManagement/settings", params, trace_id)

    @nw_action(
        "get_suggested_articles",
        http_method="GET",
        http_path="/sobjects/{sobject}/suggestedArticles",
    )
    async def get_suggested_articles(
        self, params: GetSuggestedArticlesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get articles suggested for a new record of an object."""
        return await self._call("GET", "/sobjects/{sobject}/suggestedArticles", params, trace_id)

    @nw_action(
        "get_suggested_articles_for_record",
        http_method="GET",
        http_path="/sobjects/{sobject}/{record_id}/suggestedArticles",
    )
    async def get_suggested_articles_for_record(
        self, params: GetSuggestedArticlesForRecordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get articles suggested for a record."""
        return await self._call(
            "GET", "/sobjects/{sobject}/{record_id}/suggestedArticles", params, trace_id
        )

    @nw_action(
        "get_embedded_service_config",
        http_method="GET",
        http_path="/support/embeddedservice/configuration/{config_name}",
    )
    async def get_embedded_service_config(
        self, params: GetEmbeddedServiceConfigInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get an Embedded Service configuration."""
        return await self._call(
            "GET", "/support/embeddedservice/configuration/{config_name}", params, trace_id
        )

    @nw_action(
        "get_event_schema_by_name",
        http_method="GET",
        http_path="/sobjects/{event_name}/eventSchema",
    )
    async def get_event_schema_by_name(
        self, params: GetEventSchemaByNameInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the schema of a platform event by its name."""
        return await self._call("GET", "/sobjects/{event_name}/eventSchema", params, trace_id)

    @nw_action(
        "get_event_schema_by_id", http_method="GET", http_path="/event/eventSchema/{schema_id}"
    )
    async def get_event_schema_by_id(
        self, params: GetEventSchemaByIdInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the schema of a platform event by schema Id."""
        return await self._call("GET", "/event/eventSchema/{schema_id}", params, trace_id)

    @nw_action("compile_consent", http_method="GET", http_path="/consent/action/{consent_action}")
    async def compile_consent(
        self, params: CompileConsentInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Compile the consent settings of records for one action."""
        return await self._call("GET", "/consent/action/{consent_action}", params, trace_id)

    @nw_action("compile_multi_consent", http_method="GET", http_path="/consent/multiaction")
    async def compile_multi_consent(
        self, params: CompileMultiConsentInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Compile the consent settings of records for several actions."""
        return await self._call("GET", "/consent/multiaction", params, trace_id)

    @nw_action("write_consent", http_method="PATCH", http_path="/consent/action/{consent_action}")
    async def write_consent(
        self, params: WriteConsentInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Write consent settings for records."""
        return await self._call("PATCH", "/consent/action/{consent_action}", params, trace_id)

    @nw_action("request_portability_data", http_method="POST", http_path="/consent/dsr/rtp/execute")
    async def request_portability_data(
        self, params: RequestPortabilityDataInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Start compiling the data of a data subject for a portability request."""
        return await self._call("POST", "/consent/dsr/rtp/execute", params, trace_id)

    @nw_action("get_portability_status", http_method="GET", http_path="/consent/dsr/rtp/execute")
    async def get_portability_status(
        self, params: GetPortabilityStatusInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the status of a portability request."""
        return await self._call("GET", "/consent/dsr/rtp/execute", params, trace_id)

    @nw_action("get_scheduling_resources", http_method="GET", http_path="/scheduling")
    async def get_scheduling_resources(
        self, params: GetSchedulingResourcesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the Salesforce Scheduler resources."""
        return await self._call("GET", "/scheduling", params, trace_id)

    @nw_action(
        "get_appointment_slots", http_method="POST", http_path="/scheduling/getAppointmentSlots"
    )
    async def get_appointment_slots(
        self, params: GetAppointmentSlotsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the appointment slots for a work type and territories."""
        return await self._call("POST", "/scheduling/getAppointmentSlots", params, trace_id)

    @nw_action(
        "get_appointment_candidates",
        http_method="POST",
        http_path="/scheduling/getAppointmentCandidates",
    )
    async def get_appointment_candidates(
        self, params: GetAppointmentCandidatesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the service resources available for appointments."""
        return await self._call("POST", "/scheduling/getAppointmentCandidates", params, trace_id)

    @nw_action(
        "set_survey_field_translation",
        http_method="POST",
        http_path="/localizedvalue/record/{developer_name}/{language}",
    )
    async def set_survey_field_translation(
        self, params: SetSurveyFieldTranslationInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Add or change the translation of a survey field."""
        return await self._call(
            "POST", "/localizedvalue/record/{developer_name}/{language}", params, trace_id
        )

    @nw_action(
        "set_survey_field_translations",
        http_method="POST",
        http_path="/localizedvalue/records/upsert",
    )
    async def set_survey_field_translations(
        self, params: SetSurveyFieldTranslationsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Add or change the translations of several survey fields."""
        return await self._call("POST", "/localizedvalue/records/upsert", params, trace_id)

    @nw_action(
        "delete_survey_field_translation",
        http_method="DELETE",
        http_path="/localizedvalue/record/{developer_name}/{language}",
    )
    async def delete_survey_field_translation(
        self, params: DeleteSurveyFieldTranslationInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Delete the translation of a survey field."""
        return await self._call(
            "DELETE", "/localizedvalue/record/{developer_name}/{language}", params, trace_id
        )

    @nw_action(
        "delete_survey_field_translations",
        http_method="POST",
        http_path="/localizedvalue/records/delete",
    )
    async def delete_survey_field_translations(
        self, params: DeleteSurveyFieldTranslationsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Delete the translations of several survey fields."""
        return await self._call("POST", "/localizedvalue/records/delete", params, trace_id)

    @nw_action(
        "get_survey_field_translation",
        http_method="GET",
        http_path="/localizedvalue/record/{developer_name}/{language}",
    )
    async def get_survey_field_translation(
        self, params: GetSurveyFieldTranslationInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the translation of a survey field."""
        return await self._call(
            "GET", "/localizedvalue/record/{developer_name}/{language}", params, trace_id
        )

    @nw_action(
        "get_survey_field_translations", http_method="POST", http_path="/localizedvalue/records/get"
    )
    async def get_survey_field_translations(
        self, params: GetSurveyFieldTranslationsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the translations of several survey fields."""
        return await self._call("POST", "/localizedvalue/records/get", params, trace_id)

    @nw_action(
        "get_user_password_status", http_method="GET", http_path="/sobjects/User/{user_id}/password"
    )
    async def get_user_password_status(
        self, params: GetUserPasswordStatusInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get whether the password of a user is expired."""
        return await self._call("GET", "/sobjects/User/{user_id}/password", params, trace_id)

    @nw_action(
        "set_user_password", http_method="POST", http_path="/sobjects/User/{user_id}/password"
    )
    async def set_user_password(
        self, params: SetUserPasswordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Set the password of a user."""
        return await self._call("POST", "/sobjects/User/{user_id}/password", params, trace_id)

    @nw_action(
        "reset_user_password", http_method="DELETE", http_path="/sobjects/User/{user_id}/password"
    )
    async def reset_user_password(
        self, params: ResetUserPasswordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Reset the password of a user. The response holds the new password."""
        return await self._call("DELETE", "/sobjects/User/{user_id}/password", params, trace_id)

    @nw_action(
        "get_self_service_user_password_status",
        http_method="GET",
        http_path="/sobjects/SelfServiceUser/{self_service_user_id}/password",
    )
    async def get_self_service_user_password_status(
        self, params: GetSelfServiceUserPasswordStatusInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get whether the password of a self-service user is expired."""
        return await self._call(
            "GET", "/sobjects/SelfServiceUser/{self_service_user_id}/password", params, trace_id
        )

    @nw_action(
        "set_self_service_user_password",
        http_method="POST",
        http_path="/sobjects/SelfServiceUser/{self_service_user_id}/password",
    )
    async def set_self_service_user_password(
        self, params: SetSelfServiceUserPasswordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Set the password of a self-service user."""
        return await self._call(
            "POST", "/sobjects/SelfServiceUser/{self_service_user_id}/password", params, trace_id
        )

    @nw_action(
        "reset_self_service_user_password",
        http_method="DELETE",
        http_path="/sobjects/SelfServiceUser/{self_service_user_id}/password",
    )
    async def reset_self_service_user_password(
        self, params: ResetSelfServiceUserPasswordInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Reset the password of a self-service user. The response holds the new password."""
        return await self._call(
            "DELETE", "/sobjects/SelfServiceUser/{self_service_user_id}/password", params, trace_id
        )

    @nw_action(
        "get_lightning_toggle_metrics",
        http_method="GET",
        http_path="/sobjects/LightningToggleMetrics",
    )
    async def get_lightning_toggle_metrics(
        self, params: GetLightningToggleMetricsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get Lightning Experience toggle metrics."""
        return await self._call("GET", "/sobjects/LightningToggleMetrics", params, trace_id)

    @nw_action(
        "get_lightning_usage_by_app_type",
        http_method="GET",
        http_path="/sobjects/LightningUsageByAppTypeMetrics",
    )
    async def get_lightning_usage_by_app_type(
        self, params: GetLightningUsageByAppTypeInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get Lightning usage grouped by app type."""
        return await self._call("GET", "/sobjects/LightningUsageByAppTypeMetrics", params, trace_id)

    @nw_action(
        "get_lightning_usage_by_browser",
        http_method="GET",
        http_path="/sobjects/LightningUsageByBrowserMetrics",
    )
    async def get_lightning_usage_by_browser(
        self, params: GetLightningUsageByBrowserInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get Lightning usage grouped by browser."""
        return await self._call("GET", "/sobjects/LightningUsageByBrowserMetrics", params, trace_id)

    @nw_action(
        "get_lightning_usage_by_page",
        http_method="GET",
        http_path="/sobjects/LightningUsageByPageMetrics",
    )
    async def get_lightning_usage_by_page(
        self, params: GetLightningUsageByPageInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get Lightning usage grouped by page."""
        return await self._call("GET", "/sobjects/LightningUsageByPageMetrics", params, trace_id)

    @nw_action(
        "get_lightning_usage_by_flexipage",
        http_method="GET",
        http_path="/sobjects/LightningUsageByFlexiPageMetrics",
    )
    async def get_lightning_usage_by_flexipage(
        self, params: GetLightningUsageByFlexipageInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get Lightning usage grouped by Lightning page."""
        return await self._call(
            "GET", "/sobjects/LightningUsageByFlexiPageMetrics", params, trace_id
        )

    @nw_action(
        "get_lightning_exit_by_page",
        http_method="GET",
        http_path="/sobjects/LightningExitByPageMetrics",
    )
    async def get_lightning_exit_by_page(
        self, params: GetLightningExitByPageInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the pages users exit Lightning Experience from."""
        return await self._call("GET", "/sobjects/LightningExitByPageMetrics", params, trace_id)

    @nw_action("create_ingest_job", http_method="POST", http_path="/jobs/ingest")
    async def create_ingest_job(
        self, params: CreateIngestJobInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Create a Bulk API 2.0 ingest job (insert, update, upsert, delete)."""
        return await self._call("POST", "/jobs/ingest", params, trace_id)

    @nw_action(
        "upload_ingest_job_data", http_method="PUT", http_path="/jobs/ingest/{job_id}/batches"
    )
    async def upload_ingest_job_data(
        self, params: UploadIngestJobDataInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Upload the CSV data of an ingest job."""
        return await self._call("PUT", "/jobs/ingest/{job_id}/batches", params, trace_id)

    @nw_action("complete_ingest_job", http_method="PATCH", http_path="/jobs/ingest/{job_id}")
    async def complete_ingest_job(
        self, params: CompleteIngestJobInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Tell Salesforce the upload is done, so the job starts processing."""
        return await self._call(
            "PATCH", "/jobs/ingest/{job_id}", params, trace_id, body={"state": "UploadComplete"}
        )

    @nw_action("abort_ingest_job", http_method="PATCH", http_path="/jobs/ingest/{job_id}")
    async def abort_ingest_job(
        self, params: AbortIngestJobInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Abort an ingest job."""
        return await self._call(
            "PATCH", "/jobs/ingest/{job_id}", params, trace_id, body={"state": "Aborted"}
        )

    @nw_action("delete_ingest_job", http_method="DELETE", http_path="/jobs/ingest/{job_id}")
    async def delete_ingest_job(
        self, params: DeleteIngestJobInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Delete an ingest job and its data."""
        return await self._call("DELETE", "/jobs/ingest/{job_id}", params, trace_id)

    @nw_action("get_ingest_job", http_method="GET", http_path="/jobs/ingest/{job_id}")
    async def get_ingest_job(
        self, params: GetIngestJobInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the status of an ingest job."""
        return await self._call("GET", "/jobs/ingest/{job_id}", params, trace_id)

    @nw_action("list_ingest_jobs", http_method="GET", http_path="/jobs/ingest")
    async def list_ingest_jobs(
        self, params: ListIngestJobsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the ingest jobs."""
        return await self._call("GET", "/jobs/ingest", params, trace_id)

    @nw_action(
        "get_ingest_successful_results",
        http_method="GET",
        http_path="/jobs/ingest/{job_id}/successfulResults/",
    )
    async def get_ingest_successful_results(
        self, params: GetIngestSuccessfulResultsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the records an ingest job processed successfully, as CSV."""
        return await self._call(
            "GET", "/jobs/ingest/{job_id}/successfulResults/", params, trace_id, accept="text/csv"
        )

    @nw_action(
        "get_ingest_failed_results",
        http_method="GET",
        http_path="/jobs/ingest/{job_id}/failedResults/",
    )
    async def get_ingest_failed_results(
        self, params: GetIngestFailedResultsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the records an ingest job failed to process, as CSV."""
        return await self._call(
            "GET", "/jobs/ingest/{job_id}/failedResults/", params, trace_id, accept="text/csv"
        )

    @nw_action(
        "get_ingest_unprocessed_records",
        http_method="GET",
        http_path="/jobs/ingest/{job_id}/unprocessedrecords/",
    )
    async def get_ingest_unprocessed_records(
        self, params: GetIngestUnprocessedRecordsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the records an ingest job did not process, as CSV."""
        return await self._call(
            "GET", "/jobs/ingest/{job_id}/unprocessedrecords/", params, trace_id, accept="text/csv"
        )

    @nw_action("create_query_job", http_method="POST", http_path="/jobs/query")
    async def create_query_job(
        self, params: CreateQueryJobInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Create a Bulk API 2.0 query job."""
        return await self._call("POST", "/jobs/query", params, trace_id)

    @nw_action("get_query_job", http_method="GET", http_path="/jobs/query/{job_id}")
    async def get_query_job(self, params: GetQueryJobInput, *, trace_id: str) -> RestResponseOutput:
        """Get the status of a query job."""
        return await self._call("GET", "/jobs/query/{job_id}", params, trace_id)

    @nw_action("abort_query_job", http_method="PATCH", http_path="/jobs/query/{job_id}")
    async def abort_query_job(
        self, params: AbortQueryJobInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Abort a query job."""
        return await self._call(
            "PATCH", "/jobs/query/{job_id}", params, trace_id, body={"state": "Aborted"}
        )

    @nw_action("delete_query_job", http_method="DELETE", http_path="/jobs/query/{job_id}")
    async def delete_query_job(
        self, params: DeleteQueryJobInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Delete a query job and its results."""
        return await self._call("DELETE", "/jobs/query/{job_id}", params, trace_id)

    @nw_action("list_query_jobs", http_method="GET", http_path="/jobs/query")
    async def list_query_jobs(
        self, params: ListQueryJobsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """List the query jobs."""
        return await self._call("GET", "/jobs/query", params, trace_id)

    @nw_action("get_query_job_results", http_method="GET", http_path="/jobs/query/{job_id}/results")
    async def get_query_job_results(
        self, params: GetQueryJobResultsInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the results of a query job, as CSV."""
        return await self._call(
            "GET", "/jobs/query/{job_id}/results", params, trace_id, accept="text/csv"
        )

    @nw_action(
        "get_query_job_result_pages",
        http_method="GET",
        http_path="/jobs/query/{job_id}/resultPages",
    )
    async def get_query_job_result_pages(
        self, params: GetQueryJobResultPagesInput, *, trace_id: str
    ) -> RestResponseOutput:
        """Get the result pages of a query job, to download in parallel."""
        return await self._call("GET", "/jobs/query/{job_id}/resultPages", params, trace_id)
